#!/usr/bin/env python3
"""Export a .pptx to PDF with whatever this Mac has: PowerPoint, else LibreOffice.

Usage:
    python3 deck_to_pdf.py DECK.pptx [--out DECK.pdf]

Exit codes: 0 the PDF is written; 1 the export failed; 3 neither PowerPoint
nor LibreOffice is installed (lecture-video then looks for a PDF of the deck
with the same name beside the .pptx, and otherwise stops and asks for one:
in PowerPoint or Keynote, File > Export > PDF).

Microsoft PowerPoint renders the deck exactly as its author sees it (fonts,
layouts, images), so it comes first. The script:

  1. copies the deck to a temporary folder, so the original is never opened
     and a presentation of the same name that is already open cannot clash;
  2. asks PowerPoint, through AppleScript, to open that copy and save it as
     PDF;
  3. closes only that copy, without saving, and leaves PowerPoint and any
     other open presentation as they were.

Pass the .pptx to slides_prepare.py as --notes-from afterwards: the PDF has no
speaker notes, the .pptx does. Builds do not survive a PDF export; one page
per build step does.

The first run may ask for permission to let the terminal control PowerPoint
(System Settings > Privacy & Security > Automation). PowerPoint may also show
its own "Grant File Access" dialog, which blocks every Apple event: that is a
security prompt for the user, so the script never clicks it; it says so and
exits, and lecture-video falls back to a PDF beside the deck.

Without PowerPoint, LibreOffice (soffice) converts the deck headless. Its
layout can differ a little from PowerPoint's (fonts it does not have are
replaced). Nothing is installed by this script. Stdlib only.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APP = "Microsoft PowerPoint"


def soffice():
    for p in (shutil.which("soffice"), "/Applications/LibreOffice.app/Contents/MacOS/soffice",
              os.path.expanduser("~/Applications/LibreOffice.app/Contents/MacOS/soffice")):
        if p and os.path.exists(p):
            return p
    return None


def via_libreoffice(deck, out):
    exe = soffice()
    with tempfile.TemporaryDirectory(prefix="deck-to-pdf-") as tmp:
        copy = Path(tmp) / "lecture-deck.pptx"
        shutil.copyfile(deck, copy)
        r = subprocess.run([exe, "--headless", "--convert-to", "pdf", "--outdir", tmp, str(copy)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600)
        pdf = Path(tmp) / "lecture-deck.pdf"
        if r.returncode != 0 or not pdf.is_file() or pdf.stat().st_size == 0:
            sys.exit("ERROR: LibreOffice wrote no PDF: %s"
                     % r.stderr.decode("utf-8", "replace").strip()[-300:])
        shutil.copyfile(pdf, out)
    print("PDF (LibreOffice) -> %s (%d bytes)" % (out, out.stat().st_size))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("deck")
    ap.add_argument("--out", default=None, help="PDF to write (default: beside the deck)")
    ap.add_argument("--timeout", type=int, default=330)
    a = ap.parse_args()

    deck = Path(a.deck).expanduser().resolve()
    if deck.suffix.lower() != ".pptx" or not deck.is_file():
        sys.exit("ERROR: give the path of a .pptx file.")
    out = Path(a.out).expanduser().resolve() if a.out else deck.with_suffix(".pdf")
    has_ppt = any(Path(p).exists() for p in ("/Applications/%s.app" % APP,
                                              os.path.expanduser("~/Applications/%s.app" % APP)))
    if not has_ppt:
        if soffice():
            via_libreoffice(deck, out)
            return
        print("ERROR: neither Microsoft PowerPoint nor LibreOffice is installed, so the .pptx "
              "cannot be turned into a PDF here. Export the deck as PDF (PowerPoint or Keynote: "
              "File > Export > PDF) and put the PDF beside the .pptx.")
        sys.exit(3)

    # PowerPoint (sandboxed) sometimes opens a "Grant File Access" dialog and waits for a click.
    # Every Apple event then fails (error -9074) or times out (-1712). That dialog is a security
    # prompt for the user: this script never clicks it, it only names it.
    blocked = subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to tell process "%s" to get name of every window' % APP],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20).stdout.decode("utf-8", "replace")
    if "Grant File Access" in blocked:
        sys.exit("ERROR: PowerPoint is waiting on its \"Grant File Access\" dialog. Click it once "
                 "(Grant Access, or Cancel), then run again. Nothing was exported. A PDF of the "
                 "deck with the same name, beside the .pptx, is used instead when there is one.")

    with tempfile.TemporaryDirectory(prefix="deck-to-pdf-") as tmp:
        copy = Path(tmp) / "lecture-deck.pptx"
        pdf = Path(tmp) / "lecture-deck.pdf"
        shutil.copyfile(deck, copy)
        # A long Apple event timeout (the default 120 s once ran out while PowerPoint was busy),
        # and one retry: a presentation left open by the first try is closed first.
        script = '''
with timeout of 300 seconds
  tell application "%s"
    try
      close presentation "lecture-deck.pptx" saving no
    end try
    open POSIX file "%s"
    set p to presentation "lecture-deck.pptx"
    save p in POSIX file "%s" as save as PDF
    close p saving no
  end tell
end timeout
return "done"
''' % (APP, copy, pdf)
        r = None
        for attempt in (1, 2):
            try:
                r = subprocess.run(["osascript", "-e", script], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=a.timeout)
            except subprocess.TimeoutExpired:
                if attempt == 2:
                    sys.exit("ERROR: PowerPoint did not answer in %d s, twice. Look for a dialog "
                             "in PowerPoint (a permission or file prompt) and run again." % a.timeout)
                continue
            if r.returncode == 0:
                break
            if attempt == 2:
                sys.exit("ERROR: PowerPoint refused twice: %s"
                         % r.stderr.decode("utf-8", "replace").strip())
        if not pdf.is_file() or pdf.stat().st_size == 0:
            sys.exit("ERROR: PowerPoint wrote no PDF.")
        shutil.copyfile(pdf, out)
    print("PDF -> %s (%d bytes). Next: slides_prepare.py %s --notes-from %s" % (
        out, out.stat().st_size, out, deck))


if __name__ == "__main__":
    main()
