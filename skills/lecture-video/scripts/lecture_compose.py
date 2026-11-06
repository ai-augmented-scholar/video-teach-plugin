#!/usr/bin/env python3
"""Cut a lecture between setting A (talking head) and setting B (slides + PiP).

Usage:
    python3 lecture_compose.py --video SRC.mp4 --work DIR --timing DIR/timing.json \
        --out OUT.mp4 [--set key=value ...]
    python3 lecture_compose.py ... --preview [--at 75,190] [--guides]
                                              stills only, no render

DIR is the folder slides_prepare.py wrote (slides/ and slides.json). timing.json
holds the segments; every other number lives in DEFAULTS below and can be set
in timing.json (same key) or on the command line with --set key=value.

Setting A  The talking head alone, full frame. FRAMING RULE: the eyes
           stand on the upper-third line (1/3 of the height from the top) and AT
           MINIMUM both shoulders are in frame, with a little chest below them; the
           face never fills the height. The zoom is whatever places the speaker at
           `a_target_x` (a little right of centre, the left 46% free for text), but it
           never goes past what the rule allows and never past `a_max_zoom`. When
           the rule and the free left 46% disagree, the rule wins and the preview says so.
           The rule needs the speaker's landmarks, measured from a still of the SOURCE
           (use --source-grid): speaker_eye_y, speaker_shoulder_y, and the shoulders' span
           speaker_shoulder_x0/x1, all as fractions of the source frame.
Setting B  The background plate (a neutral charcoal gradient by default; the user's own
           image from config.json slides.background, or --set background=...). The slide
           nearly fills the frame (gaps: 5% top, left and bottom, 12% right; its size and
           place never change). The speaker is a vertical crop on the right, 43.5% of the
           frame height, its top just above the horizontal midline, 2% of the frame width
           clear of the right edge, and it overlaps the slide's right edge. The slide and the insert
           have rounded corners and cast a drop shadow.

segments in timing.json:
    {"start": 0,    "setting": "A"}
    {"start": 41.2, "setting": "B", "slide": 1}
    {"start": 96.0, "setting": "B", "slide": 2}      a slide change inside B
Each runs until the next one starts. Times are on the clock of --video.

How it renders, in one ffmpeg pass. The speaker's video is cropped twice
(the A crop and the B insert). Every B segment has a still plate (background,
shadows, slide) and every A segment a transparent plate; the plates play back
to back for exact frame counts and sit over the A crop, and the insert switches
on during B by frame number. The sound is copied from the source, so it stays
in sync by construction. Output: 1920x1080 at the source's frame rate.

Writes OUT's neighbour settings.json (the A/B timeline in frames and seconds).
map_settings.py reads it to find the setting of every moment after perfect-cuts.
Stdlib only.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path

W, H = 1920, 1080
SKILL_DIR = Path(__file__).resolve().parents[1]

DEFAULTS = {
    # where the speaker is in the SOURCE frame: centre, as a fraction of width
    "speaker_x": 0.5,
    # setting A: the framing rule needs these four landmarks (fractions of the SOURCE frame)
    "speaker_eye_y": None,         # eyes, from the top of the frame
    "speaker_shoulder_y": None,    # the line where the shoulders meet the arms
    "speaker_shoulder_x0": None,   # left end of the shoulders (optional)
    "speaker_shoulder_x1": None,   # right end of the shoulders (optional)
    "a_target_x": 0.62,       # where the speaker's centre should land in the A frame
    "a_free_left": 0.46,      # the part of the frame left empty for text (guide, and a warning)
    "a_eye_line": 1 / 3.0,    # eyes on this line, from the top of the A frame
    "a_shoulder_margin": 0.07,  # chest below the shoulder line, fraction of the source height
    "a_max_zoom": 1.5,        # never closer than this, whatever the placement wants
    # setting B: gaps around the slide, as fractions of the frame (the slide never moves)
    "gap_top": 0.05, "gap_left": 0.05, "gap_bottom": 0.05, "gap_right": 0.12,
    # setting B: the speaker insert (it overlaps the slide's right edge)
    "ins_h": 0.435,           # height, fraction of frame height
    "ins_aspect": 9 / 16.0,   # width / height of the vertical view
    "ins_margin_right": 0.02, # background left clear right of the insert
    # "left" mirrors setting B for a speaker in profile who faces left to right:
    # the insert on the left; swap gap_left/gap_right with it
    "ins_side": "right",
    "ins_midline_overlap": 0.02,  # the insert's top sits this far above the midline
    "ins_zoom": 1.4,          # 1 = the source full height; 1.4 = head and shoulders
    "ins_crop_y": 0.5,
    "ins_speaker_x": None,    # centre of the insert crop; default speaker_x
    "corner_radius": 24,      # px, slide and insert in setting B
    # drop shadow
    "shadow_blur": 22, "shadow_alpha": 0.5, "shadow_dx": 5, "shadow_dy": 9,
    "background": None,       # None: config.json slides.background, else assets/background-plate.png
}
_FRAMING_WARNINGS = []
MIN_A, MIN_B = 4.0, 6.0   # seconds; a shorter run only draws a warning

_path = os.environ.get("PATH", "").split(os.pathsep)
os.environ["PATH"] = os.pathsep.join(
    _path + [p for p in ("/opt/homebrew/bin", "/usr/local/bin") if p not in _path])


def need(tool):
    p = shutil.which(tool)
    if not p:
        sys.exit("ERROR: %s is not installed (brew install ffmpeg)." % tool)
    return p


FFMPEG, FFPROBE = need("ffmpeg"), need("ffprobe")


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        sys.stderr.write(r.stderr.decode("utf-8", "replace")[-3000:])
        sys.exit("ERROR: ffmpeg failed (%s)" % " ".join(cmd[:3]))
    return r.stdout.decode("utf-8", "replace")


def even(x):
    return int(round(x / 2.0)) * 2


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ---------------------------------------------------------------- probing

def probe(video):
    out = json.loads(run([FFPROBE, "-v", "error", "-print_format", "json",
                          "-show_streams", "-show_format", str(video)]))
    v = next((s for s in out["streams"] if s["codec_type"] == "video"), None)
    if v is None:
        sys.exit("ERROR: %s has no video stream." % video)
    a = next((s for s in out["streams"] if s["codec_type"] == "audio"), None)
    # The nominal rate is the one the footage was shot at. The average drifts
    # on edited files (a 60 fps cut reported 59.953), so it is the fallback.
    std = (Fraction(24000, 1001), Fraction(24), Fraction(25), Fraction(30000, 1001),
           Fraction(30), Fraction(50), Fraction(60000, 1001), Fraction(60))
    fps = None
    for key in ("r_frame_rate", "avg_frame_rate"):
        rate = v.get(key) or "0/0"
        if rate.startswith("0"):
            continue
        cand = Fraction(rate)
        near = min(std, key=lambda s: abs(float(s) - float(cand)))
        if abs(float(near) - float(cand)) < (0.001 if key == "r_frame_rate" else 0.02):
            fps = near
            break
        if key == "avg_frame_rate" and 10 <= float(cand) <= 120:
            fps = cand
    if fps is None:
        fps = Fraction(30)
    dur = float(v.get("duration") or out["format"]["duration"])
    w, h = int(v["width"]), int(v["height"])
    for sd in v.get("side_data_list", []):
        if abs(int(sd.get("rotation", 0))) in (90, 270):
            w, h = h, w
    return {"w": w, "h": h, "fps": fps, "duration": dur,
            "audio": a["codec_name"] if a else None}


# ---------------------------------------------------------------- geometry

def fit(aspect, max_w, max_h):
    w = max_w
    h = w / aspect
    if h > max_h:
        h = max_h
        w = h * aspect
    return even(w), even(h)


def geometry(aspect, cfg):
    """Slide rectangle and insert rectangle (x, y, w, h) on the 1920x1080 frame."""
    bx = int(round(W * cfg["gap_left"]))
    by = int(round(H * cfg["gap_top"]))
    bw = int(round(W * (1 - cfg["gap_left"] - cfg["gap_right"])))
    bh = int(round(H * (1 - cfg["gap_top"] - cfg["gap_bottom"])))
    sw, sh = fit(aspect, bw, bh)
    # the slide is centred in the gap box; a 16:9 slide is width-limited there
    slide = (bx + (bw - sw) // 2, by + (bh - sh) // 2, sw, sh)
    ih = even(H * cfg["ins_h"])
    iw = even(ih * cfg["ins_aspect"])
    if cfg["ins_side"] == "left":   # mirrored: the margin is kept clear left of the insert
        ix = int(round(W * cfg["ins_margin_right"]))
    else:
        ix = W - int(round(W * cfg["ins_margin_right"])) - iw
    iy = int(round(H / 2.0 - H * cfg["ins_midline_overlap"]))
    return slide, (ix, iy, iw, ih)


def a_crop(src_w, src_h, cfg):
    """The part of the source that becomes the A frame, and a report on how it came out.

    FRAMING RULE: eyes on the a_eye_line (1/3 from the top), both shoulders in frame
    with a_shoulder_margin of chest below, never closer than a_max_zoom. The zoom the
    placement wants (speaker centre to a_target_x) is used only as far as the rule
    allows; the window never leaves the source. Returns (cw, ch, x, y, report).
    """
    for k in ("speaker_eye_y", "speaker_shoulder_y"):
        if cfg[k] is None:
            sys.exit("ERROR: setting A framing needs the speaker's landmarks, as fractions of "
                     "the SOURCE frame: speaker_eye_y and speaker_shoulder_y (the line where "
                     "the shoulders meet the arms), and speaker_shoulder_x0 / _x1 (the span "
                     "of the shoulders). Look at source stills with --source-grid and set them "
                     "in timing.json. Without them the crop could go as close as it likes.")
    aspect = W / float(H)
    sx, tx = cfg["speaker_x"], cfg["a_target_x"]
    eye, shy, e = cfg["speaker_eye_y"], cfg["speaker_shoulder_y"], cfg["a_eye_line"]
    kw = src_h * aspect / float(src_w)       # crop width fraction per crop height fraction
    hc_want = min(sx / tx, (1 - sx) / (1 - tx)) / kw         # what the placement asks for
    hc_min_zoom = 1.0 / cfg["a_max_zoom"]
    hc_min_v = (shy + cfg["a_shoulder_margin"] - eye) / (1 - e)   # shoulders and chest in frame
    x0s, x1s = cfg["speaker_shoulder_x0"], cfg["speaker_shoulder_x1"]
    hc_min_h = ((x1s - x0s) + 0.04) / kw if x0s is not None and x1s is not None else 0.0
    hc_floor = max(hc_min_zoom, hc_min_v, hc_min_h)          # the closest the rule allows
    hc = max(hc_floor, min(hc_want, 0.99))
    wc = hc * kw
    if wc > 1.0:                                              # the source is narrower than 16:9
        wc, hc = 1.0, 1.0 / kw
    x0 = clamp(sx - tx * wc, 0.0, 1.0 - wc)
    if x0s is not None and x1s is not None:                  # keep both shoulders inside
        lo, hi = x1s + 0.02 - wc, x0s - 0.02
        if lo <= hi:
            x0 = clamp(x0, max(0.0, lo), min(1.0 - wc, hi))
    y0 = clamp(eye - e * hc, 0.0, 1.0 - hc)
    ch = even(src_h * hc)
    cw = even(ch * aspect)
    if cw > src_w:
        cw = src_w - src_w % 2
        ch = even(cw / aspect)
    x = int(round(x0 * src_w)); x -= x % 2
    y = int(round(y0 * src_h)); y -= y % 2
    x = min(x, src_w - cw); y = min(y, src_h - ch)
    # what came out
    eye_out = (eye - y / float(src_h)) / (ch / float(src_h))
    bottom_src = (y + ch) / float(src_h)
    left_src, right_src = x / float(src_w), (x + cw) / float(src_w)
    spk_out = (sx - left_src) / (right_src - left_src)
    warn = []
    if abs(eye_out - e) > 0.04:
        warn.append("the eyes are at %.2f of the height, not %.2f (the window cannot go "
                    "above the top of the source)" % (eye_out, e))
    if bottom_src < shy + 0.02:
        warn.append("SHOULDERS NOT IN FRAME: the source ends above the shoulder line; film wider")
    if x0s is not None and (left_src > x0s - 0.005 or right_src < x1s + 0.005):
        warn.append("a shoulder is cut by the frame: the source itself cuts it, or the shoulder "
                    "span is wider than the window; film wider or further from the left edge")
    if spk_out < tx - 0.06:
        warn.append("the speaker stands at %.2f of the A frame, left of the target %.2f: the "
                    "framing rule limits the zoom, so the speaker overlaps the free left %d%%. "
                    "The rule wins. Reposition the speaker for the next shoot."
                    % (spk_out, tx, int(cfg["a_free_left"] * 100)))
    report = {"zoom": src_h / float(ch), "eye_out": eye_out, "speaker_out_x": spk_out,
              "bottom_src": bottom_src, "warnings": warn}
    return cw, ch, x, y, report


def ins_crop(src_w, src_h, iw, ih, cfg):
    """The vertical view of the speaker that fills the insert."""
    ia = iw / float(ih)
    ch = even(src_h * 0.98 / cfg["ins_zoom"])
    cw = even(ch * ia)
    if cw > src_w:
        cw = src_w - src_w % 2
        ch = even(cw / ia)
    cx = cfg["ins_speaker_x"] if cfg["ins_speaker_x"] is not None else cfg["speaker_x"]
    x = int(round(clamp(cx * src_w - cw / 2.0, 0, src_w - cw)))
    y = int(round((src_h - ch) * cfg["ins_crop_y"]))
    return cw, ch, x - x % 2, y - y % 2


# ---------------------------------------------------------------- plates

def make_mask(path, w, h, r):
    """A white rounded rectangle on black, w x h, anti-aliased (drawn 3x and scaled down)."""
    k = 3
    R = r * k
    expr = ("if(lte(hypot(max(abs(X-W/2)-(W/2-%d),0),max(abs(Y-H/2)-(H/2-%d),0)),%d),255,0)"
            % (R, R, R))
    run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=black:s=%dx%d,format=gray,geq=lum='%s',scale=%d:%d:flags=area,format=gray"
         % (w * k, h * k, expr, w, h), "-frames:v", "1", str(path)])


def shadow_chain(src_blk, mask_label, rect, label_in, label_out, idx, cfg):
    """Filter text: a soft shadow in the shape of the rounded rectangle, onto label_in.

    src_blk is a label of a black rgba stream of the rectangle's size, mask_label its
    rounded-corner mask (gray)."""
    x, y, w, h = rect
    r = int(cfg["shadow_blur"])
    return (
        "[%s][%s]alphamerge,colorchannelmixer=aa=%.2f,"
        "pad=%d:%d:%d:%d:color=black@0,boxblur=%d:2[sh%d];"
        "[%s][sh%d]overlay=%d:%d[%s]" % (
            src_blk, mask_label, cfg["shadow_alpha"], w + 2 * r, h + 2 * r, r, r, r // 2, idx,
            label_in, idx, x - r + int(cfg["shadow_dx"]), y - r + int(cfg["shadow_dy"]),
            label_out))


def make_plate_b(path, slide_png, aspect, cfg, masks):
    """Background + slide shadow + rounded slide + insert shadow, one opaque frame.

    masks = (slide mask png, insert mask png)."""
    slide, box = geometry(aspect, cfg)
    bg = cfg["background"]
    cmd = [FFMPEG, "-v", "error", "-y"]
    if bg.startswith("#"):
        cmd += ["-f", "lavfi", "-i", "color=c=0x%s:s=%dx%d" % (bg[1:], W, H)]
    else:
        cmd += ["-i", bg]
    cmd += ["-i", str(slide_png), "-i", str(masks[0]), "-i", str(masks[1])]
    chain = [
        "[0:v]scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,format=rgb24[bg]"
        % (W, H, W, H),
        "[2:v]format=gray,split=2[ms1][ms2]",
        "[3:v]format=gray[mi]",
        "color=c=black:s=%dx%d,format=rgba[blk1]" % (slide[2], slide[3]),
        shadow_chain("blk1", "ms1", slide, "bg", "b1", 1, cfg),
        "[1:v]scale=%d:%d:flags=lanczos,format=rgba[sl0];[sl0][ms2]alphamerge[sl]"
        % (slide[2], slide[3]),
        "[b1][sl]overlay=%d:%d[b2]" % (slide[0], slide[1]),
        "color=c=black:s=%dx%d,format=rgba[blk2]" % (box[2], box[3]),
        shadow_chain("blk2", "mi", box, "b2", "b3", 2, cfg),
        "[b3]format=rgba[out]",
    ]
    run(cmd + ["-filter_complex", ";".join(chain), "-map", "[out]",
               "-frames:v", "1", str(path)])


def make_plate_a(path):
    """A fully transparent frame: setting A shows the speaker through it."""
    run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
         "color=c=black@0.0:s=%dx%d,format=rgba" % (W, H), "-frames:v", "1", str(path)])


# ---------------------------------------------------------------- timing

def load_segments(timing, info, n_slides):
    segs = timing.get("segments") or []
    if not segs:
        sys.exit("ERROR: timing.json has no segments.")
    fps = info["fps"]
    total = int(round(info["duration"] * fps))
    out = []
    for i, s in enumerate(segs):
        f0 = 0 if i == 0 else int(round(Fraction(str(s["start"])) * fps))
        setting = str(s.get("setting", "")).upper()
        if setting == "A":
            slide = None
        elif setting == "B":
            slide = s.get("slide")
            if not isinstance(slide, int) or not 1 <= slide <= n_slides:
                sys.exit("ERROR: segment %d (setting B) needs \"slide\": N, 1 to %d."
                         % (i + 1, n_slides))
        else:
            sys.exit("ERROR: segment %d needs \"setting\": \"A\" or \"B\"." % (i + 1))
        out.append({"f0": f0, "setting": setting, "slide": slide})
    for i, s in enumerate(out):
        s["f1"] = out[i + 1]["f0"] if i + 1 < len(out) else total
        if s["f1"] <= s["f0"]:
            sys.exit("ERROR: segment %d has no length: each start must come after "
                     "the one before, and before the end of the video." % (i + 1))
    # a run is a stretch of one setting; a slide change inside B does not end it
    run_start = 0
    for i in range(1, len(out) + 1):
        if i == len(out) or out[i]["setting"] != out[run_start]["setting"]:
            secs = float((out[i - 1]["f1"] - out[run_start]["f0"]) / fps)
            limit = MIN_A if out[run_start]["setting"] == "A" else MIN_B
            if secs < limit:
                sys.stderr.write(
                    "WARNING: setting %s lasts only %.1f s at %.1f s; a back and forth "
                    "that fast looks nervous.\n" % (out[run_start]["setting"], secs,
                                                    float(out[run_start]["f0"] / fps)))
            run_start = i
    return out, total


def b_ranges(segs):
    spans = []
    for s in segs:
        if s["setting"] != "B":
            continue
        if spans and spans[-1][1] == s["f0"]:
            spans[-1][1] = s["f1"]
        else:
            spans.append([s["f0"], s["f1"]])
    return spans


# ---------------------------------------------------------------- graph

def build_graph(segs, plate_inputs, info, geo, cfg, mask_idx=None, still=False, guides=False):
    """Filter graph text. Input 0 is the speaker; plate inputs follow; mask_idx is the input
    that holds the insert's rounded-corner mask."""
    fps = info["fps"]
    slide, box = geo
    spans = b_ranges(segs)
    has_b = bool(spans)
    chain = []
    src = "[0:v]setpts=PTS-STARTPTS" + ("" if still else ",fps=%s" % fps)
    if has_b:
        chain.append(src + ",split=2[s_a][s_b]")
    else:
        chain.append(src + "[s_a]")
    cw, ch, x, y, _rep = a_crop(info["w"], info["h"], cfg)
    a = "[s_a]crop=%d:%d:%d:%d,scale=%d:%d:flags=lanczos,setsar=1,format=yuv420p" % (
        cw, ch, x, y, W, H)
    if guides:
        gx = int(round(W * cfg["a_free_left"]))
        # black and white side by side, so the line shows on any picture
        a += (",drawbox=x=%d:y=0:w=3:h=%d:color=black:t=fill"
              ",drawbox=x=%d:y=0:w=3:h=%d:color=white:t=fill" % (gx - 3, H, gx, H))
        ey = int(round(H * cfg["a_eye_line"]))      # the upper-third line the eyes belong on
        a += (",drawbox=x=0:y=%d:w=%d:h=3:color=black:t=fill"
              ",drawbox=x=0:y=%d:w=%d:h=3:color=white:t=fill" % (ey - 3, W, ey, W))
    chain.append(a + "[base]")
    cur = "base"
    if has_b:
        icw, ich, ix, iy = ins_crop(info["w"], info["h"], box[2], box[3], cfg)
        chain.append("[s_b]crop=%d:%d:%d:%d,scale=%d:%d:flags=lanczos,setsar=1,"
                     "format=yuva420p[ins0]" % (icw, ich, ix, iy, box[2], box[3]))
        chain.append("[%d:v]format=gray,setsar=1[mi];[ins0][mi]alphamerge[ins]" % mask_idx)
        labels = []
        for i, s in enumerate(segs):
            idx = plate_inputs[i]
            lab = "p%d" % i
            if still:
                chain.append("[%d:v]format=rgba,setsar=1[%s]" % (idx, lab))
            else:
                chain.append("[%d:v]trim=end_frame=%d,setpts=PTS-STARTPTS,format=rgba,"
                             "setsar=1[%s]" % (idx, s["f1"] - s["f0"], lab))
            labels.append(lab)
        if len(labels) > 1:
            chain.append("%sconcat=n=%d:v=1:a=0,setpts=N/(%s)/TB[plates]"
                         % ("".join("[%s]" % l for l in labels), len(labels), fps))
        else:
            chain.append("[%s]null[plates]" % labels[0])
        chain.append("[base][plates]overlay=0:0:format=auto:eof_action=repeat[withplate]")
        en = "" if still else ":enable='%s'" % "+".join(
            "between(n,%d,%d)" % (a0, b0 - 1) for a0, b0 in spans)
        chain.append("[withplate][ins]overlay=%d:%d:format=auto:eof_action=repeat%s[comp]"
                     % (box[0], box[1], en))
        cur = "comp"
    chain.append("[%s]format=yuv420p[vout]" % cur)
    return ";".join(chain)


