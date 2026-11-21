---
name: lecture-video
description: Turn a talking-head lecture recording, and its slide deck (PDF or .pptx) if there is one, into one finished, professional lecture video with captions, a transcript and a course-page description, end to end and with no approval stops. Without a deck the whole video is the speaker with text cards. With a deck, Claude cuts between setting A (the speaker alone, framed a little right of centre, with animated text cards in the free left side) and setting B (the slide almost full screen on a neutral background, the speaker as a small rounded insert), decides itself when to switch, cuts the retakes and fillers, adds text cards in setting A only, and bakes them into the final MP4. Use when the user drops a lecture recording (with or without slides) into a video folder, says "make a lecture video", "edit my lecture", "here is my recording", "my lecture with slides", "slides with me in the corner", "put my slides in", or runs /video-teach-plugin:lecture-video. Also the editing chain of make-video, with or without slides.
---

# Lecture video

$ARGUMENTS

**Drop in the talking-head recording, and the slides if you have them; get a finished lecture video out.**
One input, a video folder that holds the recording and, optionally, the deck. The chain grades the
picture if it is phone HDR, cleans the sound, builds the A/B slide edit, cuts the
retakes, brings the A/B timeline across the cuts, writes text cards for setting A, and
renders the finished MP4 with the text baked in, plus a transparent text overlay for
anyone who wants to keep editing in iMovie (or any editor), then the captions, the
transcript and the course-page description of the finished video.

**No deck?** Then the whole lecture is setting A: the speaker, framed by the framing
rule, with text cards in the free column. prepare skips the slide steps and writes the
one-segment timing itself; Judgment 1 is only the framing landmarks. Everything else
runs the same. Never ask the user for slides: a missing deck is a choice, not a fault.

| | Setting A | Setting B |
|---|---|---|
| Picture | the speaker alone, full frame, a little right of centre | the slide almost full screen, the speaker as a small insert at the right |
| Text cards | yes, in the free left side | **never** (the slides carry the text) |

## The chain runs on its own

**No approval stops.** From the folder to the finished video, nothing asks the user
anything. Every judgment is made by you, the model, and leaves a reason in a file: the
slide timing, the A/B decisions, the framing landmarks, the editorial pass, the card text
and placement, the plate. This overrides every "show the user" and "wait for approval"
in the skills this chain calls (`perfect-cuts` intake questions, `text-treatments`
wording and timing approvals): here they are answered by the rules below and logged.
The user judges the finished video and says what to change.

- **The record replaces the approval.** `lecture/run-log.csv` holds every choice the
  scripts made (which files, which grade, the transcript pass, the plate, the look);
  `lecture/timing-log.csv` holds one row per A/B segment with the words at the change,
  what decided it and why, and where it landed in the finished video;
  `<package>/4 REVIVE - cut decisions (C).csv` holds every cut with its reason;
  `text-cards/placement-table.txt` holds every card's place and every skipped card.
- **Hard stops, only for real failures**, each written to `lecture/stop-reason.txt` by
  the scripts and said in your closing message: no video, PQ HDR footage, a
  picture too bright for the highlight gate, a deck that cannot become a PDF, a broken
  input, a failed step, a failed check (composite length, the A/B mapping against the
  real picture, an overlay with alpha inside setting B), a judgment file missing. A
  framing WARNING is not a stop: the render goes on with the framing rule applied, and
  the warning goes into the summary.
- **Model.** The judgment steps (slide timing, A/B, editorial pass, cards) want Sonnet at
  high effort or Opus at medium effort, as `perfect-cuts` says. On a weaker model or a low
  effort, say so in one line at the start, carry on (no stop), and repeat it in the
  closing summary next to the cut log, which is the file to audit.
- **The closing message is the handover.** It is the shortened output of
  `lecture_summary.py` (the phase `finish` prints it and saves it as
  `lecture/summary.txt`): the A/B table, the cuts, the cards, the plate, any warning, and
  the files to look at, the finished video first.

Throughout this file:

```bash
SK="${CLAUDE_PLUGIN_ROOT}/skills/lecture-video/scripts"
export TV_DATA="${CLAUDE_PLUGIN_DATA}"
```

Pass `TV_DATA="$TV_DATA"` on every command, as written; the scripts find the plugin's
tools and the user's settings (`$TV_DATA/config.json`) through it. If a script prints
`MISSING` or says a tool is not installed, point the user to `/video-teach-plugin:setup`
and stop (that is a real failure).

## The input: one video folder

Each video lives in a numbered folder `NN-slug` under the user's `videos_root` (from
setup; default `~/Movies/teaching-videos`).

