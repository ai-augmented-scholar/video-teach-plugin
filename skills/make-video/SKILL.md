---
name: make-video
description: Run the whole video-teach-plugin workflow for an educational video with talking-head footage, from beat sheet to finished video. The user films; after that Claude runs every step to the finished video with no question and no stop - the sound, the cut, the slides if there is a deck, the text cards, the captions, the course-page description and a title and thumbnail idea - through lecture-video. The user then watches the result and publishes it by hand; short clips on request. Keeps a status file in the video's folder, sends one Mac notification when the video is finished, and picks up where it left off after a break. Use when the user says "make a video", "start a new lecture video", "continue my video", "where was I", or runs /video-teach-plugin:make-video.
---

# make-video: film it, and Claude does the rest

$ARGUMENTS

This skill runs the other skills of the plugin in the right order and keeps track
of where each video stands. It does no editing of its own.

**The promise to the user: set up once, film, hand over the recording.** From the
moment the recording is in the video folder, nothing asks the user anything until the
finished video is there. Every judgment (the cut, the slides, the cards, the
description) is Claude's, made with the rules of the skills and logged with its reason.
The user judges the finished video and says what to change, in a sentence.

The plugin is made for **educational videos with talking-head footage**: one
person on camera, explaining. Any video editor works; iMovie stands for "iMovie,
or any editor".

Throughout this file:

```bash
MV="${CLAUDE_PLUGIN_ROOT}/skills/make-video/scripts"
export TV_DATA="${CLAUDE_PLUGIN_DATA}"
```

Pass `TV_DATA` on every command, as written. The scripts read the settings
(`$TV_DATA/config.json`) through it.

## The status file

Each video has a folder `NN-slug` under `videos_root` (from the settings;
default `~/Movies/teaching-videos`), and in it a file `make-video-status.json`
that records each step's state (`pending`, `running`, `waiting-for-you`,
`done`, `skipped`), its input and output files, the choices made, and times.

**Only `status.py` reads and writes that file. Never edit the JSON by hand.**

```bash
TV_DATA="$TV_DATA" python3 "$MV/status.py" new --title "<title>"     # new video: makes the next NN-slug folder, prints its path
TV_DATA="$TV_DATA" python3 "$MV/status.py" find [words]              # list videos and where each stands
TV_DATA="$TV_DATA" python3 "$MV/status.py" show "<video folder>"     # the 14 steps, with the next one marked
TV_DATA="$TV_DATA" python3 "$MV/status.py" set "<video folder>" 03 running --input "<raw take>"
TV_DATA="$TV_DATA" python3 "$MV/status.py" set "<video folder>" 03 done --output "<cleaned file>"
TV_DATA="$TV_DATA" python3 "$MV/status.py" set "<video folder>" 06 done --choice path=slides --output "<file>"
TV_DATA="$TV_DATA" python3 "$MV/status.py" reopen "<video folder>" 04   # send 04 and every later step back to pending
TV_DATA="$TV_DATA" python3 "$MV/status.py" look                     # the saved look in one line, or "none"
```

`set` refuses to start or finish a step while an earlier one is still open, and
refuses `done` with an output file that does not exist. Mark a step `running`
before you start it and `done` with its output when it is finished, so a break
in the middle leaves an honest record.

## Start

1. **Which video?** If the user names a video, or says "continue", run `find`
   and pick the match (ask if two match). If they start a new one, ask for a
   working title and run `new`. For an existing folder that has no status file
   yet, run `init "<folder>" --title "<title>"`.
2. **Resume.** Run `show`. Continue at the step marked `->`. A step left
   `running` was interrupted: check whether its output exists and is complete
   before you redo it.
3. **New video only: the filming setup.** Run `look`.
   - It prints a look: show that line and ask **"Same filming setup as last
     time?"** On a yes, go on. On a no, run the look questions of
     `text-treatments` (its Step 0) before step 01.
   - It prints `none`, or there is no settings file at all: the computer has not
     been set up. If `/video-teach-plugin:setup` has never run (no settings file),
     ask the user to run it first; it installs the tools and asks these
     questions. If only the look is missing, ask the three look questions now,
     through `text-treatments` Step 0.
4. **Say once which steps need a strong model.** The editing run (steps 03 to 09)
   makes judgment calls and wants Sonnet at high effort or Opus at medium effort.
   On a weaker model say so in one line and carry on; `lecture-video` repeats it in
   its closing summary.

## The steps

| Step | Who | What | Skill / input -> output |
|---|---|---|---|
| 01 | You + Claude | Draft the beat sheet (optional) | `beatsheet` -> `beat-sheet.html` in the video folder |
| 02 | You | Film | the raw take (and the deck, if the lecture has one) in the video folder |
| 03-09 | Claude | **One run, no stops**: sound, cut, slides, text cards, finished video, captions, description, then title and thumbnail idea | `lecture-video` on the video folder, then `first-impression` |
| 10 | You | Watch it, then publish | changes in a sentence go back into the run; upload by hand |
| 11-14 | Claude, then you | Short clips, only on request | `perfect-clips`; pick, polish, post by hand |

### 01 Beat sheet

Run the `beatsheet` skill for this video folder (pass the folder, so it does not
make a second one). It asks for topic, audience, length and whether the user
films from a script. When `beat-sheet.html` exists: `set 01 done --output
"<folder>/beat-sheet.html"`. The user may skip the beat sheet (they already
filmed, or work from their own notes): `set 01 skipped --note "<why>"`.

### 02 Film

A "you" step, the last one before the finished video. Tell the user: film the
lecture, one take per section; when a sentence goes wrong, start it again without
stopping the camera. Then put the recording (the camera or recorder file, not an
export) into the video folder, with the slides as PDF or PowerPoint if the lecture
has them, and say "continue". When the file is there, check it with `ffprobe`
(duration, a video and an audio stream), then `set 02 done --output "<raw take>"`.

