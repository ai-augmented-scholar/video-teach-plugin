#!/usr/bin/env python3
"""Build the lecture timing.json (setting A / B per moment) and print its table.

Usage:
    python3 build_timing.py build --slides-timing T.json --out timing.json \
        [--cues cues.json] [--auto] [--duration SECONDS]
    python3 build_timing.py table --timing timing.json --words transcript.json \
        --slides slides.json

`--slides-timing` is the file `match_slides.py propose` wrote: when each slide
comes up, forward only. This script adds the second dimension, the setting.

    --cues cues.json   The lecturer's spoken "setting A / setting B" directions
                       (find_cues.py). Setting changes at every cue's
                       switch_at; the slide shown in B is the slide that the
                       slide timing has at that moment, and it changes inside B
                       where the slide timing changes.
    --auto             No cues: a first draft from the slides alone. Setting B
                       wherever a slide with mid or high confidence is up, A
                       for a speaker-only stretch and for a low-confidence
                       stretch, and A for the last 20 seconds. It is only a
                       draft. The model's judgment pass (SKILL.md, "Deciding A
                       and B") makes the real decision.

The draft is then smoothed: an A run under 4 s joins the B around it, a B run
under 6 s joins the A around it, so the picture never flickers.

segments in the output: {"start": s, "setting": "A"} or
{"start": s, "setting": "B", "slide": N}, plus "confidence", "decided_by" (what set the
change: a spoken cue, the slide timing, an auto draft) and "note" (the reason of a hand
correction in slide-timing.json). Edit freely; each runs until the next starts.

    python3 build_timing.py log --timing timing.json --words transcript.json \
        --slides slides.json --out timing-log.csv [--cues cues.json] [--final settings-final.json]

`log` writes the timing log (CSV, UTF-8 with BOM, opens in Excel): one row per segment with
its start and end, setting, slide, the words spoken at the change, what decided it and
the reason of any hand correction. With --final (settings-final.json from map_settings.py)
it adds where each segment landed in the finished video, or says that perfect-cuts cut it
out. The user reads this file to see what was done and to say what to change.

The model's judgment pass edits timing.json by hand: every segment it adds or
changes carries "decided_by": "judgment: <rule>" and a "note" with the reason,
and the log keeps both.
Stdlib only.
"""
import argparse
import json
import sys
from pathlib import Path

MIN_A, MIN_B = 4.0, 6.0
CLOSE_A = 20.0
LOOKAHEAD = 2.5


def fmt(t):
    m, s = divmod(max(t, 0.0), 60)
    return "%d:%04.1f" % (m, s)


def slide_at(slide_segs, t):
    cur = None
    for s in slide_segs:
        if s["start"] <= t + 1e-6:
            cur = s
    return cur


def from_cues(slide_segs, cues, duration):
    marks = [(0.0, "A")] + [(c["switch_at"], c["setting"]) for c in cues]
    marks.sort()
    spans = []
    for i, (t, setting) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else duration
        if spans and spans[-1][2] == setting:
            spans[-1][1] = end
        else:
            spans.append([t, end, setting])
    return spans


def from_auto(slide_segs, duration):
    spans = []
    for i, s in enumerate(slide_segs):
        end = slide_segs[i + 1]["start"] if i + 1 < len(slide_segs) else duration
        shows_slide = "slide" in s and s.get("confidence") in ("mid", "high")
        setting = "B" if shows_slide else "A"
        if spans and spans[-1][2] == setting:
            spans[-1][1] = end
        else:
            spans.append([s["start"], end, setting])
    if duration and spans and spans[-1][2] == "B":
        # the sign-off belongs to the face: the last CLOSE_A seconds are A,
        # as far as that leaves a B run worth keeping
        last = spans[-1]
        close = min(CLOSE_A, (last[1] - last[0]) - MIN_B)
        if close >= MIN_A:
            last[1] = duration - close
            spans.append([duration - close, duration, "A"])
    return spans


def smooth(spans):
    """Join runs that are too short to be a deliberate change."""
    changed = True
    while changed and len(spans) > 1:
        changed = False
        for i, (a, b, setting) in enumerate(spans):
            limit = MIN_A if setting == "A" else MIN_B
            if b - a < limit and 0 < i < len(spans) - 1:
                prev, nxt = spans[i - 1], spans[i + 1]
                prev[1] = b
                if prev[2] == nxt[2]:
                    prev[1] = nxt[1]
                    del spans[i:i + 2]
                else:
                    spans[i][2] = prev[2]
                    spans[i - 1:i + 1] = [[prev[0], b, prev[2]]]
                changed = True
                break
        # a short first or last run is kept: the opening and the close are real
    merged = []
    for s in spans:
        if merged and merged[-1][2] == s[2]:
            merged[-1][1] = s[1]
        else:
            merged.append(list(s))
    return merged


