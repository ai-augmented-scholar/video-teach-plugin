#!/usr/bin/env python3
"""The mechanical half of the editorial pass: speech map + decisions -> cuts.json and the cut log.

Usage:
    python3 refine_cuts.py --video CLIP.mp4 --map map.json --words words.json \
        --decisions decisions.json --out-dir DIR [--cues cues.json] [--merge-gap 0.6]

The model's editorial pass (SKILL.md step 4) is judgment: which retakes, false starts and
asides go. This script is everything mechanical that follows, so a pass that cuts at the
waveform below block level (lecture-video uses it) calls it instead of redoing it by hand:

  1. cut ranges     Every block whose middle lies in a range of the decisions file is CUT,
                    with the reason written beside the range. --cues adds the optional
                    spoken "setting A / setting B" directions of lecture-video
                    (cues.json) the same way: they are direction, never content.
  2. automatic cuts Empty blocks (filler sound, breath, click: measure the long ones first,
                    step 3) and blocks that hold only a filler word.
  3. start trims    "trim_starts" in the decisions file: a block that begins with a stumble
                    ("I'll grow I'll go over") starts at the voice's rise after the pause
                    found between two search times.
  4. filler words   A filler (uh, um) at a block's start is dropped and the block starts
                    where speech resumes: the rise after a dip, else the local minimum, else
                    the next word's start minus 0.04 s. A filler inside a block is cut at
                    the waveform when the dips on both sides are clear.
  5. burst excision An isolated voiced burst of 0.15-0.6 s inside a block that no word
                    claims is cut at its dips. A word claims every voiced stretch it
                    overlaps (a word's tail can sit behind a short dip), capped at 0.8 s,
                    because Parakeet's word times run up to 0.25 s late; with plain overlap
                    the ends of long words and whole short words were cut.
  6. out-point clamp A clip never ends inside a cut filler that follows within 0.2 s.
  7. merging        Consecutive kept blocks with under --merge-gap (0.6 s) between them
                    become one clip; a block that was cut inside is not merged across.

The sound envelope is read from the clip itself (10 ms RMS in dBFS, 16 kHz mono). The
waveform decides where to cut; transcript word times only say which words exist.

decisions.json:
    {"cut_ranges":  [[start_s, end_s, "reason"], ...],
     "trim_starts": [[block_start_s, search_from_s, search_to_s, "reason"], ...]}
Times are seconds on CLIP.mp4's clock. Both lists are optional.

Writes DIR/cuts.json in build_package.py's frames mode ({"mode": "frames", "clips":
[{in_frame, out_frame, text}], "cut_log": ...}; build the package from it with
build_package.py --cuts DIR/cuts.json) and DIR/cut-log.csv (the revival sheet, UTF-8 with
BOM). Prints the clips. Check the result by transcribing the rendered MP4: no cut words,
no retake, the fillers left, and a read for chopped words. Worked example:
lecture-video/reference/example-decisions.json. On a first real 8-minute lecture take
it left 1 filler of about 45. Stdlib only.
"""
import argparse
import csv
import json
import math
import re
import subprocess
import sys
from array import array
from operator import mul
from pathlib import Path

FILLER_TOK = {"uh", "um", "uhm", "ah", "er", "erm"}
HOP = 160          # 10 ms at 16 kHz
SR = 16000


# ---------------------------------------------------------------- envelope