# ---------------------------------------------------------------- main

def default_background():
    """The user's own plate (config.json -> slides.background), else the bundled neutral one."""
    data = os.environ.get("TV_DATA", "").strip()
    if data:
        try:
            cfg = json.loads((Path(data).expanduser() / "config.json").read_text())
            bg = ((cfg.get("slides") or {}).get("background") or "").strip()
            if bg and (bg.startswith("#") or Path(bg).expanduser().is_file()):
                return bg
        except (OSError, ValueError):
            pass
    return str(SKILL_DIR / "assets" / "background-plate.png")


def settings_from(a, timing):
    cfg = dict(DEFAULTS)
    for k in DEFAULTS:
        if timing.get(k) is not None:
            cfg[k] = timing[k]
    for item in a.set or []:
        if "=" not in item:
            sys.exit("ERROR: --set takes key=value, got %r" % item)
        k, v = item.split("=", 1)
        if k not in DEFAULTS:
            sys.exit("ERROR: unknown setting %r. Known: %s" % (k, ", ".join(sorted(DEFAULTS))))
        if k in ("background", "ins_side"):
            cfg[k] = v
        else:
            cfg[k] = None if v.lower() == "none" else float(v)
    if not cfg["background"]:
        cfg["background"] = default_background()
    bg = cfg["background"]
    if not bg.startswith("#"):
        bg = str(Path(bg).expanduser())
        if not Path(bg).is_file():
            sys.exit("ERROR: no background image at %s" % bg)
        cfg["background"] = bg
    if not 0.05 <= cfg["speaker_x"] <= 0.95:
        sys.exit("ERROR: speaker_x is the speaker's centre as a fraction of the "
                 "source width (0.05 to 0.95).")
    return cfg