**Never ask whether there are slides.** A `.pdf` or `.pptx` in the folder is the
deck; no such file means a lecture without slides. Both are normal.

### 03 to 09: the editing run

From here to the end of step 09, run everything in one go, in one turn, with no
question to the user and no "you" step. A break in the middle (a closed session)
resumes from `status.py show` and `lecture_run.py next`.

1. `set 03 running --input "<raw take>"`.
2. Run the `lecture-video` skill on the video folder, as its SKILL.md says, all four
   phases and Judgment 4, with `--video "<raw take>"` (and `--deck` only if the user
   named a deck outside the folder). It grades the picture if needed, cleans the
   sound, places the slides (or keeps the speaker throughout when there is no deck),
   cuts the retakes, writes and places the text cards, renders the finished video,
   writes the captions and the transcript, and you write the course-page
   description. Its sound gate and checks run inside; a real failure stops it with
   `lecture/stop-reason.txt`: say the reason and stop.
3. Record the steps it covered, each with its file:

   ```bash
   F="<video folder>"; N="<folder name>"
   TV_DATA="$TV_DATA" python3 "$MV/status.py" set "$F" 03 done --output "$F/lecture/<the _enhanced-audio file>"
   TV_DATA="$TV_DATA" python3 "$MV/status.py" set "$F" 04 done --output "$F/$N perfect cut (C)/1 WATCH - final video (C).mp4"
   TV_DATA="$TV_DATA" python3 "$MV/status.py" set "$F" 05 done --note "stills and logs checked by Claude"
   TV_DATA="$TV_DATA" python3 "$MV/status.py" set "$F" 06 done --choice path=lecture-video --output "$F/text-cards/cards.json"
   TV_DATA="$TV_DATA" python3 "$MV/status.py" set "$F" 07 done --output "$F/$N-finished-with-text.mp4"
   ```

   (Mark each `running` first if `set` asks for it.) Step 05 is your own check, not
   the user's: look at `lecture/check/` (every card still, setting A and B) and the
   summary before you go on. A card that is cut off, a slide in the wrong place you can
   see from the log: fix it in the phase it belongs to and render again, then go on.
4. **08 Title, opening, thumbnail idea.** Run `first-impression` on the transcript,
   without its questions: take the topic and audience from the beat sheet if there is
   one, else from the transcript; replace an existing `first-impression.html` only if
   this run wrote it. `set 08 done --output "<folder>/first-impression.html"`. The
   thumbnail itself is the user's, and optional; mention the idea in the handover.
5. **09 Captions and description** were made by `lecture-video`:
   `set 09 done --output "<folder>/captions.srt" --output "<folder>/course-page-description.txt"`.

### 10 Watch it, then publish

The first "you" step after filming. `set 10 waiting-for-you`, then notify:

```bash
bash "$MV/notify.sh" "Your lecture video is finished" "<folder name>-finished-with-text.mp4"
```

The handover message, in this order:

1. The finished video (full path) and its length.
2. What Claude decided, from the `lecture-video` summary, shortened: the cuts, the
   slides (or "you on screen throughout"), the cards, any warning.
3. The other files: `captions.srt` (upload it as the caption track, not as a
   download), `transcript.txt`, `course-page-description.txt`, the title and the
   thumbnail idea from `first-impression.html`.
4. **"Watch it once. If something should change, say it in a sentence ('show the
   slide at 4:10', 'bring back the sentence about Kant', 'drop the second card') and
   I render it again. The plugin never publishes anything: upload the video and the
   captions to your course site yourself."**
5. "Short clips for announcements? Say 'make short clips'."

A change: `reopen` the step it belongs to (04 for a cut or a slide, 06 for a card),
redo that `lecture-video` phase and the ones after it, and the captions with them.
`set 10 done` when the user says the video is up.

### 11 to 14 Short clips (only on request)

Only when the user asks. Run `perfect-clips` on the finished video (upright for
phones, or wide for a course page; the skill asks). Upright clips are cut from the
speaker cut, not the export: perfect-clips finds that cut in the video folder by
itself, so a burned-in card is never sliced by the upright crop. `set 11 done
--output "<clips package folder>"`, then 12 to 14 are the user's: pick, polish in any
editor, post by hand. Without a request, leave 11 to 14 pending.

## A step that is yours

Two steps after the beat sheet are the user's: 02 (film) and 10 (watch, publish).
At each, in this order:

1. `set <step> waiting-for-you`.
2. Send the notification with `notify.sh`, as above.
3. Tell the user what to do, which file to use (full name and folder), and how to
   come back: **"When you are done, say 'continue', or run
   /video-teach-plugin:make-video."**
4. Stop. Do not start the next step in the same turn.

When the user comes back, run `show` and continue at the waiting step: confirm it
is done (check the file it should have produced), mark it `done`, and go on.

## Without the run: the single skills

Each skill still runs on its own when a user wants one piece by hand: `audio-enhance`,
`perfect-cuts`, `text-treatments` (cards over an export from their own editor, with
its approval of the wording and the timing), `video-with-slides` (other slide
layouts), `captions-and-description` (captions for an export the user made
themselves). make-video does not use them one by one any more.

## Prompts to remember

- `/video-teach-plugin:make-video` - start a new video, or continue the last one.
- "Here is my recording (and my slides): make the lecture video." - the editing run, straight away.
- "Continue my video." / "Where was I?" - shows the steps and picks up.
- "Show the slide at 4:10." / "Bring back the sentence about Kant." - a change after watching.
- "Make short clips from this lecture." - steps 11 to 14.
- `/video-teach-plugin:lecture-video` - the editing run on its own.
