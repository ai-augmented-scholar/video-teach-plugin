#!/usr/bin/env python3
"""Decide whether the text cards of a lecture need a plate, from the picture.

Usage:
    python3 plate_check.py --final FINAL.mp4 --settings settings-final.json \
        --look lecture-look.json [--cards cards-timed.json] [--out plate-decision.json] \
        [--patch-props overlay-props.json] [--region x,y,w,h]

The cards are white type in the plugin's neutral design system, in the
column of setting A that the lecture look marks as free (`free_area` of
lecture-look.json, the left part of the frame). White type needs a dark
enough, even enough picture behind it. This script measures that picture
where the cards will stand, in setting A only (setting B takes no cards), and
chooses one look for the whole lecture: no plate, or a plate in the user's
plate colour (look.plate_color, default #202020 dark grey) behind the text.

THE RULE. The region is the card column (free_area, in px of the 1920x1080
frame; --region overrides it). Each sampled frame is scaled down and every
pixel turned into relative luminance L (WCAG). White has L 1, so its contrast
against a pixel is 1.05 / (L + 0.05). A frame FAILS when

    the brightest 10% of its pixels (P90) give white less than 4.5 : 1, or
    the brightest 1% (P99) give white less than 3 : 1.

P90 catches a bright or uneven picture (a window, a white wall, a poster
behind part of the column: some word will cross it). P99 catches a hot spot
that large type cannot survive. 4.5:1 is the WCAG bar for normal text; the
type is 24 px and up, where 3:1 would do, so 4.5 is deliberately strict for
the P90 and 3 applies only to the extreme tail.

The whole lecture gets a plate when 10% or more of the sampled frames fail,
or when the user's saved look already asks for one (look.plate true). One
look per video: cards with and without a plate in one lecture look like two
styles.

SAMPLES. With --cards (cards-timed.json from place_cards.py), three frames
inside every placed card (so the cards that will exist decide). Without, a
frame every 8 s through setting A, at most 120.

The plate opacity is the smallest of the look's plate_opacity (default 70%)
and the steps above it (75 ... 95%) at which white type holds 4.5:1 over the
bright end (the 95th percentile across frames of the P99 pixel) after the
plate is laid over it. The decision is written into lecture-look.json (plate,
plate_opacity) and, with --patch-props, into the overlay props' "look", so the
renderer draws exactly what was measured.

Prints a plain summary and writes the decision as JSON. Stdlib only.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + "/opt/homebrew/bin"

W, H = 1920, 1080
NEED_P90, NEED_P99 = 4.5, 3.0
FAIL_FRACTION = 0.10
MAX_ALPHA = 0.95
STEP, MAX_SAMPLES = 8.0, 120


def hex_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c + c for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def region_of(look):
    a = look.get("free_area") or {"x": 0.03, "y": 0.30, "w": 0.36, "h": 0.58}
    x, y = int(round(a["x"] * W)), int(round(a["y"] * H))
    w, h = int(round(a["w"] * W)), int(round(a["h"] * H))
    return (x - x % 2, y - y % 2, w - w % 2, h - h % 2)


def lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lum(r, g, b):
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def gray_for(L):
    """The sRGB grey (0-255) whose luminance is L."""
    v = 12.92 * L if L <= 0.0031308 else 1.055 * (L ** (1 / 2.4)) - 0.055
    return 255.0 * min(1.0, max(0.0, v))


def contrast_white(L):
    return 1.05 / (L + 0.05)


def pct(sorted_vals, p):
    i = min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def fmt(t):
    m, s = divmod(t, 60)
    return "%d:%04.1f" % (m, s)


def measure(ffmpeg, video, t, region):
    x, y, w, h = region
    GW, GH = max(8, w // 8), max(8, h // 8)
    r = subprocess.run(
        [ffmpeg, "-nostdin", "-v", "error", "-ss", "%.3f" % t, "-i", str(video),
         "-frames:v", "1", "-vf", "crop=%d:%d:%d:%d,scale=%d:%d:flags=area" % (w, h, x, y, GW, GH),
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    data = r.stdout
    if len(data) < GW * GH * 3:
        return None
    ls = sorted(lum(data[i], data[i + 1], data[i + 2]) for i in range(0, GW * GH * 3, 3))
    return {"t": t, "p10": pct(ls, 0.10), "p50": pct(ls, 0.50),
            "p90": pct(ls, 0.90), "p99": pct(ls, 0.99)}


def sample_times(settings, cards, duration):
    times = []
    if cards:
        for c in cards:
            if c.get("start") is None:
                continue
            a, b = float(c["start"]), float(c["start"]) + float(c["dur"])
            if b - a < 3.0:
                times.append((a + b) / 2)
            else:
                times += [a + 0.8, (a + b) / 2, b - 0.8]
    else:
        for s in settings["segments"]:
            if s["setting"] != "A":
                continue
            t = s["t0"] + 1.0
            while t < s["t1"] - 1.0:
                times.append(t)
                t += STEP
            if s["t1"] - s["t0"] <= 2.0:
                times.append((s["t0"] + s["t1"]) / 2)
    times = [t for t in times if 0 <= t < duration - 0.05]
    if len(times) > MAX_SAMPLES:
        step = len(times) / float(MAX_SAMPLES)
        times = [times[int(i * step)] for i in range(MAX_SAMPLES)]
    return times


def plate_over(g, alpha, color):
    """Luminance of the plate laid over a neutral grey pixel g (0-255)."""
    return lum(*[alpha * c + (1 - alpha) * g for c in color])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--final", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--look", required=True,
                    help="lecture-look.json (the neutral look for setting A); the decision is written into it")
    ap.add_argument("--cards", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--patch-props", default=None)
    ap.add_argument("--region", default=None, help="x,y,w,h on the 1920x1080 frame")
    a = ap.parse_args()

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ERROR: ffmpeg is not installed.")
    look_path = Path(a.look).expanduser()
    look = json.loads(look_path.read_text())
    color = hex_rgb(look.get("plate_color") or "#202020")
    base_alpha = float(look.get("plate_opacity") or 0.7)
    alphas = [round(base_alpha + 0.05 * k, 2) for k in range(0, 10)
              if base_alpha + 0.05 * k <= MAX_ALPHA + 1e-6] or [MAX_ALPHA]
    asked = bool(look.get("plate_asked"))
    region = tuple(int(v) for v in a.region.split(",")) if a.region else region_of(look)
    settings = json.loads(Path(a.settings).expanduser().read_text())
    cards = None
    if a.cards:
        cards = json.loads(Path(a.cards).expanduser().read_text())["cards"]
    duration = float(settings["duration"])
    times = sample_times(settings, cards, duration)
    if not times:
        sys.exit("ERROR: no setting-A moment to sample (no card placed, or the lecture "
                 "has no setting A).")

    rows = [m for m in (measure(ffmpeg, a.final, t, region) for t in times) if m]
    if not rows:
        sys.exit("ERROR: could not read any frame of %s" % a.final)
    for m in rows:
        m["c90"] = contrast_white(m["p90"])
        m["c99"] = contrast_white(m["p99"])
        m["fail"] = m["c90"] < NEED_P90 or m["c99"] < NEED_P99
    fails = [m for m in rows if m["fail"]]
    frac = len(fails) / float(len(rows))
    plate = frac >= FAIL_FRACTION or asked

    p99s = sorted(m["p99"] for m in rows)
    ref = pct(p99s, 0.95)
    g_ref = gray_for(ref)
    alpha = None
    if plate:
        for al in alphas:
            if contrast_white(plate_over(g_ref, al, color)) >= NEED_P90:
                alpha = al
                break
        if alpha is None:
            alpha = alphas[-1]
    worst = min(rows, key=lambda m: min(m["c90"], m["c99"] * 1.5))
    result = {
        "plate": plate,
        "why": ("the saved look asks for a plate" if asked and frac < FAIL_FRACTION else
                "%d%% of the sampled frames fail white-type contrast (limit %d%%)"
                % (round(100 * frac), int(FAIL_FRACTION * 100))),
        "plate_color": "#%02x%02x%02x" % color,
        "plate_opacity": alpha,
        "region": list(region),
        "samples": len(rows),
        "failing_samples": len(fails),
        "fail_fraction": round(frac, 3),
        "median_luminance": round(sorted(m["p50"] for m in rows)[len(rows) // 2], 3),
        "worst": {"t": round(worst["t"], 2), "white_vs_p90": round(worst["c90"], 2),
                  "white_vs_p99": round(worst["c99"], 2)},
        "rule": "fail = white vs P90 < %.1f:1 or vs P99 < %.1f:1; plate when >= %d%% of "
                "samples fail" % (NEED_P90, NEED_P99, int(FAIL_FRACTION * 100)),
    }
    if plate:
        result["white_on_plate_at_bright_end"] = round(
            contrast_white(plate_over(g_ref, alpha, color)), 2)
    out = Path(a.out).expanduser() if a.out else Path(a.settings).expanduser().with_name(
        "plate-decision.json")
    out.write_text(json.dumps(result, indent=1) + "\n")

    look["plate"] = bool(plate)
    if plate:
        look["plate_opacity"] = alpha
    look_path.write_text(json.dumps(look, indent=1) + "\n")

    print("Card area x %d-%d, y %d-%d; %d frames from setting A." % (
        region[0], region[0] + region[2], region[1], region[1] + region[3], len(rows)))
    print("White type vs the picture: worst frame %s at %.1f:1 (brightest 10%%) and "
          "%.1f:1 (brightest 1%%); %d of %d frames fail (%.0f%%)." % (
              fmt(worst["t"]), worst["c90"], worst["c99"], len(fails), len(rows), 100 * frac))
    if plate:
        print("VERDICT: PLATE, %s at %d%% (%s). White on it holds %.1f:1 even over the "
              "bright end of the picture." % (result["plate_color"], int(round(alpha * 100)),
                                              result["why"],
                                              contrast_white(plate_over(g_ref, alpha, color))))
    else:
        print("VERDICT: NO PLATE. The picture behind the column is dark and even enough for "
              "white type.")
    print("Decision -> %s" % out)

    if a.patch_props:
        pp = Path(a.patch_props).expanduser()
        props = json.loads(pp.read_text())
        props["look"] = {k: v for k, v in look.items() if k != "plate_asked"}
        pp.write_text(json.dumps(props, ensure_ascii=False) + "\n")
        print("Props %s: look with plate %s" % (pp, ("%s at %d%%" % (result["plate_color"],
              int(round(alpha * 100)))) if plate else "off"))

    # the worst frame's card area, for a look
    look = out.with_name("plate-check-worst.png")
    x, y, w, h = region
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-y", "-ss", "%.3f" % worst["t"],
                    "-i", str(a.final), "-frames:v", "1",
                    "-vf", "crop=%d:%d:%d:%d" % (w, h, x, y), str(look)])
    print("Look at the worst frame's card area: %s" % look)


if __name__ == "__main__":
    main()