def prepare(a):
    work = Path(a.work).expanduser().resolve()
    slides = json.loads((work / "slides.json").read_text())
    timing = json.loads(Path(a.timing).expanduser().read_text())
    cfg = settings_from(a, timing)
    info = probe(a.video)
    segs, total = load_segments(timing, info, slides["count"])
    aspect = float(slides["aspect"])
    geo = geometry(aspect, cfg)
    slide, box = geo
    if box[0] + box[2] > W or box[1] + box[3] > H:
        sys.exit("ERROR: the insert does not fit in the frame: lower ins_h.")
    if any(x["setting"] == "A" for x in segs):
        _FRAMING_WARNINGS.extend(a_crop(info["w"], info["h"], cfg)[4]["warnings"])
    plates = work / "plates"
    plates.mkdir(exist_ok=True)
    r = int(cfg["corner_radius"])
    masks = (plates / "mask-slide.png", plates / "mask-insert.png")
    make_mask(masks[0], slide[2], slide[3], r)
    make_mask(masks[1], box[2], box[3], r)
    made, files = {}, []
    for s in segs:
        key = "a" if s["setting"] == "A" else s["slide"]
        if key not in made:
            path = plates / ("plate-a.png" if key == "a" else "plate-b-%03d.png" % key)
            if key == "a":
                make_plate_a(path)
            else:
                make_plate_b(path, work / slides["pages"][key - 1]["image"], aspect, cfg, masks)
            made[key] = path
        files.append(made[key])
    return work, slides, cfg, info, segs, total, files, geo, masks