def segments_from(spans, slide_segs):
    out = []
    for a, b, setting in spans:
        if setting == "A":
            out.append({"start": round(a, 3), "setting": "A"})
            continue
        # The cue comes just before the slide's topic: look a little ahead, so a
        # slide that starts within LOOKAHEAD seconds of the cue is the one shown.
        cur = slide_at(slide_segs, a + LOOKAHEAD if a + LOOKAHEAD < b else a)
        n = cur.get("slide") if cur and "slide" in cur else None
        if n is None:       # B over a speaker-only stretch: use the next slide shown
            nxt = next((s for s in slide_segs if s["start"] >= a and "slide" in s), None)
            n = nxt["slide"] if nxt else None
        if n is None:
            sys.exit("ERROR: no slide to show in setting B at %s." % fmt(a))
        out.append({"start": round(a, 3), "setting": "B", "slide": n,
                    "confidence": cur.get("confidence", "") if cur else ""})
        for s in slide_segs:
            if a + LOOKAHEAD < s["start"] < b - LOOKAHEAD and "slide" in s and s["slide"] != n:
                out.append({"start": round(s["start"], 3), "setting": "B",
                            "slide": s["slide"], "confidence": s.get("confidence", "")})
                n = s["slide"]
    out[0]["start"] = 0.0
    return out


def annotate(segs, slide_segs, cues, how):
    """Say what decided each segment: a spoken cue, the slide timing, or an auto draft."""
    for i, s in enumerate(segs):
        if i == 0:
            s["decided_by"] = "start of the video"
            continue
        prev = segs[i - 1]
        cue = next((c for c in cues if abs(c["switch_at"] - s["start"]) < 0.06), None)
        if cue and prev["setting"] != s["setting"]:
            s["decided_by"] = "spoken cue %r at %s" % (cue.get("text", "setting " + cue["setting"]),
                                                      fmt(cue["start"]))
        elif how == "cues" and prev["setting"] == s["setting"]:
            s["decided_by"] = "slide timing"
        else:
            s["decided_by"] = "auto draft from the slide matching" if how != "cues" else "slide timing"
        # the reason of a hand correction in slide-timing.json
        if s["setting"] == "B":
            for ss in slide_segs:
                if ss.get("slide") == s.get("slide") and ss.get("note") and \
                        abs(ss["start"] - s["start"]) < 3.0:
                    s["note"] = ss["note"]
                    break
    for s in segs:
        if s["setting"] == "B" and "note" not in s:
            for ss in slide_segs:
                if ss.get("slide") == s.get("slide") and ss.get("note"):
                    s["note"] = ss["note"]
                    break


def cmd_build(a):
    slide_t = json.loads(Path(a.slides_timing).expanduser().read_text())
    slide_segs = slide_t["segments"]
    duration = a.duration or slide_t.get("duration")
    if not duration:
        sys.exit("ERROR: pass --duration (the video's length in seconds).")
    cues = []
    if a.cues:
        cues = json.loads(Path(a.cues).expanduser().read_text())["cues"]
        spans = from_cues(slide_segs, cues, duration)
        how = "cues"
    elif a.auto:
        spans = from_auto(slide_segs, duration)
        how = "auto draft"
    else:
        sys.exit("ERROR: give --cues cues.json, or --auto for a draft without cues.")
    spans = smooth([list(s) for s in spans])
    segs = segments_from(spans, slide_segs)
    cue_list = cues if a.cues else []
    annotate(segs, slide_segs, cue_list, how)
    timing = {
        "_comment": "Built from %s. Each segment runs to the next one's start. "
                    "{\"setting\": \"A\"} is the talking head alone; "
                    "{\"setting\": \"B\", \"slide\": N} is slide N with the insert." % how,
        "duration": duration,
        "speaker_x": 0.5,
        "segments": segs,
    }
    Path(a.out).expanduser().write_text(json.dumps(timing, indent=1, ensure_ascii=False))
    print("%d segments (%s) -> %s" % (len(segs), how, a.out))


def words_from(path):
    data = json.loads(Path(path).read_text())
    raw = data["words"] if isinstance(data, dict) and "words" in data else \
        [w for s in data.get("segments", []) for w in s.get("words", [])]
    return [(float(w["start"]), float(w["end"]), w.get("word", w.get("text", ""))) for w in raw]


