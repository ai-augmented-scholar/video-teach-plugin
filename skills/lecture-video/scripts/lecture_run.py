#!/usr/bin/env python3
"""lecture_run.py: the lecture-video chain, in four phases, with no approval stop.

Usage (TV_DATA must hold the plugin data folder, as for every plugin script):

    lecture_run.py prepare "<video folder>" [--video V] [--deck D] [--pages 1-4]
                                            [--allow-bright] [--force]
    lecture_run.py compose "<video folder>"
    lecture_run.py cut     "<video folder>"
    lecture_run.py finish  "<video folder>"
    lecture_run.py next    "<video folder>"     which phase comes next, and what is missing

ONE INPUT: the video folder (a numbered folder under the plugin's videos_root, or any
folder). Put the talking-head recording in it, or in its raw/ subfolder, and the deck
(.pdf or .pptx) if the lecture has one. The chain finds both itself: the newest video and
the newest deck; with more than one candidate it takes the newest and logs what it left
alone. --video and --deck override the search (the files may then live anywhere). Work
goes to <folder>/lecture/, the cards to <folder>/text-cards/, and the finished files to
the folder.

NO DECK: the deck is optional. Without one the whole lecture is setting A (the speaker,
framed by the framing rule, with text cards in the free column); prepare skips the slide
steps and writes the one-segment timing itself, so Judgment 1 is the framing landmarks
only. Everything else runs the same.

Between the phases the model makes the judgment calls the skill describes (SKILL.md),
each with a reason written into the file it edits. Nothing asks the user anything:

    prepare  HDR grade (only for an HLG clip; the setting is searched), audio-enhance,
             the highlight gate, deck to PDF, slides to images and text, the transcript
             (Parakeet; a windowed pass when the whole-file pass dropped speech), the
             spoken cues (optional), a draft slide timing, a draft A/B timing, the
             timing table, and four source stills with a grid for the framing landmarks.
       -> model: correct the slide timing, decide A and B, read the landmarks.
    compose  preview stills, the A/B composite, the speech map of the composite and a
             block list for the editorial pass.
       -> model: the editorial pass, written as lecture/cut/decisions.json.
    cut      refine_cuts.py (the cues are cut out), the perfect-cuts package, the sound
             gate, the settings carried across the cuts (checked on the real picture),
             the words of the finished cut.
       -> model: the text cards for setting A, text-cards/cards.json.
    finish   card placement (never over setting B), the plate decision, the full-length
             transparent overlay, the alpha check inside setting B, the finished video
             with the text baked in, check stills, the final timing log, the captions
             and transcript of the finished video, and the summary.
       -> model: spelling of names and terms in the captions, and course-page-description.txt.

HARD STOPS, only for real failures, each written to <folder>/lecture/stop-reason.txt and
printed: no video, PQ HDR footage, a picture no grade brings under the
highlight gate (or the gate itself; --allow-bright turns it off), a deck that cannot
become a PDF, a broken input, a failed step, a failed check (composite length, the
mapped settings against the picture, an overlay with alpha inside setting B), a missing
judgment file the phase needs. A framing WARNING is not a stop; it goes in the summary.

Every choice lands in <folder>/lecture/run-log.csv (a rerun appends). A file the model
edited by hand since this command wrote it (slide-timing.json, timing.json) is never
overwritten; --force does it. Transcription is Parakeet only. Stdlib only.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

os.environ["PATH"] = os.pathsep.join(
    [os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin"])
HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parents[2]
PY = sys.executable
TRANSCRIBE = PLUGIN / "scripts" / "transcribe"
SLIDES = PLUGIN / "skills" / "video-with-slides" / "scripts"
PC = PLUGIN / "skills" / "perfect-cuts" / "scripts"
TT = PLUGIN / "skills" / "text-treatments" / "scripts"
MV = PLUGIN / "skills" / "make-video" / "scripts"
CAP = PLUGIN / "skills" / "captions-and-description" / "scripts"
ENHANCE = PLUGIN / "skills" / "audio-enhance" / "scripts" / "audio_enhance.py"
TV_DATA = os.environ.get("TV_DATA", "").strip()

GUARDED = ("slide-timing.json", "timing.json")
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".mkv"}
OWN_OUTPUTS = ("preview", "composite", "perfect cut", "overlay", "with-text", "with-cards",
               "with-slides", "1 watch", "grade-preview", "-alpha")
GATE = 230.0                     # the highlight gate: stop at or above this luma
GRADE_OK = 226.0                 # the first grade setting at or under this wins
# From the grade's defaults (they lift midtones: right for a dim, warm room) to a dark
# exposure (a bright white wall in neutral light needs the fourth rung).
LADDER = [
    ("the grade's defaults", []),
    ("exposure 0.75", ["--exposure", "0.75", "--midtone", "1.0", "--contrast", "0.15"]),
    ("exposure 0.55", ["--exposure", "0.55", "--midtone", "1.0", "--contrast", "0.15"]),
    ("exposure 0.45, midtone 0.95", ["--exposure", "0.45", "--midtone", "0.95", "--contrast", "0.15"]),
    ("exposure 0.38, midtone 0.95", ["--exposure", "0.38", "--midtone", "0.95", "--contrast", "0.15"]),
]
# The card column of setting A: the left part of the frame that the framing rule keeps
# free (the speaker lands right of 0.46 of the width). Fractions of the frame.
LECTURE_FREE_AREA = {"x": 0.03, "y": 0.30, "w": 0.36, "h": 0.58}

OUT = None                       # <folder>/lecture
RUNLOG = []                      # (phase, step, what, detail, why)
PHASE = "?"


# ---------------------------------------------------------------- plumbing

def note(step, what, detail="", why=""):
    RUNLOG.append((PHASE, step, what, str(detail), str(why)))


def write_runlog():
    if OUT is None or not RUNLOG:
        return
    f = OUT / "run-log.csv"
    new_file = not f.exists()
    stamp = time.strftime("%Y-%m-%d %H:%M")
    with open(f, "a", newline="", encoding="utf-8-sig" if new_file else "utf-8") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(["run", "phase", "step", "what", "detail", "why"])
        for r in RUNLOG:
            w.writerow([stamp] + list(r))
    RUNLOG.clear()


def stop(msg):
    """A hard stop: say why, and write the reason where the user will find it."""
    note("stop", "the run stopped", msg)
    if OUT is not None:
        (OUT / "stop-reason.txt").write_text("[%s] %s\n" % (PHASE, msg))
    write_runlog()
    sys.exit(msg)


def env():
    e = dict(os.environ)
    if TV_DATA:
        e["TV_DATA"] = TV_DATA
    return e


def sh(cmd, quiet=False, ok=(0,), capture=None):
    """Run a step. Output is shown as it comes (unless quiet) and, with capture, saved to that
    file. A failing step's last lines go into the stop reason, so the log says why."""
    p = subprocess.Popen([str(c) for c in cmd], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         env=env())
    tail, full = [], []
    for raw in p.stdout:
        line = raw.decode("utf-8", "replace").rstrip("\n")
        full.append(line)
        tail = (tail + [line])[-8:]
        if not quiet:
            print(line, flush=True)
    p.wait()
    if capture:
        Path(capture).write_text("\n".join(full) + "\n")
    if p.returncode not in ok:
        if quiet:
            sys.stderr.write("\n".join(tail) + "\n")
        stop("step failed: %s -- %s" % (" ".join(Path(str(c)).name for c in cmd[:3]),
                                        " | ".join(l for l in tail if l.strip())[-600:]))
    return p.returncode, "\n".join(full)