def write_settings(work, a, info, segs, total, out_path=None, background=None):
    fps = info["fps"]
    rec = {
        "_comment": "Written by lecture_compose.py. map_settings.py reads it.",
        "source": str(Path(a.video).expanduser().resolve()),
        "output": str(Path(out_path).expanduser().resolve()) if out_path else None,
        "background": background,
        "fps": str(fps), "frames": int(total),
        "duration": round(float(total / fps), 4),
        "segments": [{"f0": s["f0"], "f1": s["f1"],
                      "t0": round(float(s["f0"] / fps), 4),
                      "t1": round(float(s["f1"] / fps), 4),
                      "setting": s["setting"], "slide": s["slide"]} for s in segs],
    }
    (Path(work) / "settings.json").write_text(json.dumps(rec, indent=1) + "\n")


def render(a, ctx):
    work, slides, cfg, info, segs, total, files, geo, masks = ctx
    fps = info["fps"]
    cmd = [FFMPEG, "-v", "error", "-y", "-i", str(a.video)]
    plate_inputs = []
    if b_ranges(segs):          # a lecture without setting B needs no plates
        for i, f in enumerate(files):
            cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(f)]
            plate_inputs.append(i + 1)
    mask_idx = None
    if b_ranges(segs):
        mask_idx = len(plate_inputs) + 1
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", str(masks[1])]
    graph = build_graph(segs, plate_inputs, info, geo, cfg, mask_idx=mask_idx)
    script = work / "filtergraph.txt"
    script.write_text(graph)
    cmd += ["-filter_complex_script", str(script), "-map", "[vout]"]
    if info["audio"]:
        cmd += ["-map", "0:a:0"]
        cmd += ["-c:a", "copy"] if info["audio"] == "aac" else ["-c:a", "aac", "-b:a", "256k"]
    cmd += ["-frames:v", str(total), "-r", str(fps),
            "-c:v", "libx264", "-preset", a.preset, "-crf", str(a.crf),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(a.out)]
    for w_ in _FRAMING_WARNINGS:
        sys.stderr.write("WARNING (setting A framing): %s\n" % w_)
    (work / "compose-warnings.txt").write_text("\n".join(_FRAMING_WARNINGS) + ("\n" if _FRAMING_WARNINGS else ""))
    print("Rendering %d frames (%d segments) to %s" % (total, len(segs), a.out))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        sys.exit("ERROR: the render failed. The filter graph is in %s" % script)
    write_settings(work, a, info, segs, total, a.out, cfg["background"])
    check(a, info, segs, total)


