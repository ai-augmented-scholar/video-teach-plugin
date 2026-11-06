#!/usr/bin/env python3
"""Find the spoken edit directions in a lecture transcript.

Usage:
    python3 find_cues.py --words transcript.json --out cues.json [--loose]

Optional. A lecturer MAY say "setting A" or "setting B" on camera to tell the
editor where the picture changes; most will not, and then this script finds
nothing and the model decides A and B by itself. When there are cues, they
govern. This script finds those phrases in the word list
that `transcribe --words` wrote (segments[].words[]), and writes cues.json:

    {"cues": [{"setting": "B", "start": 41.3, "end": 42.0,
               "switch_at": 41.65, "text": "Setting B", "heard": "..."}, ...]}

switch_at is the middle of the spoken cue. The cue words are cut out later
(perfect-cuts), so the picture change lands exactly on that cut.

A phrase counts as a cue when it is a direction, not part of a sentence:
either it holds a directive verb ("switch to setting B", "go to setting A",
"cut to", "back to") or it stands alone, with a pause of at least 0.3 s on one
side. The whole phrase is the cue, "okay, switch to setting B now" included, so
the editor cuts all of it. "In a classroom setting a teacher ..." has neither
and is left alone. --loose also reports such phrases as candidates; they are
never written to cues.json.

The transcript may write the letters in several ways; these are accepted:
    A: a, ay          B: b, be, bee
Stdlib only.
"""
import argparse
import json
import re
import sys
from pathlib import Path

PAUSE = 0.3
MAX_SPAN = 1.2          # "setting" to the letter, seconds
LETTERS = {"a": "A", "ay": "A", "b": "B", "be": "B", "bee": "B"}


def norm(text):
    return re.sub(r"[^0-9a-zà-ɏ]+", "", text.lower().replace("’", "'"))


def load_words(path):
    data = json.loads(Path(path).read_text())
    raw = []
    if isinstance(data, dict) and "words" in data:
        raw = data["words"]
    elif isinstance(data, dict) and "segments" in data:
        raw = [w for s in data["segments"] for w in s.get("words", [])]
    elif isinstance(data, list):
        raw = data
    else:
        sys.exit("ERROR: cannot read words from %s" % path)
    return [{"text": (w.get("word", w.get("text", "")) or "").strip(),
             "tok": norm(w.get("word", w.get("text", "")) or ""),
             "start": float(w["start"]), "end": float(w["end"])} for w in raw]


def fmt(t):
    m, s = divmod(t, 60)
    return "%d:%04.1f" % (m, s)


# A direction is spoken as a short phrase: "Okay, switch to setting B now."
VERBS = {"switch", "go", "cut", "change", "move", "back"}
LEAD = VERBS | {"to", "okay", "ok", "now", "and", "so", "please", "let's", "lets", "let", "over"}
TAIL = {"now", "please", "here"}
WORD_CAP = 0.8   # Parakeet pads a word before a pause ("now" ran 9 s); cap every word


def end_of(w):
    return min(w["end"], w["start"] + WORD_CAP)


def find(words, loose):
    found, loose_hits = [], []
    for i in range(len(words) - 1):
        if words[i]["tok"] != "setting" or words[i + 1]["tok"] not in LETTERS:
            continue
        a, b = words[i], words[i + 1]
        if end_of(b) - a["start"] > MAX_SPAN:
            continue
        # the whole spoken direction: leading "okay, switch to", trailing "now"
        i0 = i
        while i0 > 0 and i - i0 < 5 and words[i0 - 1]["tok"] in LEAD \
                and words[i0]["start"] - words[i0 - 1]["end"] < 0.6:
            i0 -= 1
        i1 = i + 1
        # "setting B now" takes the "now"; "setting B. Now ..." (a full stop after the
        # letter) starts a new sentence and keeps it
        if i + 2 < len(words) and words[i + 2]["tok"] in TAIL \
                and words[i + 2]["start"] - b["end"] < 0.6 \
                and not re.search(r"[.?!]$", b["text"]):
            i1 = i + 2
        first, last = words[i0], words[i1]
        before = first["start"] - words[i0 - 1]["end"] if i0 > 0 else 99.0
        after = words[i1 + 1]["start"] - last["end"] if i1 + 1 < len(words) else 99.0
        # a directive verb in the phrase ("switch to setting B") is enough on its
        # own; otherwise the phrase has to stand alone, with a pause on one side
        has_verb = any(w["tok"] in VERBS for w in words[i0:i])
        ctx = " ".join(w["text"] for w in words[max(0, i0 - 3):i1 + 5])
        cue = {"setting": LETTERS[b["tok"]], "start": round(first["start"], 3),
               "end": round(end_of(last), 3),
               "switch_at": round((first["start"] + end_of(last)) / 2, 3),
               "text": " ".join(w["text"] for w in words[i0:i1 + 1]), "heard": ctx}
        if has_verb or before >= PAUSE or after >= PAUSE:
            found.append(cue)
        elif loose:
            loose_hits.append(cue)
    return found, loose_hits


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--words", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--loose", action="store_true")
    a = ap.parse_args()
    words = load_words(a.words)
    if not words:
        sys.exit("ERROR: the transcript has no words.")
    cues, loose = find(words, a.loose)

    notes = []
    # The same cue said twice in a row is a retake of the cue: keep the later one.
    kept = []
    for c in cues:
        if kept and kept[-1]["setting"] == c["setting"] and c["start"] - kept[-1]["end"] < 8.0:
            notes.append("%s: setting %s said twice (%s and %s); kept the later one."
                         % (fmt(c["start"]), c["setting"], fmt(kept[-1]["start"]), fmt(c["start"])))
            kept[-1] = c
        else:
            kept.append(c)
    cues = kept

    Path(a.out).expanduser().write_text(json.dumps({"cues": cues, "notes": notes},
                                                    indent=1, ensure_ascii=False))
    if not cues:
        print("No spoken setting cues (the usual case). The model decides A and B from the "
              "content (SKILL.md, 'Deciding A and B').")
    for c in cues:
        print("%-8s setting %s  cut %s-%s   ...%s..." % (
            fmt(c["start"]), c["setting"], fmt(c["start"]), fmt(c["end"]), c["heard"]))
    for n in notes:
        print("NOTE " + n)
    for c in loose:
        print("CANDIDATE (no pause, not used) %s: %s" % (fmt(c["start"]), c["heard"]))
    print("%d cue(s) -> %s" % (len(cues), a.out))


if __name__ == "__main__":
    main()