def step(n, total, text):
    print("\n[%s %d/%d] %s" % (PHASE, n, total, text), flush=True)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load(p, default=None):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return default


class State:
    """lecture/state.json: the files the phases share, and the hashes of what this command
    wrote (so a later hand edit is never overwritten)."""

    def __init__(self, out, force=False):
        self.file = out / "state.json"
        self.force = force
        self.data = load(self.file, {}) or {}
        self.data.setdefault("wrote", {})

    def __getitem__(self, k):
        return self.data.get(k)

    def __setitem__(self, k, v):
        self.data[k] = v
        self.save()

    def writable(self, path):
        p = Path(path)
        if not p.exists() or self.force:
            return True
        return self.data["wrote"].get(p.name) == digest(p)

    def wrote(self, path):
        self.data["wrote"][Path(path).name] = digest(path)
        self.save()

    def save(self):
        self.file.write_text(json.dumps(self.data, indent=1) + "\n")


def probe(video):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=color_transfer,width,height,r_frame_rate,pix_fmt:format=duration",
                          "-of", "json", str(video)], stdout=subprocess.PIPE).stdout
    try:
        d = json.loads(out)
        s = d["streams"][0]
        return s.get("color_transfer", ""), float(d["format"]["duration"]), s
    except (ValueError, KeyError, IndexError):
        stop("%s is not a readable video." % video)


def fps_of(stream):
    num, den = (int(x) for x in stream["r_frame_rate"].split("/"))
    return num / float(den)