def check(a, info, segs, total):
    out = probe(a.out)
    fps = info["fps"]
    frames = int(round(out["duration"] * fps))
    ok = abs(frames - total) <= 1
    print("Length: %.3f s, source %.3f s (%s)" % (
        out["duration"], info["duration"], "ok" if ok else "MISMATCH"))
    if not ok:
        sys.exit("ERROR: the composite is %d frames, the source %d: frames were dropped."
                 % (frames, total))
    if info["audio"] and not out["audio"]:
        sys.exit("ERROR: the composite has no sound.")
    shots = Path(a.work) / "check"
    shots.mkdir(exist_ok=True)
    seen = set()
    for s in segs:
        if s["setting"] in seen:
            continue
        seen.add(s["setting"])
        t = float((s["f0"] + s["f1"]) / 2 / fps)
        dest = shots / ("check-%s.png" % s["setting"])
        run([FFMPEG, "-v", "error", "-y", "-ss", "%.3f" % t, "-i", str(a.out),
             "-frames:v", "1", str(dest)])
        print("Look at: %s (%.1f s)" % (dest, t))


def preview(a, ctx):
    """Stills of the composite at chosen times, without a render."""
    work, slides, cfg, info, segs, total, files, geo, masks = ctx
    fps = info["fps"]
    if a.at:
        times = [float(t) for t in a.at.split(",")]
    else:
        times, seen = [], set()
        for s in segs:
            if s["setting"] not in seen:
                seen.add(s["setting"])
                times.append(float((s["f0"] + s["f1"]) / 2 / fps))
    shots = work / "check"
    shots.mkdir(exist_ok=True)
    for t in times:
        f = int(round(t * float(fps)))
        i = next((j for j, s in enumerate(segs) if s["f0"] <= f < s["f1"]), len(segs) - 1)
        seg = dict(segs[i], f0=0, f1=1)
        frame = shots / "src.png"
        run([FFMPEG, "-v", "error", "-y", "-ss", "%.3f" % t, "-i", str(a.video),
             "-frames:v", "1", str(frame)])
        graph = build_graph([seg], [1], info, geo, cfg, mask_idx=2, still=True,
                            guides=a.guides and seg["setting"] == "A")
        dest = shots / ("preview-%07.2fs-%s.png" % (t, seg["setting"]))
        cmd = [FFMPEG, "-v", "error", "-y", "-i", str(frame)]
        if seg["setting"] == "B":
            cmd += ["-i", str(files[i]), "-i", str(masks[1])]
        run(cmd + ["-filter_complex", graph, "-map", "[vout]", "-frames:v", "1", str(dest)])
        label = ("B, slide %d" % seg["slide"]) if seg["slide"] else "A"
        print("Look at: %s (%.1f s, setting %s)" % (dest, t, label))
    frame = shots / "src.png"
    if frame.exists():
        frame.unlink()
    cw, ch, x, y, rep = a_crop(info["w"], info["h"], cfg)
    print("Setting A: crop %dx%d at %d,%d of the %dx%d source: zoom %.2fx, eyes at %.2f of the "
          "height (line %.2f), speaker centre at %.2f (target %.2f), crop reaches %.2f of the "
          "source height (shoulder line %.2f)." % (
              cw, ch, x, y, info["w"], info["h"], rep["zoom"], rep["eye_out"], cfg["a_eye_line"],
              rep["speaker_out_x"], cfg["a_target_x"], rep["bottom_src"], cfg["speaker_shoulder_y"]))
    for w_ in rep["warnings"]:
        print("WARNING: " + w_)
    slide, box = geo
    print("Setting B: slide %dx%d at %d,%d; insert %dx%d at %d,%d." % (
        slide[2], slide[3], slide[0], slide[1], box[2], box[3], box[0], box[1]))


