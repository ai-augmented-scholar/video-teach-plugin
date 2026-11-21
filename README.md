<p align="center">
  <a href="https://aiaugmentedscholar.co"><img src="assets/owl-logo.png" width="160" alt="The AI-Augmented Scholar owl"></a>
</p>

# video-teach-plugin

A free Claude Code plugin for educational videos with talking-head footage:
one person on camera, explaining. You draft the lecture and film it. Claude
does the editing work that used to take three or four times the length of
the video.

Made by The AI-Augmented Scholar: [aiaugmentedscholar.co](https://aiaugmentedscholar.co).

## New in 2.2: film it, and Claude does the rest

You set the plugin up once, then you film your lecture. That is your whole part.
Hand Claude the recording, and the slides if your lecture has them, and the next
thing you see is the finished video:

```
Here is my recording and my slides: make the lecture video.
```

(or `/video-teach-plugin:lecture-video`, or `/video-teach-plugin:make-video` for
the whole workflow). Nothing asks you anything along the way. Claude:

- grades phone HDR footage and cleans the sound (noise removal, EQ,
  compression, steady loudness);
- cuts the retakes, false starts, ums and pauses: when a sentence goes wrong,
  start it again without stopping the camera, and Claude keeps the last good one;
- with slides, cuts between two looks. **Setting A** is you alone, framed a
  little right of centre, with animated text cards in the free space beside
  you. **Setting B** is your slide almost full screen, with you in a small
  rounded box at the side. Claude decides when to switch from what you say:
  the slide for a definition, a list or a figure; you for a story, an aside or
  a question to the class;
- without slides, keeps you on screen throughout, with the text cards beside
  you;
- writes the text cards from your own words, only where you are on screen;
- renders the finished MP4 with the text in it, and writes the captions, a
  full transcript, a description for your course page, and a title and
  thumbnail idea.

Every decision goes into a log with its reason. You watch the finished video
once. If something should change, say it in a sentence ("show the slide at
4:10", "bring back the sentence about Kant") and Claude renders it again. Then
you upload it. New in 2.2: the run works without slides, and the captions and
the course-page description come out of the same run. Nothing new to install.

## What it does

The workflow, from a beat sheet to a finished video:

1. **You and Claude** draft a beat sheet: bullet points per section, never a
   word-for-word script. Optional.
2. **You film.** One take per section; restarts are fine.
3. to 9. **Claude, in one run with no stops:** sound, cut, slides, text cards,
   the finished video, captions, transcript, course-page description, title
   and thumbnail idea.
10. **You watch it**, ask for any change in a sentence, and upload it to your
    course site.
11. to 14. **On request:** Claude cuts short clips with captions for
    announcements or the course homepage; you pick, polish and post them.

Everything runs on your own Mac. Your footage stays on your computer; Claude works from the transcript text.

You do not need a script. A recording is enough.

Each skill also runs on its own, for anyone who wants one piece by hand:
`audio-enhance`, `perfect-cuts`, `text-treatments` (cards over an export from
your own editor), `video-with-slides` (other slide layouts) and
`captions-and-description`. For a PowerPoint deck the plugin uses PowerPoint
or LibreOffice if your Mac has one; otherwise export the deck as PDF.

## What you need

- A Mac with Apple Silicon (M1 or later) and macOS 14 (Sonoma) or later
- About 8 GB of free disk space for the tools
- [Claude Code](https://claude.com/claude-code)
- A paid Claude plan: Pro or Max. The free plan does not include Claude
  Code. The step that cuts the retakes needs a strong model (Sonnet at high
  effort, or Opus), and both paid plans include it.

## Install

In Claude Code, type these two commands:

```
/plugin marketplace add ai-augmented-scholar/video-teach-plugin
/plugin install video-teach-plugin@video-teach-plugin
```

Then run setup once on each computer:

```
/video-teach-plugin:setup
```

Setup checks your Mac first and stops with a plain message if the plugin
cannot run on it. Then it installs what is missing: Homebrew (you paste one
command it gives you, because Homebrew asks for your Mac password), ffmpeg,
Node and uv, then the Parakeet transcription model, DeepFilterNet for noise
removal, and the renderer for text cards (about 500 MB). The tools go into a
private folder inside the plugin, so your own Python stays as it is. Setup
asks before every large download. It also asks where your videos live, which
editor you use, and the three questions below. It ends with a 10-second test
clip that runs through sound cleanup, transcription, one cut and a sound
check, with a pass or fail for each step.

The first transcription downloads the speech model (about 2.5 GB), so the
first test takes a few minutes. Running setup again is safe: it checks what is
already in place and skips it.

### Upgrading from teaching-video

Until version 2.0.0 this plugin was called `teaching-video`. If you installed
it under that name, remove the old marketplace, then install again with the
two commands above:

```
/plugin marketplace remove teaching-video
```

Then run `/video-teach-plugin:setup` once. The new name gets a new tools
folder, so setup installs the tools again and asks its questions again. Your
videos stay where they are; give setup the same videos folder as before.

## Three questions on first use

Before your first video, Claude asks three questions about your filming
setup and your look, and saves the answers.

1. **Where can text sit?** Is part of your frame empty, such as the wall
   beside you? Describe it, share a screenshot, or let Claude take
   frames from your recording. With no free area, the text goes into lower
   thirds along the bottom of the frame.
2. **Is that area dark enough for white text?** Claude measures it. On a
   light background, the white text gets a dark grey plate behind it.
3. **Which font?** The default is Inter, which comes with the plugin. Name
   another font and Claude checks that your Mac has it.

Each later video starts with one line: same filming setup as last time?

## Prompts to remember

- `/video-teach-plugin:make-video` runs the whole workflow. After you film it
  runs to the finished video without a stop, sends one Mac notification when
  the video is ready, and picks up where you left off after a break.
- `/video-teach-plugin:lecture-video` turns a recording, with or without a
  slide deck, into the finished lecture video with captions and a
  course-page description, with no stops.
- `/video-teach-plugin:setup` runs once per computer.
- `/video-teach-plugin:beatsheet` drafts a beat sheet for a lecture on any topic.

Plain sentences work too:

- "Clean up the sound on this recording."
- "Cut the retakes out of this take."
- "Make text cards from this transcript."
- "Here is my recording: make the lecture video."
- "Here is my lecture and the slides: make the video."
- "Put my slides next to me in this video."
- "Make short clips from this lecture."
- "Is this audio good enough to publish?"

## What it does not do

- It does not publish to your course site. Uploading is yours.
- It does not run on Windows or on an Intel Mac.
- It runs in Claude Code only, not in the other Claude apps.

## Questions and feedback

Write to The AI-Augmented Scholar at aiaugmentedscholar@gmail.com.

## License and Credits

**License.** The plugin is free under the MIT License, Copyright (c) 2026
The AI-Augmented Scholar. You can use it, change it and pass it on, also in
paid work. Keep the copyright notice and the license text with it. The full
text is in [LICENSE](LICENSE).

**Credits.** The plugin passes on work by these people. Thank you to each
of them.

- **Vic Laranja**, Systems by Vic: the skills `perfect-cuts` and
  `perfect-clips`. YouTube:
  [youtube.com/@systemsbyvic](https://www.youtube.com/@systemsbyvic). Web:
  [systemsbyvic.com](https://systemsbyvic.com). MIT License, Copyright (c)
  2026 Systems by Vic; the license ships in each skill's folder.
- **Shane Hummus**: the "Holy Trifecta" method behind `first-impression`
  (the title, the thumbnail and the opening make one promise). YouTube:
  [youtube.com/@ShaneHummus](https://www.youtube.com/@ShaneHummus). The skill
  is written new; his wording is not copied.
- **Hardik Pandya**: the stop-slop writing rules, adapted inside
  `perfect-clips`. Web: [hvpandya.com](https://hvpandya.com). Code:
  [github.com/hardikpandya/stop-slop](https://github.com/hardikpandya/stop-slop).
  MIT License, Copyright (c) 2025 Hardik Pandya.

**Tools under their own licenses.** Setup installs open-source tools that
keep their own licenses: Parakeet and parakeet-mlx for transcription,
DeepFilterNet for noise removal, pypdfium2 for slide decks, and ffmpeg.
The fonts Inter and Montserrat ship under the SIL Open Font License.

**Remotion**, which draws the text cards, is not MIT. It is free for
individuals, for companies with up to 3 employees, and for non-profit
organizations, universities included. A larger for-profit company needs a
Remotion company license: [remotion.dev/license](https://www.remotion.dev/license).

[CREDITS.md](CREDITS.md) lists every credit and license in full.