- The user names a folder, or `make-video` hands one over: use it.
- The user drops files somewhere else ("here is my lecture and the slides"): make the
  folder with make-video's status script and point the chain at the files where they
  are, without moving them:

  ```bash
  TV_DATA="$TV_DATA" python3 "${CLAUDE_PLUGIN_ROOT}/skills/make-video/scripts/status.py" new --title "<lecture title>"
  ```

  then pass `--video "<recording>" --deck "<deck>"` to the prepare phase.

Inside the folder (or its `raw/` subfolder) the chain takes the newest video (`.mov`,
`.mp4`, `.m4v`, `.mkv`; its own outputs are skipped) and the newest deck (`.pdf`, best,
or `.pptx`). With several candidates it takes the newest and logs what it left alone.
No deck in the folder means no slides: the run goes on in setting A throughout.

## The four phases

```bash
TV_DATA="$TV_DATA" python3 "$SK/lecture_run.py" prepare "<video folder>"   # [--video V --deck D] [--pages 3-14]
#   -> Judgment 1: slide timing, A and B, framing landmarks
TV_DATA="$TV_DATA" python3 "$SK/lecture_run.py" compose "<video folder>"
#   -> Judgment 2: the editorial pass (lecture/cut/decisions.json)
TV_DATA="$TV_DATA" python3 "$SK/lecture_run.py" cut "<video folder>"
#   -> Judgment 3: the text cards for setting A (text-cards/cards.json)
TV_DATA="$TV_DATA" python3 "$SK/lecture_run.py" finish "<video folder>"
#   -> Judgment 4: names in the captions, course-page-description.txt
TV_DATA="$TV_DATA" python3 "$SK/lecture_run.py" next "<video folder>"      # where a run stands, after a break
```

Run them in order, in one turn, with your judgment between them. Each phase ends by
printing exactly what the next judgment needs; do it at once and run the next phase. A
phase run again redoes its own work (a timing change means compose, cut and finish
again). Times: about 2.5x real time for the composite at 1080p, about real time for the
overlay; the rest is minutes.

**prepare** does: an HDR grade when the clip is phone HDR (HLG), with the setting
searched from the grade's defaults to a dark exposure, the first one under the highlight
gate wins; `audio-enhance` when the clip is not enhanced yet; the highlight gate (the
brightest 10% of the pixels under luma 230 of 255); the deck to PDF; one image and the
text per slide (speaker notes too, from a `.pptx`); the Parakeet transcript, with the
windowed pass when the whole-file pass dropped speech; the optional spoken cues; a draft
slide timing (`match_slides.py`); a draft A/B timing; the timing table
(`lecture/timing-table.txt`); four source stills with a grid (`lecture/check/source-grid-*.png`).

**compose** does: preview stills (`lecture/check/preview-*-A.png` with guides,
`-B.png`), the A/B composite (length checked against the source, frame for frame), the
speech map of the composite, and the block list for the editorial pass
(`lecture/cut/blocks.txt`, the empty blocks measured in `measure-blocks.txt`).

**cut** does: `refine_cuts.py` (your decisions, the automatic filler and noise rules, the
spoken cues cut out), the `perfect-cuts` package `<folder> perfect cut (C)/` in the video
folder, the sound gate, the A/B settings carried across the cuts and checked against the
real picture, and the words of the finished cut with the setting-A stretches listed in
`text-cards/setting-a-words.txt`.

**finish** does: the card placement (no card over setting B), the plate decision, the
full-length transparent overlay, the checks (its length; alpha 0 inside every setting B;
every placed card visible), the finished video with the text baked in, check stills, the
final timing log, and the summary.

## Judgment 1 (after prepare)

**Without a deck**, only the framing landmarks below are left: the timing is already
one setting-A segment. Skip the slide timing and the A/B decision.

### The slide timing

`match_slides.py` proposes when each slide comes up, forward only; it is a draft and
often wrong on a short deck (it may skip the title slide, or start a slide late). Read
`lecture/transcript.json` against the slide text in `lecture/slides.json` and the draft
table in `lecture/slide-timing-draft.txt`, and rewrite `lecture/slide-timing.json`
where the draft is wrong. **Every changed segment gets a `"note"` with the reason**
("'three stages' opens the stages slide"). A retake keeps the same slide. Then rebuild
the A/B draft, which keeps the notes:

```bash
TV_DATA="$TV_DATA" python3 "$SK/build_timing.py" build --slides-timing "<folder>/lecture/slide-timing.json" \
  --auto --duration <seconds> --out "<folder>/lecture/timing.json"      # --cues "<folder>/lecture/cues.json" when there are cues
```