def highlight_level(video, duration, pix_fmt):
    """Median over seven frames of the 90th-percentile luma, on the 8-bit scale (white = 235)."""
    depth = 12 if "12" in pix_fmt else 10 if "10" in pix_fmt else 8
    vals = []
    for k in range(1, 8):
        t = duration * k / 8.0
        r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", "%.2f" % t, "-i", str(video),
                            "-frames:v", "1", "-vf",
                            "scale=480:270,signalstats,metadata=print:key=lavfi.signalstats.YHIGH:file=-",
                            "-f", "null", "-"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        m = re.search(r"YHIGH=([\d.]+)", r.stdout.decode("utf-8", "replace"))
        if m:
            vals.append(float(m.group(1)) / (2 ** (depth - 8)))
    return sorted(vals)[len(vals) // 2] if vals else None


def fmt(t):
    m, s = divmod(max(float(t), 0.0), 60)
    return "%d:%04.1f" % (m, s)


def words_of(path):
    data = load(path, {}) or {}
    return [w for s in data.get("segments", []) for w in s.get("words", [])]


def config():
    if not TV_DATA:
        return {}
    return load(Path(TV_DATA).expanduser() / "config.json", {}) or {}


# ---------------------------------------------------------------- finding the inputs

def newest(paths):
    return sorted(paths, key=lambda p: p.stat().st_mtime, reverse=True)


def find_inputs(folder):
    places = [folder] + ([folder / "raw"] if (folder / "raw").is_dir() else [])
    files = [p for d in places for p in d.iterdir() if p.is_file() and not p.name.startswith((".", "~"))]
    vids = [p for p in files if p.suffix.lower() in VIDEO_EXT
            and not any(w in p.name.lower() for w in OWN_OUTPUTS)]
    decks = [p for p in files if p.suffix.lower() in (".pptx", ".pdf")]
    return newest(vids), newest(decks)


def choose(kind, found, where):
    if not found:
        return None
    pick = found[0]
    if len(found) == 1:
        note("input", "%s chosen" % kind, pick, "the only candidate in %s" % where)
    else:
        note("input", "%s chosen" % kind, pick, "the newest of %d candidates in %s; left alone: %s"
             % (len(found), where, "; ".join(p.name for p in found[1:])))
    return pick


# ---------------------------------------------------------------- grade and enhance

def venv_python():
    if not TV_DATA:
        return None
    p = Path(TV_DATA).expanduser() / "venv" / "bin" / "python"
    if p.exists() and subprocess.run([str(p), "-c", "import numpy"], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL).returncode == 0:
        return str(p)
    return None


def preview_level(py, src, t, args):
    """Brightest-10%-of-pixels luma of the graded half of a hdr_grade.py preview at second t."""
    png = src.with_name(src.stem + "-grade-preview.png")
    r = subprocess.run([py, str(HERE / "hdr_grade.py"), str(src), "--preview", "%.1f" % t] + args,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env())
    if r.returncode != 0 or not png.exists():
        return None
    m = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(png), "-vf",
                        "crop=iw/2:ih:iw/2:0,scale=480:270,signalstats,"
                        "metadata=print:key=lavfi.signalstats.YHIGH:file=-",
                        "-frames:v", "1", "-f", "null", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    png.unlink()
    mm = re.search(r"YHIGH=([\d.]+)", m.stdout.decode("utf-8", "replace"))
    return float(mm.group(1)) if mm else None


def auto_grade(src, duration, state):
    """Grade an HLG clip with the first ladder setting whose previews pass the brightness measure."""
    py = venv_python()
    if py is None:
        stop("the video is phone HDR (HLG) and needs a grade, but the plugin's Python with numpy "
             "is missing. Run /video-teach-plugin:setup once, then run this again.")
    dest = OUT / (src.stem + "-graded.mov")
    key = "grade:" + dest.name
    if dest.exists() and dest.stat().st_mtime > src.stat().st_mtime and state["wrote"].get(key):
        note("grade", "reused an earlier grade", dest.name, "setting: %s" % state["wrote"][key])
        print("Grade: reusing %s (%s)" % (dest.name, state["wrote"][key]))
        return dest
    times = [duration * f for f in (0.2, 0.5, 0.8)]
    best = None
    for label, args in LADDER:
        levels = [v for v in (preview_level(py, src, t, args) for t in times) if v is not None]
        if not levels:
            note("grade", "setting failed to preview", label, "hdr_grade.py made no preview")
            continue
        level = sorted(levels)[len(levels) // 2]
        print("Grade try: %s -> brightest 10%% at luma %.0f" % (label, level))
        note("grade", "tried: " + label, "brightest 10%% of the pixels at luma %.0f" % level,
             "passes (limit %.0f)" % GRADE_OK if level <= GRADE_OK else "too bright (limit %.0f)" % GRADE_OK)
        if best is None or level < best[0]:
            best = (level, label, args)
        if level <= GRADE_OK:
            best = (level, label, args)
            break
    if best is None:
        stop("hdr_grade.py produced no preview for %s; cannot grade the HDR clip." % src.name)
    if best[0] >= GATE:
        stop("no grade setting brings the picture under the highlight gate (the darkest try still "
             "reads luma %.0f, limit %.0f). The room is very bright: film with less light behind "
             "you, or pass --allow-bright." % (best[0], GATE))
    if dest.exists():
        dest.unlink()
    print("Grading %s with: %s" % (src.name, best[1]))
    sh([py, HERE / "hdr_grade.py", src] + best[2] + ["-o", dest], quiet=True)
    state["wrote"][key] = best[1]
    state.save()
    note("grade", "graded the HDR clip", dest.name, "setting: %s (luma %.0f)" % (best[1], best[0]))
    return dest


def auto_enhance(src):
    dest = OUT / (src.stem + "_enhanced-audio" + src.suffix)
    if dest.exists() and dest.stat().st_mtime > src.stat().st_mtime:
        note("enhance", "reused an earlier audio-enhance output", dest.name, "newer than its source")
        print("Audio: reusing %s" % dest.name)
        return dest
    rep = OUT / "audio-enhance-report.json"
    print("Audio: running audio-enhance on %s" % src.name)
    sh([PY, ENHANCE, src, dest, "--json", rep, "--quiet"], quiet=True)
    if not dest.exists():
        stop("audio-enhance wrote no %s." % dest.name)
    detail = dest.name
    r = load(rep)
    rec = r[0] if isinstance(r, list) and r else (r or {})
    if isinstance(rec, dict):
        detail += " (loudness %s -> %s LUFS; %s)" % (rec.get("input_i", "?"), rec.get("output_i", "?"),
                                                     rec.get("status", "?"))
        for w in (rec.get("warnings") or [])[:3]:
            detail += " [warning: %s]" % str(w)[:90]
    note("enhance", "ran audio-enhance", detail, "the video was not audio-enhanced yet")
    return dest


# ---------------------------------------------------------------- phase: prepare

def deck_to_pdf(deck, pdf):
    if deck.suffix.lower() == ".pdf":
        shutil.copyfile(deck, pdf)
        note("deck", "PDF given", deck.name, "")
        return deck
    r = subprocess.run([PY, str(HERE / "deck_to_pdf.py"), str(deck), "--out", str(pdf)],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    said = r.stdout.decode("utf-8", "replace").strip()
    print(said)
    if r.returncode == 0:
        note("deck", "exported the .pptx to PDF", deck.name, said.splitlines()[-1][:120] if said else "")
        return deck
    twin = next((q for q in deck.parent.iterdir()
                 if q.suffix.lower() == ".pdf" and q.stem == deck.stem), None)
    if twin is None:
        stop("the deck %s could not be turned into a PDF (%s), and no PDF with the same name is "
             "beside it. Export the deck as PDF (File > Export > PDF), put it next to the .pptx, "
             "and run again." % (deck.name, said[-300:]))
    note("deck", "the .pptx export failed; used the PDF beside the deck", twin.name, said[-200:])
    print("Using the PDF beside the deck: %s" % twin)
    shutil.copyfile(twin, pdf)
    return deck   # keep the .pptx for its speaker notes


def improve_transcript(video, transcript, work, total_label):
    """A whole-file Parakeet pass can drop speech. Build the speech map, measure the empty
    blocks, and when speech was dropped, transcribe in ~20 s windows."""
    m = work / "map-raw.json"
    sh([PY, PC / "speech_map.py", video, transcript, "--out", m], quiet=True)
    rc, out = sh([PY, PC / "measure_blocks.py", m, video], quiet=True, ok=(0, 1))
    if rc == 0:
        note("transcript", "whole-file pass kept", total_label, "measure_blocks.py: no dropped speech")
        return transcript
    better = work / "transcript-windows.json"
    print("Parakeet dropped speech in the whole-file pass; transcribing in windows.")
    sh([PY, PC / "transcribe_windows.py", video, m, "--out", better], quiet=True)
    n0, n1 = len(words_of(transcript)), len(words_of(better))
    note("transcript", "windowed pass", "%d words (whole-file pass: %d)" % (n1, n0),
         "measure_blocks.py found dropped or sparse blocks")
    return better if n1 >= n0 else transcript


def transcribe(video):
    work = OUT / "cut"
    work.mkdir(exist_ok=True)
    raw_t = OUT / "transcript-whole.json"
    sh([PY, TRANSCRIBE, video, "--words", raw_t, "--quiet"])
    n = len(words_of(raw_t))
    if n == 0:
        stop("Parakeet heard no words in %s. Is there speech on the sound track?" % video.name)
    best = improve_transcript(video, raw_t, work, "%d words" % n)
    shutil.copyfile(best, OUT / "transcript.json")
    words = words_of(OUT / "transcript.json")
    print("%d words, the last one ends at %.1f s" % (len(words), words[-1]["end"]))


def prepare_no_deck(folder, state, video, duration, total):
    """No deck: setting A from start to end. The slide steps, the cues and the A/B judgment
    fall away; the chain writes the one-segment timing itself. compose, cut and finish need
    a slides.json, so an empty one stands in (no pages, 16:9 for the unused B geometry)."""
    for f in ("deck-full.pdf", "deck.pdf", "cues.json", "slide-timing.json", "slide-timing-draft.txt"):
        if (OUT / f).exists():
            (OUT / f).unlink()      # left from an earlier run with a deck
    (OUT / "slides.json").write_text(json.dumps(
        {"_comment": "Written by lecture_run.py: no deck, so no slides.",
         "count": 0, "aspect": 16 / 9.0, "pages": []}, indent=1) + "\n")
    note("deck", "no deck", "slide steps skipped", "the whole lecture is setting A")

    step(5, total, "transcript (Parakeet)")
    transcribe(video)

    step(7, total, "timing: setting A from start to end")
    tj = OUT / "timing.json"
    old = load(tj, {}) or {}
    has_b = any(s.get("setting") == "B" for s in old.get("segments", []))   # from a run with a deck
    if state.writable(tj) or not old.get("segments") or has_b:
        rec = {"_comment": "Written by lecture_run.py: no deck, so setting A throughout.",
               "duration": round(duration, 3),
               "segments": [{"start": 0.0, "setting": "A",
                             "decided_by": "no deck", "note": "no slides: the speaker throughout"}]}
        for k in LANDMARKS + ("speaker_x",):     # landmarks the model wrote earlier survive
            if old.get(k) is not None:
                rec[k] = old[k]
        tj.write_text(json.dumps(rec, indent=1) + "\n")
        state.wrote(tj)
    else:
        print("timing.json was edited since this command wrote it; kept (--force overwrites).")
    note("timing", "setting A throughout", "1 segment", "no deck")
    sh([PY, HERE / "build_timing.py", "table", "--timing", tj, "--words", OUT / "transcript.json",
        "--slides", OUT / "slides.json"], capture=OUT / "timing-table.txt")

    step(8, total, "source stills for the framing landmarks")
    sh([PY, HERE / "lecture_compose.py", "--video", video, "--work", OUT, "--timing", tj, "--source-grid"])
    state["prepared"] = True
    print("""
PREPARE DONE (no deck: setting A throughout). No approval stop: the model goes on, now,
with the one judgment call left (SKILL.md "Judgment 1", the framing landmarks): read
speaker_x, speaker_eye_y, speaker_shoulder_y, speaker_shoulder_x0, speaker_shoulder_x1 off
%s/check/source-grid-*.png into %s.
Then: lecture_run.py compose "%s".""" % (OUT, tj, folder))
    return None


def phase_prepare(a, folder, state):
    if state["video_source"] and not (a.video or a.force):
        pass   # a rerun searches again; the log says what it chose
    video = Path(a.video).expanduser().resolve() if a.video else None
    deck = Path(a.deck).expanduser().resolve() if a.deck else None
    if video is None or deck is None:
        vids, decks = find_inputs(folder)
        where = "%s (and raw/)" % folder
        if video is None:
            video = choose("video", vids, where)
            if video is None:
                stop("no talking-head video (.mov .mp4 .m4v .mkv) found in %s. Put the "
                     "recording there." % where)
        if deck is None:
            deck = choose("deck", decks, where)
            if deck is None:
                note("input", "no deck", where, "no .pdf or .pptx: the whole lecture is setting A, "
                     "the speaker with text cards")
                print("No deck found: the whole lecture is setting A (you, with text cards).")
    else:
        note("input", "files given on the command line", "%s; %s" % (video, deck), "explicit")
    if not video.is_file():
        stop("no such video: %s" % video)
    if deck is not None and (not deck.is_file() or deck.suffix.lower() not in (".pptx", ".pdf")):
        stop("the deck must be a .pptx or a .pdf file: %s" % deck)
    state["video_source"] = str(video)
    state["deck"] = str(deck) if deck else None
    state["no_deck"] = deck is None

    total = 8
    step(1, total, "picture and sound")
    transfer, duration, vs = probe(video)
    if transfer == "smpte2084":
        stop("%s is PQ HDR (HDR10 or Dolby Vision), which this chain does not grade. Export it "
             "as SDR (in the phone's settings or the editor) and run again." % video.name)
    if transfer == "arib-std-b67":
        note("grade", "the video is phone HDR (HLG)", video.name, "it needs a grade to Rec. 709")
        video = auto_grade(video, duration, state)
        transfer, duration, vs = probe(video)
    if "enhanced-audio" not in video.name:
        video = auto_enhance(video)
        transfer, duration, vs = probe(video)
    else:
        note("enhance", "the video is already audio-enhanced", video.name, "'_enhanced-audio' in the name")
    level = highlight_level(video, duration, vs.get("pix_fmt", ""))
    if level is not None and level >= GATE and not a.allow_bright:
        stop("the picture is too bright: the brightest 10%% of the pixels sit at luma %.0f of 255 "
             "(video white is 235), so walls and skin highlights are at white. Lower the exposure "
             "for the next recording (lock it on your face), or pass --allow-bright." % level)
    if level is not None:
        print("Highlight gate: brightest 10%% of the pixels at luma %.0f (limit %.0f): ok" % (level, GATE))
        note("gate", "highlight gate passed", "luma %.0f" % level, "limit %.0f" % GATE)
    state["video"] = str(video)
    state["duration"] = duration
    state["fps"] = fps_of(vs)
    print("Video: %s (%dx%d, %.1f s, %s)" % (video.name, vs["width"], vs["height"], duration,
                                            vs.get("r_frame_rate")))

    if deck is None:
        return prepare_no_deck(folder, state, video, duration, total)

    step(2, total, "deck to PDF")
    pdf = OUT / "deck-full.pdf"
    deck = deck_to_pdf(deck, pdf)

    step(3, total, "pages" + (" %s" % a.pages if a.pages else " (all)"))
    use_pdf = OUT / "deck.pdf"
    if a.pages:
        m = re.fullmatch(r"(\d+)-(\d+)", a.pages.strip())
        if not m:
            stop("--pages takes a range like 1-4.")
        first, last = int(m.group(1)), int(m.group(2))
        py = venv_python() or PY
        r = subprocess.run([py, "-c", "import pypdfium2 as p,sys;d=p.PdfDocument(sys.argv[1]);"
                            "n=p.PdfDocument.new();n.import_pages(d,list(range(%d,%d)));n.save(sys.argv[2])"
                            % (first - 1, last), str(pdf), str(use_pdf)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        if r.returncode != 0:
            stop("could not keep pages %s: %s" % (a.pages, r.stdout.decode("utf-8", "replace")[-300:]))
        note("deck", "kept pages", a.pages, "--pages")
    else:
        shutil.copyfile(pdf, use_pdf)

    step(4, total, "slides to images and text")
    cmd = [PY, SLIDES / "slides_prepare.py", use_pdf, "--out", OUT]
    if deck.suffix.lower() == ".pptx" and not a.pages:
        cmd += ["--notes-from", deck]
    sh(cmd)
    slides = load(OUT / "slides.json")

    step(5, total, "transcript (Parakeet)")
    transcribe(video)

    step(6, total, "spoken setting cues (optional)")
    sh([PY, HERE / "find_cues.py", "--words", OUT / "transcript.json", "--out", OUT / "cues.json", "--loose"])
    cues = load(OUT / "cues.json", {}).get("cues", [])
    note("cues", "%d spoken cue(s)" % len(cues),
         "; ".join("%s at %s" % (c["setting"], fmt(c["switch_at"])) for c in cues),
         "cues govern A/B" if cues else "no cues: the model decides A and B")

    step(7, total, "slide timing and A/B timing (drafts)")
    st = OUT / "slide-timing.json"
    if state.writable(st):
        sh([PY, SLIDES / "match_slides.py", "propose", "--slides", OUT / "slides.json", "--words",
            OUT / "transcript.json", "--out", st, "--duration", "%.3f" % duration],
           capture=OUT / "slide-timing-draft.txt")
        state.wrote(st)
    else:
        print("slide-timing.json was edited since this command wrote it; kept (--force overwrites).")
    tj = OUT / "timing.json"
    if state.writable(tj):
        mode = ["--cues", OUT / "cues.json"] if cues else ["--auto"]
        sh([PY, HERE / "build_timing.py", "build", "--slides-timing", st] + mode +
           ["--duration", "%.3f" % duration, "--out", tj])
        state.wrote(tj)
    else:
        print("timing.json was edited since this command wrote it; kept (--force overwrites).")
    sh([PY, HERE / "build_timing.py", "table", "--timing", tj, "--words", OUT / "transcript.json",
        "--slides", OUT / "slides.json"], capture=OUT / "timing-table.txt")

    step(8, total, "source stills for the framing landmarks")
    sh([PY, HERE / "lecture_compose.py", "--video", video, "--work", OUT, "--timing", tj, "--source-grid"])
    state["prepared"] = True
    print("""
PREPARE DONE. No approval stop: the model goes on, now, with the three judgment calls
(SKILL.md "Judgment 1"), each with its reason written into the file:
  1. slide timing: read %s against %s and
     correct %s (a "note" on every change), then rebuild timing.json:
       build_timing.py build --slides-timing ... %s --out %s
  2. A and B: %s
  3. framing landmarks: read speaker_x, speaker_eye_y, speaker_shoulder_y,
     speaker_shoulder_x0, speaker_shoulder_x1 off %s/check/source-grid-*.png into timing.json.
Then: lecture_run.py compose "%s".""" % (
        OUT / "transcript.json", OUT / "slides.json", st,
        "--cues cues.json" if cues else "--auto", tj,
        ("the spoken cues govern (%d found); check them in cues.json" % len(cues)) if cues else
        "no spoken cues; decide every change with the rules, writing \"decided_by\" and \"note\"",
        OUT, folder))
    return slides


# ---------------------------------------------------------------- phase: compose

LANDMARKS = ("speaker_eye_y", "speaker_shoulder_y", "speaker_shoulder_x0", "speaker_shoulder_x1")


def phase_compose(a, folder, state):
    if not state["prepared"]:
        stop("run the prepare phase first: lecture_run.py prepare \"%s\"" % folder)
    video = Path(state["video"])
    tj = OUT / "timing.json"
    timing = load(tj)
    if not timing:
        stop("timing.json is missing or not valid JSON.")
    missing = [k for k in LANDMARKS if timing.get(k) is None]
    if missing:
        stop("timing.json has no framing landmarks (%s). Read them off %s/check/source-grid-*.png "
             "(fractions of the source frame) and write them into timing.json." % (", ".join(missing), OUT))
    judged = [s for s in timing["segments"] if str(s.get("decided_by", "")).startswith("judgment")]
    note("timing", "A/B timing used", "%d segments, %d by judgment" % (len(timing["segments"]), len(judged)),
         "cues" if load(OUT / "cues.json", {}).get("cues") else "no cues")
    for k in LANDMARKS + ("speaker_x",):
        note("framing", k, timing.get(k), "read off the source grid stills")

    total = 3
    step(1, total, "preview stills (setting A with guides, setting B)")
    rc, out = sh([PY, HERE / "lecture_compose.py", "--video", video, "--work", OUT, "--timing", tj,
                  "--preview", "--guides"], capture=OUT / "preview-report.txt")
    warns = [l[len("WARNING: "):] for l in out.splitlines() if l.startswith("WARNING: ")]
    for w in warns:
        note("framing", "WARNING", w, "the framing rule wins; the render goes on")

    step(2, total, "the A/B composite")
    comp = OUT / (folder.name + "-composite.mp4")
    sh([PY, HERE / "lecture_compose.py", "--video", video, "--work", OUT, "--timing", tj, "--out", comp])
    state["composite"] = str(comp)
    note("composite", "rendered", comp.name, "length checked against the source")

    step(3, total, "speech map of the composite, for the editorial pass")
    work = OUT / "cut"
    work.mkdir(exist_ok=True)
    m = work / "map.json"
    sh([PY, PC / "speech_map.py", comp, OUT / "transcript.json", "--out", m], quiet=True)
    smap = load(m)
    cues = load(OUT / "cues.json", {}).get("cues", [])
    lines = ["# Blocks of %s (seconds on the composite's clock = the source clock)." % comp.name,
             "# i  start  end  gap_after  split  text"]
    for b in smap["blocks"]:
        cue = any(c["start"] - 0.1 <= (b["start"] + b["end"]) / 2 <= c["end"] + 0.1 for c in cues)
        lines.append("%d  %.2f  %.2f  %.2f  %s  %s%s" % (
            b["i"], b["start"], b["end"], b.get("gap_after") or 0, b.get("split", "-"),
            b.get("text", ""), "   [spoken cue: cut automatically]" if cue else ""))
    (work / "blocks.txt").write_text("\n".join(lines) + "\n")
    rc, mout = sh([PY, PC / "measure_blocks.py", m, comp], quiet=True, ok=(0, 1),
                  capture=work / "measure-blocks.txt")
    state["composed"] = True
    print("""
COMPOSE DONE. Look at the stills in %s/check/ (preview-*-A.png with the guides, -B.png).
Next, without asking (SKILL.md "Judgment 2"): the editorial pass over %s
(measured empty blocks: %s), written as %s:
  {"cut_ranges": [[start_s, end_s, "reason"], ...], "trim_starts": [...]}
The spoken cues are cut automatically. Then: lecture_run.py cut "%s".""" % (
        OUT, work / "blocks.txt", work / "measure-blocks.txt", work / "decisions.json", folder))


# ---------------------------------------------------------------- phase: cut

def phase_cut(a, folder, state):
    if not state["composed"]:
        stop("run the compose phase first: lecture_run.py compose \"%s\"" % folder)
    comp = Path(state["composite"])
    work = OUT / "cut"
    dec = work / "decisions.json"
    if not dec.exists():
        stop("%s is missing: the editorial pass writes it (an empty {} when nothing needs cutting "
             "beyond the automatic rules)." % dec)
    if load(dec) is None:
        stop("%s is not valid JSON." % dec)
    total = 5
    step(1, total, "refine_cuts.py: the decisions, the automatic rules, the spoken cues")
    cmd = [PY, PC / "refine_cuts.py", "--video", comp, "--map", work / "map.json",
           "--words", OUT / "transcript.json", "--decisions", dec, "--out-dir", work,
           "--sequence-name", folder.name + " lecture"]
    if load(OUT / "cues.json", {}).get("cues"):
        cmd += ["--cues", OUT / "cues.json"]
    sh(cmd, quiet=True, capture=work / "refine-report.txt")
    note("cut", "refine_cuts.py", (work / "refine-report.txt").read_text().splitlines()[0][:150],
         "decisions.json + automatic rules")

    step(2, total, "the perfect-cuts package")
    pkg = folder / ("%s perfect cut (C)" % folder.name)
    rc, out = sh([PY, PC / "build_package.py", "--source", comp, "--cuts", work / "cuts.json",
                  "--out", pkg, "--title", folder.name + " lecture"], quiet=True)
    summary = json.loads(out[out.index("{"):]) if "{" in out else {}
    mp4 = Path(summary.get("files", {}).get("mp4", pkg / "1 WATCH - final video (C).mp4"))
    if not mp4.exists():
        stop("the perfect-cuts package has no MP4: %s" % out[-300:])
    for w in summary.get("warnings", []):
        note("cut", "build warning", w, "")
    state["cut_mp4"] = str(mp4)
    state["package"] = str(pkg)
    note("cut", "package built", pkg.name, "%s s of %s s kept" % (summary.get("expected_duration"),
                                                                 summary.get("source_duration")))

    step(3, total, "sound gate on the cut")
    rc, gout = sh([PY, MV / "aqc_gate.py", mp4, "--cleaned"], quiet=True, ok=(0, 2, 3),
                  capture=work / "sound-gate.txt")
    first = next((l for l in gout.splitlines() if l.strip()), "")
    if rc == 2:
        stop("the sound check failed on the cut (loudness or true peak): %s" % first)
    if rc == 3:
        stop("the sound check could not find its checks (a plugin fault): %s" % first)
    note("gate", "sound check", first, "")

    step(4, total, "settings across the cuts, checked on the picture")
    sh([PY, HERE / "map_settings.py", "--settings", OUT / "settings.json", "--cuts",
        pkg / "cut data (C).json", "--out", OUT / "settings-final.json", "--final", mp4])
    note("settings", "mapped and checked", "settings-final.json", "every segment matches the picture")

    step(5, total, "the words of the finished cut")
    cards_dir = folder / "text-cards"
    cards_dir.mkdir(exist_ok=True)
    sh([PY, TT / "place_cards.py", "words", mp4, "--out", cards_dir / "timeline-words.json"], quiet=True)
    sf = load(OUT / "settings-final.json")
    words = (load(cards_dir / "timeline-words.json", {}) or {}).get("words", [])
    lines = ["# Setting A on the finished video's clock: the only places a card may stand.",
             "# Setting B (slides + insert) takes no card. Words are copied from timeline-words.json."]
    for s in sf["segments"]:
        if s["setting"] != "A":
            lines.append("\n[B %s-%s, slide %s: no cards]" % (fmt(s["t0"]), fmt(s["t1"]), s["slide"]))
            continue
        said = " ".join(w.get("word", "").strip() for w in words
                        if s["t0"] - 0.05 <= w["start"] < s["t1"] - 0.15)
        lines.append("\n[A %s-%s, %.1f s]\n%s" % (fmt(s["t0"]), fmt(s["t1"]), s["t1"] - s["t0"], said))
    (cards_dir / "setting-a-words.txt").write_text("\n".join(lines) + "\n")
    state["cut_done"] = True
    print("""
CUT DONE. %s is the cut lecture (no text yet).
Next, without asking (SKILL.md "Judgment 3"): write the text cards for setting A only,
from %s, into %s. Then: lecture_run.py finish "%s".""" % (
        mp4, cards_dir / "setting-a-words.txt", cards_dir / "cards.json", folder))


# ---------------------------------------------------------------- phase: finish

def lecture_look():
    """The user's saved look, turned into the look of setting A: the font, the text colour,
    the plate colour and opacity come from setup; the place is the free column of setting A."""
    saved = config().get("look") or {}
    look = {
        "layout": "column",
        "free_area": dict(LECTURE_FREE_AREA),
        "plate": False,
        "plate_asked": bool(saved.get("plate")),
        "plate_color": saved.get("plate_color") or "#202020",
        "plate_opacity": float(saved.get("plate_opacity") or 0.7),
        "text_color": saved.get("text_color") or "#FFFFFF",
        "font": saved.get("font") or "Inter",
    }
    if saved.get("font_file"):
        look["font_file"] = saved["font_file"]
    return look, bool(saved)


def alpha_max(mov, t):
    """The largest alpha value (0-255) of the frame at t. ProRes 4444 carries 12-bit alpha,
    and signalstats on it reports 256 for a transparent frame, so convert to 8-bit first."""
    r = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", "%.3f" % t, "-i", str(mov),
                        "-frames:v", "1", "-vf", "alphaextract,format=gray,signalstats,"
                        "metadata=print:key=lavfi.signalstats.YMAX:file=-", "-f", "null", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    m = re.search(r"YMAX=([\d.]+)", r.stdout.decode("utf-8", "replace"))
    return float(m.group(1)) if m else None


def still(video, t, dest):
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", "%.3f" % t, "-i", str(video),
                    "-frames:v", "1", str(dest)])
    return dest


def phase_finish(a, folder, state):
    if not state["cut_done"]:
        stop("run the cut phase first: lecture_run.py cut \"%s\"" % folder)
    mp4 = Path(state["cut_mp4"])
    cards_dir = folder / "text-cards"
    cards = load(cards_dir / "cards.json")
    if cards is None:
        stop("%s is missing or not valid JSON: the card pass writes it ({\"cards\": []} for a "
             "lecture with no card)." % (cards_dir / "cards.json"))
    _, dur, vs = probe(mp4)
    fps = fps_of(vs)
    sf = load(OUT / "settings-final.json")
    final = folder / ("%s-finished-with-text.mp4" % folder.name)
    overlay = folder / ("%s-text-overlay.mov" % folder.name)
    check = OUT / "check"
    check.mkdir(exist_ok=True)
    total = 7

    step(1, total, "place the cards (never over setting B)")
    look, had_look = lecture_look()
    note("look", "card look", "%s, %s text%s" % (look["font"], look["text_color"],
                                                 ", plate asked for" if look["plate_asked"] else ""),
         "the saved look from setup" if had_look else "no saved look: the plugin's defaults")
    (OUT / "lecture-look.json").write_text(json.dumps(look, indent=1) + "\n")
    placed = []
    if (cards.get("cards") if isinstance(cards, dict) else cards):
        sh([PY, TT / "place_cards.py", "place", "--cards", cards_dir / "cards.json", "--words",
            cards_dir / "timeline-words.json", "--blackout", OUT / "settings-final.json",
            "--out", cards_dir / "cards-timed.json", "--fps", "%.6f" % fps,
            "--timeline-props", cards_dir / "overlay-props.json"],
           capture=cards_dir / "placement-table.txt")
        timed = load(cards_dir / "cards-timed.json")
        placed = [c for c in timed["cards"] if c.get("start") is not None]
        for c in timed["cards"]:
            label = c.get("headline") or c.get("quote") or c.get("name") or c.get("signOff") or c["kind"]
            notes = "; ".join(c.get("place", {}).get("notes", []))
            note("cards", "placed" if c.get("start") is not None else "SKIPPED", "%s %s" % (
                fmt(c["start"]) if c.get("start") is not None else "-", str(label)[:60]), notes)
    if not placed:
        note("cards", "no card placed", "", "the finished video is the cut without text")
        shutil.copyfile(mp4, final)
        step(6, total, "no card to draw: the finished video is the cut")
    else:
        step(2, total, "plate decision from the picture behind the cards")
        sh([PY, HERE / "plate_check.py", "--final", mp4, "--settings", OUT / "settings-final.json",
            "--look", OUT / "lecture-look.json", "--cards", cards_dir / "cards-timed.json",
            "--out", OUT / "plate-decision.json", "--patch-props", cards_dir / "overlay-props.json"])
        pd = load(OUT / "plate-decision.json")
        note("plate", "plate" if pd["plate"] else "no plate", "%s %s" % (pd.get("plate_color"),
             pd.get("plate_opacity") or ""), pd.get("why", ""))

        step(3, total, "render the full-length transparent overlay")
        rc, out = sh(["node", TT / "render.mjs", "--cards", cards_dir / "overlay-props.json",
                      "--timeline", "--look", OUT / "lecture-look.json", "--out", cards_dir,
                      "--footage", mp4, "--modes", "alpha", "--name", "text-overlay"], quiet=True)
        made = cards_dir / "text-overlay-alpha.mov"
        if not made.exists() or made.stat().st_size == 0:
            stop("the overlay render wrote nothing: %s" % out[-400:])
        shutil.move(str(made), str(overlay))
        note("overlay", "rendered", overlay.name, "%.0f MB" % (overlay.stat().st_size / 1e6))

        step(4, total, "check the overlay: its length, no alpha inside setting B, every card visible")
        _, odur, _ = probe(overlay)
        if abs(odur - dur) > 1.5 / fps:
            stop("the overlay runs %.3f s, the cut %.3f s." % (odur, dur))
        for b0, b1 in sf["b_intervals"]:
            for t in (b0 + 0.1, (b0 + b1) / 2, b1 - 0.1):
                if t <= b0 or t >= b1:
                    continue
                v = alpha_max(overlay, t)
                if v is None or v > 0:
                    stop("the overlay is not transparent inside setting B at %s (alpha max %s)." % (fmt(t), v))
        note("overlay", "alpha 0 inside every setting B", "%d interval(s)" % len(sf["b_intervals"]), "")
        for i, c in enumerate(placed, 1):
            t = c["start"] + min(4.0, 0.6 * c["dur"])
            v = alpha_max(overlay, t)
            if not v:
                stop("card %d (%s) is not visible in the overlay at %s." % (i, c["kind"], fmt(t)))

        step(5, total, "the finished video with the text baked in")
        tmp = final.with_name(final.stem + ".part.mp4")
        sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", mp4, "-i", overlay, "-filter_complex",
            "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]", "-map", "[v]", "-map", "0:a:0",
            "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-color_range", "tv",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "copy", "-movflags", "+faststart", tmp], quiet=True)
        os.replace(tmp, final)
        for i, c in enumerate(placed, 1):
            still(final, c["start"] + min(4.0, 0.6 * c["dur"]), check / ("final-card-%02d.png" % i))

    step(6, total, "checks and the timing log")
    _, fdur, _ = probe(final)
    if abs(fdur - dur) > 1.5 / fps:
        stop("the finished video runs %.3f s, the cut %.3f s." % (fdur, dur))
    for tag in ("A", "B"):
        seg = next((s for s in sf["segments"] if s["setting"] == tag), None)
        if seg is not None:
            still(final, (seg["t0"] + seg["t1"]) / 2, check / ("final-%s.png" % tag))
    sh([PY, HERE / "build_timing.py", "log", "--timing", OUT / "timing.json", "--words",
        OUT / "transcript.json", "--slides", OUT / "slides.json", "--final", OUT / "settings-final.json",
        "--out", OUT / "timing-log.csv"])
    note("done", "finished video", final.name, "")
    state["finished"] = str(final)

    step(7, total, "captions and transcript of the finished video")
    captions(folder, final, state)
    write_runlog()
    sh([PY, HERE / "lecture_summary.py", "--folder", folder], capture=OUT / "summary.txt")
    desc = folder / "course-page-description.txt"
    print("""
FINISH DONE. No approval stop: the model goes on, now, with the last judgment call
(SKILL.md "Judgment 4"):
  1. names and terms: correct their spelling in captions.srt and transcript.txt in %s,
     against the slide text (%s) and the beat sheet if there is one; words only, never a timing.
  2. %s
Then the closing message (the summary above, shortened, plus the corrections).""" % (
        folder, OUT / "slides.json",
        ("write %s from %s and transcript.txt." % (desc, OUT / "captions-work" / "outline.txt"))
        if state.writable(desc) else
        ("%s was edited by hand since the chain wrote it: leave it, and say so." % desc.name)))


def captions(folder, final, state):
    """captions.srt, transcript.txt and the outline for the description, from the finished
    video (its sound is the cut's, so the captions match what students see). A file the user
    edited since the chain wrote it is kept; the new one is left in lecture/captions-work/."""
    work = OUT / "captions-work"
    work.mkdir(exist_ok=True)
    rc, out = sh([PY, CAP / "caption_words.py", final, "--workdir", work, "--out", work / "words.json"],
                 quiet=True)
    info = load_last_json(out)
    detail = "%s words" % info.get("words_final", "?")
    if info.get("words_pass1") not in (None, info.get("words_final")):
        detail += " (first pass %s)" % info["words_pass1"]
    dropped = info.get("still_dropped") or []
    if dropped:
        detail += "; still missing words near %s" % ", ".join(
            fmt(d.get("start", 0)) for d in dropped[:6])
    note("captions", "transcribed the finished video", detail, "Parakeet; windowed pass when speech was dropped")
    sh([PY, CAP / "make_captions.py", work / "words.json", "--outdir", work, "--outline",
        work / "outline.txt"], quiet=True)
    for name in ("captions.srt", "transcript.txt"):
        dest = folder / name
        if state.writable(dest):
            shutil.copyfile(work / name, dest)
            state.wrote(dest)
            note("captions", "wrote " + name, dest, "")
        else:
            note("captions", "kept " + name, "the new one is in %s" % work,
                 "edited by hand since the chain wrote it")
    print("Captions: %s; %s" % (detail, ", ".join(n for n in ("captions.srt", "transcript.txt"))))


def load_last_json(text):
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {}


# ---------------------------------------------------------------- next

def phase_next(folder, state):
    order = [("prepared", "prepare"), ("composed", "compose"), ("cut_done", "cut"), ("finished", "finish")]
    stopf = OUT / "stop-reason.txt"
    if stopf.exists():
        print("The last run stopped: " + stopf.read_text().strip())
    for key, name in order:
        if not state[key]:
            print("Next phase: %s" % name)
            need = {"compose": OUT / "timing.json", "cut": OUT / "cut" / "decisions.json",
                    "finish": folder / "text-cards" / "cards.json"}.get(name)
            if need is not None:
                print("  it needs %s (%s)" % (need, "present" if need.exists() else "MISSING"))
            return
    print("All four phases are done: %s" % state["finished"])


def main():
    global OUT, PHASE
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("phase", choices=("prepare", "compose", "cut", "finish", "next"))
    ap.add_argument("folder", help="the video folder")
    ap.add_argument("--video", default=None, help="the talking-head video (overrides the search)")
    ap.add_argument("--deck", default=None, help="the deck, .pdf or .pptx (overrides the search)")
    ap.add_argument("--pages", default=None, help="prepare: keep only these deck pages, e.g. 1-4")
    ap.add_argument("--allow-bright", action="store_true", help="prepare: skip the highlight gate")
    ap.add_argument("--force", action="store_true",
                    help="prepare: overwrite slide-timing.json / timing.json even if edited")
    a = ap.parse_args()
    PHASE = a.phase
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit("ERROR: %s is not installed. Run /video-teach-plugin:setup." % tool)
    if not TV_DATA:
        sys.exit("ERROR: TV_DATA is not set. Pass TV_DATA=\"${CLAUDE_PLUGIN_DATA}\" on the command.")
    folder = Path(a.folder).expanduser().resolve()
    if not folder.is_dir():
        sys.exit("ERROR: no such folder: %s" % folder)
    OUT = folder / "lecture"
    OUT.mkdir(exist_ok=True)
    state = State(OUT, a.force)
    if a.phase == "next":
        phase_next(folder, state)
        return
    stopf = OUT / "stop-reason.txt"
    if stopf.exists():
        stopf.unlink()
    {"prepare": phase_prepare, "compose": phase_compose, "cut": phase_cut,
     "finish": phase_finish}[a.phase](a, folder, state)
    write_runlog()


if __name__ == "__main__":
    main()
