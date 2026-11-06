#!/usr/bin/env python3
"""Carry the A/B settings through perfect-cuts onto the final timeline.

Usage:
    python3 map_settings.py --settings WORK/settings.json \
        --cuts "cut data (C).json" --out LECTURE/settings-final.json \
        [--final FINAL.mp4 --background PLATE.png]

lecture_compose.py wrote settings.json on the composite's clock (before the
cut). perfect-cuts then removed retakes, fillers, the spoken edit cues and
the dead air. `cut data (C).json` lists the clips that survived (in_frame,
out_frame on the composite). This script puts every setting segment through
those clips and writes settings-final.json on the FINAL video's clock:

    {"fps": "60/1", "duration": 1834.5,
     "segments": [{"t0": 0, "t1": 41.2, "setting": "A"},
                  {"t0": 41.2, "t1": 96.0, "setting": "B", "slide": 1}, ...],
     "b_intervals": [[41.2, 96.0], ...]}

text-treatments reads b_intervals and puts no card on them.

--final FINAL.mp4 also checks the result against the real picture: it reads
the strip along the top edge of the frame (the plate background in setting B,
above the slide and its shadow; the speaker video in setting A), shrunk to 32
colour samples, at the start, middle and end of every segment, and reports
each moment whose picture is not the setting it should be. --background must
be the plate the composite was made with (lecture_compose.py records it in
settings.json; the bundled plate is the fallback).
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
STRIP = (0, 4, 1920, 40)    # x, y, w, h: above the slide (top gap 54 px, shadow from ~y 79),
                             # the same for a right or a left insert
SAMPLES = 32
MAX_DIFF = 10.0              # mean absolute difference (0-255) that still counts as the plate

os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + "/opt/homebrew/bin"


def fmt(t):
    m, s = divmod(max(t, 0.0), 60)
    return "%d:%05.2f" % (m, s)


def map_segments(settings, cuts):
    fps = Fraction(settings["fps"])
    cfps = Fraction(str(cuts["fps"])) if not isinstance(cuts["fps"], str) else Fraction(cuts["fps"])
    if abs(float(cfps) - float(fps)) > 0.01:
        sys.exit("ERROR: the composite runs at %s fps but the cuts use %s fps." % (fps, cfps))
    out, offset = [], 0
    clips = cuts["clips"]
    for c in clips:
        cin, cout = int(c["in_frame"]), int(c["out_frame"])
        for s in settings["segments"]:
            lo, hi = max(cin, s["f0"]), min(cout, s["f1"])
            if hi <= lo:
                continue
            f0, f1 = offset + (lo - cin), offset + (hi - cin)
            rec = {"f0": f0, "f1": f1, "setting": s["setting"], "slide": s["slide"]}
            last = out[-1] if out else None
            if last and last["f1"] == f0 and last["setting"] == rec["setting"] \
                    and last["slide"] == rec["slide"]:
                last["f1"] = f1
            else:
                out.append(rec)
        offset += cout - cin
    total = offset
    # a setting change that sits on a cut leaves no gap and no overlap; make sure
    for a, b in zip(out, out[1:]):
        if a["f1"] != b["f0"]:
            sys.exit("ERROR: the mapped segments do not meet at frame %d." % a["f1"])
    segs = [{"t0": round(float(Fraction(s["f0"]) / fps), 4),
             "t1": round(float(Fraction(s["f1"]) / fps), 4),
             "setting": s["setting"], "slide": s["slide"]} for s in out]
    # join A or B stretches that a cut split in two (slide changes stay separate)
    merged = []
    for s in segs:
        if merged and merged[-1]["setting"] == s["setting"] == "A":
            merged[-1]["t1"] = s["t1"]
        else:
            merged.append(s)
    return merged, float(Fraction(total) / fps), str(fps)


def b_intervals(segs):
    out = []
    for s in segs:
        if s["setting"] != "B":
            continue
        if out and abs(out[-1][1] - s["t0"]) < 1e-6:
            out[-1][1] = s["t1"]
        else:
            out.append([s["t0"], s["t1"]])
    return out


# ---------------------------------------------------------------- check

def strip_rgb(cmd_in, vf):
    r = subprocess.run(cmd_in + ["-vf", vf, "-frames:v", "1", "-f", "rawvideo",
                                 "-pix_fmt", "rgb24", "-"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return tuple(r.stdout[:SAMPLES * 3]) if len(r.stdout) >= SAMPLES * 3 else None


def check(segs, final, background):
    ff = shutil.which("ffmpeg")
    x, y, w, h = STRIP
    shrink = "crop=%d:%d:%d:%d,scale=%d:1:flags=area" % (w, h, x, y, SAMPLES)
    if background.startswith("#"):
        src = [ff, "-v", "error", "-f", "lavfi", "-i", "color=c=0x%s:s=%dx%d" % (background[1:], W, H)]
    else:
        src = [ff, "-v", "error", "-i", background]
    ref = strip_rgb(src, "scale=%d:%d:force_original_aspect_ratio=increase,crop=%d:%d,%s"
                    % (W, H, W, H, shrink))
    if ref is None:
        sys.exit("ERROR: cannot read the background %s" % background)
    bad = []
    for s in segs:
        span = s["t1"] - s["t0"]
        probes = [s["t0"] + span / 2]
        if span > 0.8:
            probes = [s["t0"] + 0.15, s["t0"] + span / 2, s["t1"] - 0.15]
        for t in probes:
            got = strip_rgb([ff, "-v", "error", "-ss", "%.3f" % t, "-i", final], shrink)
            if got is None:
                bad.append((t, s["setting"], "could not read the frame"))
                continue
            diff = sum(abs(a - b) for a, b in zip(got, ref)) / float(len(ref))
            is_plate = diff <= MAX_DIFF
            if is_plate != (s["setting"] == "B"):
                bad.append((t, s["setting"], "picture looks like setting %s"
                            % ("B" if is_plate else "A")))
    return bad


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--settings", required=True)
    ap.add_argument("--cuts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--final", default=None)
    ap.add_argument("--background", default=None,
                    help="The plate image (default: the one in the skill's assets)")
    a = ap.parse_args()
    settings = json.loads(Path(a.settings).expanduser().read_text())
    cuts = json.loads(Path(a.cuts).expanduser().read_text())
    segs, duration, fps = map_segments(settings, cuts)
    result = {
        "_comment": "Written by map_settings.py. text-treatments puts no card on "
                    "b_intervals (setting B has slides and the speaker insert).",
        "fps": fps, "duration": round(duration, 4), "segments": segs,
        "b_intervals": b_intervals(segs),
    }
    Path(a.out).expanduser().write_text(json.dumps(result, indent=1) + "\n")
    print("%-3s %-9s %-9s %-8s %s" % ("#", "from", "to", "setting", "slide"))
    for i, s in enumerate(segs, 1):
        print("%-3d %-9s %-9s %-8s %s" % (i, fmt(s["t0"]), fmt(s["t1"]),
                                         s["setting"], s["slide"] or ""))
    b = sum(y - x for x, y in result["b_intervals"])
    print("\nFinal length %s; setting B %s (%.0f%%). -> %s" % (
        fmt(duration), fmt(b), 100 * b / duration if duration else 0, a.out))
    if a.final:
        bg = a.background or settings.get("background") or str(
            Path(__file__).resolve().parents[1] / "assets" / "background-plate.png")
        bad = check(segs, str(Path(a.final).expanduser()), bg)
        if bad:
            for t, st, why in bad:
                print("MISMATCH at %s (should be %s): %s" % (fmt(t), st, why))
            sys.exit("ERROR: %d moment(s) do not match the mapped settings." % len(bad))
        print("Checked the picture of %s at every segment: all match." % a.final)


if __name__ == "__main__":
    main()