def cmd_table(a):
    timing = json.loads(Path(a.timing).expanduser().read_text())
    slides = json.loads(Path(a.slides).expanduser().read_text())
    words = words_from(a.words)
    pages = {p["n"]: p for p in slides["pages"]}
    segs = timing["segments"]
    dur = timing.get("duration") or (words[-1][1] if words else 0)
    print("%-3s %-8s %-7s %-8s %-5s %-5s %-30s %s" % (
        "#", "starts", "length", "setting", "slide", "conf", "slide title", "spoken at the change"))
    for i, s in enumerate(segs, 1):
        end = segs[i]["start"] if i < len(segs) else dur
        title = pages.get(s.get("slide"), {}).get("title", "")[:30] if s["setting"] == "B" else ""
        after = [w for w in words if w[1] > s["start"]][:12]
        spoken = "".join(w[2] if w[2].startswith(" ") else " " + w[2] for w in after).strip()
        print("%-3d %-8s %-7s %-8s %-5s %-5s %-30s %s" % (
            i, fmt(s["start"]), "%.1fs" % (end - s["start"]), s["setting"],
            s.get("slide", ""), s.get("confidence", ""), title, spoken[:70]))
    b = sum((segs[i + 1]["start"] if i + 1 < len(segs) else dur) - s["start"]
            for i, s in enumerate(segs) if s["setting"] == "B")
    print("\nSetting B: %.0f%% of the lecture (%s of %s)." % (
        100 * b / dur if dur else 0, fmt(b), fmt(dur)))
    short = []
    run = None
    for i, s in enumerate(segs):
        end = segs[i + 1]["start"] if i + 1 < len(segs) else dur
        if run and run[0] == s["setting"]:
            run[2] = end
        else:
            if run:
                short.append(run)
            run = [s["setting"], s["start"], end]
    short.append(run)
    for st, a0, b0 in short:
        lim = MIN_A if st == "A" else MIN_B
        if b0 - a0 < lim:
            print("WARNING: setting %s at %s lasts %.1f s." % (st, fmt(a0), b0 - a0))
    used = {s["slide"] for s in segs if s["setting"] == "B"}
    missing = [n for n in sorted(pages) if n not in used]
    if missing:
        print("Never shown: slide(s) %s." % ", ".join(map(str, missing)))


def cmd_log(a):
    import csv
    timing = json.loads(Path(a.timing).expanduser().read_text())
    slides = json.loads(Path(a.slides).expanduser().read_text())
    words = words_from(a.words)
    pages = {p["n"]: p for p in slides["pages"]}
    segs = timing["segments"]
    dur = timing.get("duration") or (words[-1][1] if words else 0)
    final = None
    if a.final:
        final = json.loads(Path(a.final).expanduser().read_text())["segments"]
    a_used = 0
    a_final = [f for f in final if f["setting"] == "A"] if final else []
    n_a = sum(1 for s in segs if s["setting"] == "A")
    rows = []
    for i, s in enumerate(segs):
        end = segs[i + 1]["start"] if i + 1 < len(segs) else dur
        after = [w for w in words if w[1] > s["start"]][:14]
        spoken = "".join(w[2] if w[2].startswith(" ") else " " + w[2] for w in after).strip()
        title = pages.get(s.get("slide"), {}).get("title", "") if s["setting"] == "B" else ""
        row = [i + 1, round(s["start"], 2), fmt(s["start"]), round(end, 2), fmt(end),
               round(end - s["start"], 1), s["setting"], s.get("slide", ""), title, spoken,
               s.get("decided_by", ""), s.get("note", ""), s.get("confidence", "")]
        if final is not None:
            if s["setting"] == "A":
                f = a_final[a_used] if n_a == len(a_final) and a_used < len(a_final) else None
                a_used += 1
                match = [f] if f else []
            else:
                match = [f for f in final if f["setting"] == "B" and f.get("slide") == s.get("slide")]
            if match:
                t0, t1 = min(m["t0"] for m in match), max(m["t1"] for m in match)
                row += [round(t0, 2), fmt(t0), round(t1, 2), fmt(t1), ""]
            else:
                row += ["", "", "", "", "cut out entirely by perfect-cuts (retakes, noise or asides only)"]
        rows.append(row)
    head = ["n", "start_s", "start", "end_s", "end", "length_s", "setting", "slide", "slide_title",
            "words_at_the_change", "decided_by", "note", "slide_match_confidence"]
    if final is not None:
        head += ["final_start_s", "final_start", "final_end_s", "final_end", "final_note"]
    out = Path(a.out).expanduser()
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(rows)
    print("Timing log: %d segments -> %s" % (len(rows), out))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--slides-timing", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--cues", default=None)
    b.add_argument("--auto", action="store_true")
    b.add_argument("--duration", type=float, default=None)
    lg = sub.add_parser("log")
    lg.add_argument("--timing", required=True)
    lg.add_argument("--words", required=True)
    lg.add_argument("--slides", required=True)
    lg.add_argument("--cues", default=None)
    lg.add_argument("--final", default=None)
    lg.add_argument("--out", required=True)
    t = sub.add_parser("table")
    t.add_argument("--timing", required=True)
    t.add_argument("--words", required=True)
    t.add_argument("--slides", required=True)
    a = ap.parse_args()
    {"build": cmd_build, "table": cmd_table, "log": cmd_log}[a.cmd](a)


if __name__ == "__main__":
    main()