### Deciding A and B (the main path)

**The decision is yours.** No lecturer has to say anything on camera. Start from the
`--auto` draft and edit `lecture/timing.json` by hand. Each segment is
`{"start": s, "setting": "A"}` or `{"start": s, "setting": "B", "slide": N}` and runs
until the next one starts (times on the recording's clock). **Every segment you add or
change gets `"decided_by": "judgment: <rule>"` and a `"note"` with the words that
decided it**; the timing log carries both.

- **A** for the greeting and the first topic sentence; for the sign-off (about the last
  10 to 20 seconds); for stories, asides, examples from life, questions put to the
  class, instructions ("take a minute and write down..."); and for any stretch whose
  words share nothing with the slide that would be on screen.
- **B** while the speaker goes through what a slide shows: definitions, lists, quotes,
  figures, tables, worked examples, a framework walked step by step.
- **Runs last**: A at least 4 s, B at least 6 s; shorter reads as flicker (the scripts
  warn). A slide up for under 6 s belongs to the A or B around it.
- **Where the change goes**: at a sentence boundary, in the pause. A slide change inside
  B goes a quarter second before the first word of the new topic.
- **When unsure, stay in the setting you are in.**
- A wrong call costs one more render, never an approval round: decide, log the reason,
  and go on.

### Spoken cues: an optional override

Some lecturers like to direct the edit on camera. If the transcript holds "setting A" /
"setting B" said as a direction ("switch to setting B", "back to setting A", or the
phrase standing alone with a pause), `find_cues.py` lists them in `lecture/cues.json`
during prepare. **When there are cues, they govern**: build the timing with
`--cues` instead of `--auto`, and change only the slide shown inside B. The cue words are
cut out in the cut phase, so the picture change lands exactly on the cut. "In a
classroom setting, a teacher..." is not a cue (no directive verb, no pause); such near
misses are listed in `cues.json` as notes and never used. Read the cue list yourself:
each cue is a sentence of the footage, not a certainty. Never tell a user they must say
cues; without them the chain works the same.

### The framing landmarks

Setting A obeys one rule, which beats every other number in this skill:

> **THE FRAMING RULE FOR SETTING A. Eyes on the upper-third line (1/3 of the height
> from the top). At the very least both shoulders are in frame, with some chest below
> them. The face never fills the height.** When the rule and the free left side
> disagree, the rule wins and the compose script says so.

The crop needs four landmarks of the speaker **in the source frame**, as fractions:
`speaker_eye_y`, `speaker_shoulder_y` (the line where the shoulders meet the arms),
`speaker_shoulder_x0` and `speaker_shoulder_x1` (the span of the shoulders), plus
`speaker_x` (the centre of the body). Read them off the four
`lecture/check/source-grid-*.png` stills (thin white lines every 10%, thick black lines
at 50%, a black-and-white double line at one third of the height), averaged over the
stills because the speaker moves, and write them as top-level keys into
`lecture/timing.json`. The compose phase refuses to run without them, so the crop can
never go as close as it likes. From them the script derives the crop: eyes at the
one-third line, the bottom at least 7% of the source height below the shoulder line,
both shoulders inside, **never closer than 1.5x**, and the speaker placed at 0.62 of the
width only as far as those limits allow.

## Judgment 2 (after compose): the editorial pass

Look at the preview stills first (`preview-*-A.png`: the speaker clear of the guide line
at 46% of the width, the eyes on the one-third line; `-B.png`: the slide sharp, head and
shoulders inside the insert). A framing WARNING in the compose output is logged and goes
into the summary; it is advice for the next recording, not a number to tweak until it
disappears.

Then read `lecture/cut/blocks.txt` line by line, with the rules of `perfect-cuts`
(SKILL.md step 4: retakes, false starts, the restart trap, direction to the camera,
measure empty blocks before cutting them) and write the judgment as
`lecture/cut/decisions.json`:

```json
{"cut_ranges": [[38.2, 39.6, "false start: restarted in the next block"]],
 "trim_starts": [[41.41, 41.86, 42.5, "stumble before the restart"]]}
```

Seconds on the composite's clock, which is the recording's clock. A worked example is
`reference/example-decisions.json`. Write `{}` when nothing needs cutting by judgment.
Do not redo by hand what `refine_cuts.py` does by itself:

- spoken cues, empty blocks (breath, click, filler sound) and lone filler words are cut;
- a filler at the start of a block is trimmed at the waveform (the dip and the rise after
  it, else the local minimum, else the next word's start minus 0.04 s);
- an isolated voiced burst inside a block (an "uh" between two dips, claimed by no word)
  is cut at its dips; a word claims every voiced stretch it overlaps, capped at 0.8 s,
  because the transcript's word times run up to 0.25 s late;
- an out-point never runs into a cut filler that follows within 0.2 s;
- neighbouring kept blocks under 0.6 s apart become one clip.

Meta talk to the camera (a microphone test, "hold on") is not lecture content: cut it
with its reason, and the user can revive it from the cut log.

## Judgment 3 (after cut): the text cards, setting A only

Write `text-cards/cards.json` with the card rules of `text-treatments` (SKILL.md Step 1:
the speaker's own words, compressed; never a claim, number, name or date the video does
not say; an `anchor` of four to six words copied from the words of the finished cut).
Two lecture rules on top:

- **Cards only where the picture is setting A.** `text-cards/setting-a-words.txt` lists
  every A stretch with its words; anchor each card inside one of them. The placement
  skips any card whose place is in B, ends a card before the next B, and skips a card
  that would have less than its reading time (about 8 s): plan for A stretches of at
  least 10 s, and do not anchor a card in a short one.
- **Few and useful**: a hook or title near the start, a pull quote or a term in a long A
  stretch, an end card with what comes next. Setting B needs nothing: the slides carry
  the text. `{"cards": []}` is a valid answer for a lecture that is almost all B.

## Judgment 4 (after finish): names, and the course-page description

finish transcribes the finished video and writes `captions.srt` and `transcript.txt` into
the video folder (a copy the user edited by hand is kept; the new one waits in
`lecture/captions-work/`). Two things are left, still without a question:

1. **Spelling of names and terms.** Parakeet spells unfamiliar names by sound. Correct
   them in both files the same way, against the slide text (`lecture/slides.json`) and
   the beat sheet if the folder has one. Words only: never a timing line, never a merged
   or split cue, never the speaker's grammar. List every correction in the closing
   message ("Rousso -> Rousseau (6 places)").
2. **`course-page-description.txt`**, in the video folder, written from
   `lecture/captions-work/outline.txt` and `transcript.txt` with the template and rules of
   `captions-and-description` (SKILL.md Step 5): title, two or three sentences on what
   the student will learn, 3 to 5 key points with times from the outline, the terms and
   readings the video names, the length. `[FILL IN: ...]` where a fact is missing. If
   finish says the file was edited by hand, leave it and say so.

Then the closing message: the summary, shortened, the corrections, and the files.

## The look

**Setting A cards** are the plugin's neutral design system
(`text-treatments/renderer/src/design-systems/neutral.ts`): white text, Inter, left-aligned
in the free column of setting A (x 3-39%, y 30-88% of the frame, kept 80 px from the
edges), no brand marks. The font, the text colour and the plate colour come from the
user's saved `look` (setup's three questions); no saved look means Inter, white, and the
dark grey plate `#202020`. The free area of the saved look is not used here: setting A's
framing decides the free column. No question is asked.

**The plate** is decided by `plate_check.py`, from the picture: it samples three frames
inside every placed card, in the card column, and fails a frame when white type holds
less than 4.5:1 against the brightest 10% of its pixels, or less than 3:1 against the
brightest 1%. The whole lecture gets the plate when 10% or more of the frames fail, or
when the saved look already asks for one; one look per video. The plate opacity starts
at the look's `plate_opacity` (70% by default) and rises in 5% steps to 95% until white
holds 4.5:1 over the bright end of the picture. `lecture/plate-check-worst.png` shows the
card area of the worst frame.

**Setting B**: the slide inside a box with 5% gaps at the top, left and bottom and 12% at
the right (a 16:9 slide is 1594x896 px, centred), on the neutral charcoal background
`assets/background-plate.png` (or the user's own image: `slides.background` in
config.json, or `--set background=<path or #rrggbb>` in a `lecture_compose.py` call).
The speaker insert is a 9:16 crop of the video, 43.5% of the frame height, its top just
above the midline, 2% of the width clear of the right edge, overlapping the slide's right
edge. Both have rounded corners (24 px) and soft drop shadows. Check a deck's right
edge in the B preview: the insert covers about 72 px of the slide there.

Every number sits in `DEFAULTS` in `lecture_compose.py` and can be changed per lecture
as a top-level key in `timing.json` (same name). The useful ones: `ins_zoom` (1.4: head
and shoulders in the insert), `ins_crop_y`, `ins_speaker_x` (centre the insert on a
speaker who stands off the middle), and for a speaker in profile who faces left to
right, `"ins_side": "left", "gap_left": 0.12, "gap_right": 0.05` (a mirrored setting B).

## What the user gets

| File, in the video folder | What |
|---|---|
| `<folder>-finished-with-text.mp4` | **The finished lecture video**, text baked in. Upload it as it is. |
| `<folder>-text-overlay.mov` | The text cards alone, full length, transparent (ProRes 4444). For an editor: lay it on a track above `1 WATCH` of the package. iMovie does not honour alpha in an overlay; use the finished MP4 there. |
| `captions.srt`, `transcript.txt`, `course-page-description.txt` | The caption track (upload it as captions, not as a download), the full text, and the description for the course page. |
| `<folder> perfect cut (C)/` | The cut without text (`1 WATCH - final video (C).mp4`), captions, timeline files for editors that read them, the cut log. |
| `lecture/timing-log.csv`, `lecture/run-log.csv` | What was decided and why. |
| `lecture/check/` | Stills: setting A and B of the finished video, one per card. |

Look at `lecture/check/final-A.png`, `final-B.png` and every `final-card-NN.png`
yourself before the closing message. Nothing else is left for the user to run: they
watch the finished video, say what to change if anything, and upload it with the captions.

## Filming checklist (for the user's next recording)

Give it when a framing WARNING came up, or when the user asks how to film. The aim: the
picture filmed is already setting A, so the crop does almost nothing.

1. **Camera**: landscape 16:9, on a tripod, focus and exposure locked on the face, 30 fps
   or more; 4K if the phone can (crop headroom). Phone HDR is fine; the chain grades it.
2. **Place in the frame**: body centre at about 0.62 of the width (0.50 to 0.70 works),
   never left of the middle; eyes on the upper-third line (0.28 to 0.38 of the height).
3. **Distance**: both full shoulders and the top of the chest in frame; the head about a
   third of the frame height. Lens at eye level or a little above.
4. **Pose**: face the lens; glances at notes are fine. Sit upright, do not lean in, keep
   the same spot for the whole lecture.
5. **The wall on the left side**: plain, matte, mid-to-dark, with nothing on it (no
   shelf, poster, whiteboard or window). A light wall works too: the cards then get the
   dark plate.
6. **Light**: soft light from the front, nothing bright behind, one colour of light. With
   a light wall, lower the exposure so it is not blown out (the highlight gate stops the
   chain at luma 230).
7. **Test first**: record ten seconds, run prepare and compose on it, and look at the
   preview stills; no framing WARNING means the setup is right.

## Sharp edges

- **The order is fixed**: the slide edit on the recording's clock first, perfect-cuts on
  the composite second (picture and sound are cut together, so the sync cannot drift),
  the cards last on the finished clock. `map_settings.py` carries the A/B timeline across
  the cuts and checks it against the picture; never go on past its MISMATCH.
- **PowerPoint**: a `.pptx` becomes a PDF through Microsoft PowerPoint if it is installed,
  else LibreOffice, else the chain uses a PDF with the same name beside the `.pptx`, else
  it stops and asks for a PDF export. PowerPoint may show a "Grant File Access" dialog that
  blocks the export: it is a security prompt for the user, so nothing clicks it; the
  script says so and the PDF beside the deck is used. Builds and animations do not survive
  a PDF; one page per build step does.
- **Parakeet drops speech** on a long or noisy file. prepare measures the empty blocks and
  transcribes in about 20 s windows when speech went missing; the run log says which pass
  was kept. Transcription is Parakeet only, never Whisper.
- **HDR**: only HLG (most phones) is graded, by `hdr_grade.py` with the plugin's own
  Python (numpy). PQ (HDR10, Dolby Vision) stops the chain: export SDR first.
- A deck that is not 16:9 changes the slide's size in the box (4:3 is 1296x972, centred).
- ffmpeg draws no text in this chain; every word on screen comes from the slide images or
  the card renderer.
- Colour is never the only signal: the guide lines in the previews are black and white.

## How this differs from video-with-slides

`video-with-slides` stays as a standalone skill. It lays slides over an already cut
video (perfect-cuts first, slides second), offers four layouts (side by side, picture in
picture, slide only, speaker only), and asks the user to approve the slide timing. Use it
for a quick slide pass over a finished cut, or another layout. `lecture-video` is the
drop-in, end-to-end chain: one fixed two-setting look, the slides before the cut, its own
A/B judgment, text cards in setting A, and no approval stops.