def load_env(video):
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(video), "-vn", "-ac", "1",
                          "-ar", str(SR), "-f", "s16le", "-"], stdout=subprocess.PIPE).stdout
    if not raw:
        sys.exit("ERROR: ffmpeg could not read the sound of %s" % video)
    x = array("h")
    x.frombytes(raw[: len(raw) // 2 * 2])
    n = len(x) // HOP
    scale = float(HOP) * 32768.0 * 32768.0
    env = []
    for i in range(n):
        fr = x[i * HOP:(i + 1) * HOP]
        ms = sum(map(mul, fr, fr)) / scale
        env.append(20.0 * math.log10(math.sqrt(ms + 1e-12)))
    return env


def rise_after(env, t0, t1, thr_in=-30.0, thr_out=-38.0):
    """First time in [t0, t1] the voice rises above thr_in after a dip below thr_out."""
    i0, i1 = int(t0 * 100), int(t1 * 100)
    seg = env[i0:i1]
    dip = next((k for k, v in enumerate(seg) if v < thr_out), None)
    if dip is not None:
        for k in range(dip, len(seg)):
            if seg[k] > thr_in:
                return (i0 + k) / 100.0, "dip+rise"
    for k, v in enumerate(seg):
        if v > thr_in:
            return (i0 + k) / 100.0, "rise"
    return None, "none"


def min_point(env, t0, t1):
    i0 = int(t0 * 100)
    i1 = max(int(t1 * 100), i0 + 1)
    seg = env[i0:i1]
    k = min(range(len(seg)), key=seg.__getitem__)
    return (i0 + k) / 100.0, seg[k]


def voiced_runs(env, a, b, thr=-38.0, dip=0.06):
    """Voiced stretches between a and b, split by dips under thr of at least `dip` s."""
    i0, i1 = int(a * 100), int(b * 100)
    runs, start, last_voiced, gap = [], None, None, 0
    for i in range(i0, min(i1, len(env))):
        if env[i] >= thr:
            if start is None:
                start = i
            last_voiced, gap = i, 0
        else:
            gap += 1
            if start is not None and gap * 0.01 >= dip:
                runs.append((start / 100.0, (last_voiced + 1) / 100.0))
                start = None
    if start is not None:
        runs.append((start / 100.0, (last_voiced + 1) / 100.0))
    return runs


# ---------------------------------------------------------------- words

def tok(t):
    return re.sub(r"[^a-z]+", "", t.lower())


def load_words(path):
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and "segments" in data:
        return [x for s in data["segments"] for x in s.get("words", [])]
    if isinstance(data, dict) and "words" in data:
        return data["words"]
    sys.exit("ERROR: cannot read words from %s" % path)


# ---------------------------------------------------------------- the rules

def after_filler(env, fw, nxt):
    """Where speech resumes after the filler word fw; nxt is the next word, or None."""
    nstart = nxt["start"] if nxt else None
    t, how = rise_after(env, fw["end"] - 0.05, fw["end"] + 0.9)
    if how == "dip+rise" and t and (nstart is None or t <= nstart + 0.15):
        return t, "dip+rise"
    if nstart is not None:
        mt, mv = min_point(env, fw["end"] - 0.05, nstart + 0.10)
        if mv < -22.0:
            return mt + 0.01, "local minimum %.0f dB" % mv
        return nstart - 0.04, "next word start"
    return fw["end"] + 0.05, "word end"


def excise_bursts(env, words, pieces):
    out, notes = [], []
    for (a, c) in pieces:
        runs = voiced_runs(env, a, c)
        claimed = set()
        for x in words:
            if tok(x["word"]) in FILLER_TOK or x["end"] < a - 0.5 or x["start"] > c + 0.5:
                continue
            lo, hi = x["start"] - 0.05, min(x["end"], x["start"] + 0.8)
            hit = [k for k, (ra, rb) in enumerate(runs) if min(rb, hi) - max(ra, lo) >= 0.03]
            if not hit:
                dist = [(min(abs(ra - x["end"]), abs(rb - x["start"])), k)
                        for k, (ra, rb) in enumerate(runs)]
                dist = [d for d in dist if d[0] < 0.4]
                hit = [min(dist)[1]] if dist else []
            claimed.update(hit)
        keep_from = a
        for k, (ra, rb) in enumerate(runs):
            dur = rb - ra
            if k not in claimed and 0.15 <= dur <= 0.6:
                if ra - keep_from > 0.12:
                    out.append((keep_from, ra - 0.02))
                keep_from = rb + 0.02
                notes.append("cut burst %.2f-%.2f s (no word claims it, %.2f s)" % (ra, rb, dur))
        if c - keep_from > 0.12:
            out.append((keep_from, c))
    return out, notes


def tc(t, fps):
    fr = int(round(t * fps))
    s, ff = divmod(fr, int(round(fps)))
    return "%02d:%02d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60, ff)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--video", required=True, help="the clip the map was made from")
    ap.add_argument("--map", required=True, help="map.json from speech_map.py")
    ap.add_argument("--words", required=True, help="the transcript that map was built from")
    ap.add_argument("--decisions", default=None, help="decisions.json (cut_ranges, trim_starts)")
    ap.add_argument("--cues", default=None, help="cues.json of lecture-video: cut every cue")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--merge-gap", type=float, default=0.6)
    ap.add_argument("--sequence-name", default=None)
    a = ap.parse_args()

    video = Path(a.video).expanduser().resolve()
    m = json.loads(Path(a.map).expanduser().read_text())
    bl = m["blocks"]
    fps = float(m["fps"])
    words = load_words(Path(a.words).expanduser())
    dec = json.loads(Path(a.decisions).expanduser().read_text()) if a.decisions else {}
    cut_ranges = [tuple(r) for r in dec.get("cut_ranges", [])]
    if a.cues:
        for c in json.loads(Path(a.cues).expanduser().read_text())["cues"]:
            cut_ranges.append((c["start"] - 0.05, c["end"] + 0.05,
                               "edit cue: %s" % c.get("text", "setting %s" % c["setting"])))
    trim_starts = [tuple(r) for r in dec.get("trim_starts", [])]
    env = load_env(video)
    out_dir = Path(a.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    def in_cut(b):
        mid = (b["start"] + b["end"]) / 2
        for lo, hi, why in cut_ranges:
            if lo <= mid <= hi:
                return why
        return None

    rows, kept = [], []
    for i, b in enumerate(bl):
        text = b.get("text", "").strip()
        why = in_cut(b)
        t0, t1 = b.get("onset", b["start"]), b["end"]
        if why is None and not text:
            why = "empty block: filler sound, breath or click (measured: not speech)"
        if why is None and tok(text) in FILLER_TOK:
            why = "filler word"
        if why is None and b.get("split") == "filler" and \
                all(tok(x) in FILLER_TOK or not tok(x) for x in text.split()):
            why = "filler word"
        if why:
            rows.append((i, "CUT", t0, t1, text, why))
            continue
        notes = []
        for k, lo, hi, why2 in trim_starts:
            if abs(b["start"] - k) < 0.06:
                t, how = rise_after(env, lo, hi)
                if t:
                    t0 = t
                    notes.append("trimmed start to %.2f s (%s)" % (t, why2))
        pieces, cur = [], t0
        bw = [x for x in words if x["end"] > t0 - 0.02 and x["start"] < t1 + 0.02]
        for x in bw:
            if tok(x["word"]) in FILLER_TOK:
                nxt = next((y for y in bw if y["start"] >= x["end"] - 0.01 and y is not x), None)
                if x["start"] <= cur + 0.05:           # at the start of the block
                    nt, how = after_filler(env, x, nxt)
                    if nt and nt < t1:
                        cur = nt
                        notes.append("dropped leading '%s' (%s, now %.2f s)"
                                     % (x["word"].strip(), how, nt))
                    else:
                        cur = max(cur, x["end"])
                        notes.append("dropped leading '%s' (word end)" % x["word"].strip())
                else:                                   # inside the block
                    lo, _ = min_point(env, x["start"] - 0.15, x["start"] + 0.02)
                    nt, how = after_filler(env, x, nxt)
                    if nt and lo > cur + 0.15 and nt < t1 - 0.1:
                        pieces.append((cur, lo))
                        cur = nt
                        notes.append("cut inner '%s' (%.2f-%.2f s)" % (x["word"].strip(), lo, nt))
        pieces.append((cur, t1))
        pieces = [(p, q) for p, q in pieces if q - p >= 0.12]
        pieces, bnotes = excise_bursts(env, words, pieces)
        notes += bnotes
        if not pieces:
            rows.append((i, "CUT", t0, t1, text, "nothing left after filler removal"))
            continue
        rows.append((i, "KEPT", pieces[0][0], pieces[-1][1], text, "; ".join(notes) or "kept"))
        kept.append((i, pieces, text))

    # clips: merge neighbours whose gap is under --merge-gap
    clips, clip_of = [], {}
    prev_idx, prev_end_src, prev_cut_inside = None, None, False
    for i, pieces, text in kept:
        for k, (p, q) in enumerate(pieces):
            join = (clips and k == 0 and prev_idx is not None and i == prev_idx + 1
                    and p - prev_end_src < a.merge_gap and not prev_cut_inside)
            if join:
                clips[-1]["end"] = q
                clips[-1]["text"] += " " + text
            else:
                clips.append({"start": p, "end": q, "text": text if k == 0 else text + " (cont.)"})
            clip_of[i] = len(clips)
            prev_end_src = q
        prev_idx = i
        prev_cut_inside = len(pieces) > 1

    cut_onsets = sorted(r[2] for r in rows if r[1] == "CUT")
    out = []
    for c in clips:
        txt = re.sub(r"\(cont\.\)", "", c["text"]).strip()
        txt = re.sub(r"^((uh|um|uhm|er|ah)[,.]?\s+)+", "", txt, flags=re.I)
        txt = re.sub(r"(\s+(uh|um|uhm|er|ah)[,.]?)+$", "", txt, flags=re.I)
        out_f = int(math.ceil(c["end"] * fps)) + 1
        nxt = next((o for o in cut_onsets if o > c["end"] - 0.01), None)
        if nxt is not None and nxt - c["end"] < 0.2:     # never run into a cut filler
            out_f = min(out_f, int(math.floor(nxt * fps)))
            out_f = max(out_f, int(math.ceil(c["end"] * fps)))
        out.append({"in_frame": int(math.floor(c["start"] * fps)), "out_frame": out_f, "text": txt})
    total = sum(o["out_frame"] - o["in_frame"] for o in out) / fps

    cuts = {"mode": "frames", "map": str(Path(a.map).expanduser().resolve()),
            "cut_log": "cut-log.csv", "source": str(video), "fps": fps,
            "source_duration": m["duration"],
            "sequence_name": a.sequence_name or (video.stem + " perfect cut"),
            "clips": out}
    (out_dir / "cuts.json").write_text(json.dumps(cuts, indent=1, ensure_ascii=False))
    with open(out_dir / "cut-log.csv", "w", newline="", encoding="utf-8-sig") as f:
        wr = csv.writer(f)
        wr.writerow(["block", "status", "source_in", "source_out", "timeline_position", "text",
                     "reason"])
        for r in rows:
            wr.writerow([r[0], r[1], tc(r[2], fps), tc(r[3], fps),
                         clip_of.get(r[0], "") if r[1] == "KEPT" else "", r[4], r[5]])

    print("clips: %d, final length %.1f s of %.1f s (%.0f%%)" % (
        len(out), total, m["duration"], 100 * total / m["duration"]))
    print("cut blocks: %d, kept blocks: %d" % (
        sum(1 for r in rows if r[1] == "CUT"), sum(1 for r in rows if r[1] == "KEPT")))
    for r in rows:
        if r[1] == "KEPT" and r[5] != "kept":
            print("  [%3d] %s" % (r[0], r[5]))
    print()
    for o in out:
        print("%7.2f-%7.2f  %s" % (o["in_frame"] / fps, o["out_frame"] / fps, o["text"][:110]))
    print("\ncuts.json and cut-log.csv -> %s" % out_dir)


if __name__ == "__main__":
    main()