def source_grid(a):
    """Source stills with a 10% grid (a heavier line at 50% and at 1/3 of the height), for
    reading the speaker's landmarks: eyes, shoulder line, shoulder ends."""
    shots = Path(a.work).expanduser() / "check"
    shots.mkdir(parents=True, exist_ok=True)
    if a.at:
        times = [float(t) for t in a.at.split(",")]
    else:   # four moments spread over the clip
        dur = probe(a.video)["duration"]
        times = [round(dur * f, 1) for f in (0.12, 0.37, 0.62, 0.87)]
    # Black and white only (lightness, not hue, carries every line): thin lines every 10%,
    # thick lines at 50%, and a double black-and-white line at 1/3 of the height.
    vf = ("drawgrid=w=iw/10:h=ih/10:t=2:c=white@0.6,"
          "drawgrid=w=iw/2:h=ih/2:t=5:c=black@0.9,"
          "drawbox=x=0:y=ih/3-3:w=iw:h=3:color=black:t=fill,"
          "drawbox=x=0:y=ih/3:w=iw:h=3:color=white:t=fill")
    for t in times:
        dest = shots / ("source-grid-%06.1fs.png" % t)
        run([FFMPEG, "-v", "error", "-y", "-ss", "%.3f" % t, "-i", str(a.video), "-frames:v", "1",
             "-vf", vf, str(dest)])
        print("Look at: %s" % dest)
    print("Grid: thin white lines every 10% of the width and height; thick black lines at 50%; "
          "a black-and-white double line at 1/3 of the height. Read speaker_eye_y, speaker_shoulder_y (where the shoulders meet the "
          "arms), speaker_shoulder_x0 / x1 as fractions of the SOURCE frame, averaged over "
          "the stills (the speaker moves).")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--video", required=True, help="The enhanced talking-head video")
    ap.add_argument("--work", required=True, help="Folder with slides/ and slides.json")
    ap.add_argument("--timing", required=True, help="timing.json")
    ap.add_argument("--out", help="Output .mp4 (not needed with --preview)")
    ap.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="Override a number in DEFAULTS, e.g. --set a_target_x=0.6")
    ap.add_argument("--preview", action="store_true",
                    help="Write stills to WORK/check/ instead of rendering")
    ap.add_argument("--at", default=None, help="With --preview: times in seconds, comma-separated")
    ap.add_argument("--source-grid", action="store_true",
                    help="Write SOURCE stills with a grid to WORK/check/ (--at sets the times) "
                         "for reading the speaker's landmarks, then stop")
    ap.add_argument("--guides", action="store_true",
                    help="With --preview: draw the edge of the free 46%% and the upper-third eye line in setting A")
    ap.add_argument("--crf", type=int, default=16)
    ap.add_argument("--preset", default="fast")
    a = ap.parse_args()
    if a.source_grid:
        source_grid(a)
        return
    if not a.preview and not a.out:
        sys.exit("ERROR: --out is required unless --preview is given.")
    ctx = prepare(a)
    if a.preview:
        preview(a, ctx)
    else:
        render(a, ctx)


if __name__ == "__main__":
    main()
