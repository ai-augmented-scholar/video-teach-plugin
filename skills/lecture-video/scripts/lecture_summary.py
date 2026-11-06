#!/usr/bin/env python3
"""The closing summary of a lecture-video run, built from the files it left.

Usage:
    python3 lecture_summary.py --folder "<video folder>"

The chain runs without asking; the user judges the finished video. This prints
what the chain decided and which files to look at, from <folder>/lecture/ (timing-log.csv,
settings-final.json, plate-decision.json, compose-warnings.txt, stop-reason.txt),
<folder>/text-cards/ (cards-timed.json) and the perfect-cuts package. Paste it, shortened,
as the last message of the run. Stdlib only.
"""
import argparse
import csv
import json
from pathlib import Path


def fmt(t):
    m, s = divmod(float(t), 60)
    return "%d:%04.1f" % (m, s)


def load(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--folder", required=True)
    a = ap.parse_args()
    f = Path(a.folder).expanduser()
    lec, cards_dir = f / "lecture", f / "text-cards"
    pkgs = sorted(p for p in f.glob("*perfect cut (C)") if p.is_dir())

    stop = lec / "stop-reason.txt"
    if stop.exists() and stop.read_text().strip():
        print("THE RUN STOPPED: " + stop.read_text().strip())
        return

    rl = lec / "run-log.csv"
    if rl.exists():
        print("WHAT THE RUN DID WITH THE FILES (run-log.csv; every choice and why)")
        rows = list(csv.DictReader(open(rl, encoding="utf-8-sig")))
        # the newest run of each phase only
        newest = {}
        for r in rows:
            newest[r.get("phase", "")] = r["run"]
        for r in rows:
            if r["run"] != newest.get(r.get("phase", "")):
                continue
            if r["step"] in ("input", "deck", "enhance", "gate", "grade", "transcript", "cues",
                             "framing", "plate", "look", "stop") and r["what"] != "speaker_x":
                if r["step"] == "framing" and r["what"] != "WARNING":
                    continue
                print("  %-10s %s: %s%s" % (r["step"], r["what"], r["detail"][:140],
                                            (" (%s)" % r["why"][:90]) if r["why"] else ""))
        print()
    print("WHAT THE CHAIN DECIDED")
    log = lec / "timing-log.csv"
    if log.exists():
        print("\nA/B timing (finished video; the full reasons are in timing-log.csv):")
        for r in csv.DictReader(open(log, encoding="utf-8-sig")):
            when = ("%s-%s" % (r.get("final_start", ""), r.get("final_end", ""))
                    if r.get("final_start") else "cut out")
            what = "setting %s%s" % (r["setting"], (" slide %s" % r["slide"]) if r["slide"] else "")
            print("  %-12s %-18s %s" % (when, what, r["decided_by"]))
    sf = load(lec / "settings-final.json")
    if sf:
        b = sum(y - x for x, y in sf["b_intervals"])
        print("  Setting B is %.0f%% of the %s video." % (100 * b / sf["duration"], fmt(sf["duration"])))

    if pkgs:
        cut = load(pkgs[-1] / "cut data (C).json")
        if cut:
            fps = float(cut["fps"])
            kept = sum(c["out_frame"] - c["in_frame"] for c in cut["clips"]) / fps
            print("\nCuts: %d clips, %s of %s kept (perfect-cuts, refine_cuts.py)." % (
                len(cut["clips"]), fmt(kept), fmt(cut["source_duration"])))

    ct = load(cards_dir / "cards-timed.json")
    if ct:
        placed = [c for c in ct["cards"] if c.get("start") is not None]
        skipped = [c for c in ct["cards"] if c.get("start") is None]
        print("\nText cards: %d placed in setting A, %d skipped." % (len(placed), len(skipped)))
        for c in placed:
            label = c.get("headline") or c.get("quote") or c.get("eyebrow") or c.get("kind")
            print("  %s-%s  %s" % (fmt(c["start"]), fmt(c["end"]), str(label)[:60]))
        for c in skipped:
            note = "; ".join(c.get("place", {}).get("notes", []))
            print("  skipped: %s (%s)" % (str(c.get("headline") or c.get("quote") or c.get("kind"))[:40], note))
    pd = load(lec / "plate-decision.json")
    if pd:
        print("Plate behind the cards: %s (%s)." % (
            ("yes, %s at %d%%" % (pd.get("plate_color"), round(100 * pd["plate_opacity"])))
            if pd["plate"] else "no", pd.get("why", "")))

    warn = lec / "compose-warnings.txt"
    if warn.exists() and warn.read_text().strip():
        print("\nWARNINGS")
        for line in warn.read_text().strip().splitlines():
            print("  " + line)

    print("\nFILES TO LOOK AT")
    for pat, what in (("*-finished-with-text.mp4", "THE FINISHED VIDEO, text baked in (watch this)"),
                      ("*-text-overlay.mov", "the text alone, transparent (for an editor: lay it "
                                             "over 1 WATCH in the package)")):
        for x in f.glob(pat):
            print("  %s\n      %s" % (x, what))
    if pkgs:
        print("  %s\n      the perfect-cuts package (1 WATCH is the video without text; 4 REVIVE is the cut log)" % pkgs[-1])
    if log.exists():
        print("  %s\n      what was decided and why, segment by segment" % log)
    for name, what in (("check", "stills: setting A and B, one per card"),
                       ("run-log.csv", "every choice the chain made, with its reason")):
        if (lec / name).exists():
            print("  %s\n      %s" % (lec / name, what))


if __name__ == "__main__":
    main()
