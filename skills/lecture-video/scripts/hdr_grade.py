#!/usr/bin/env python3
"""hdr_grade.py — turn phone HDR (HLG) footage into graded SDR Rec. 709.

Many phones (iPhones by default) record HDR video: HEVC 10-bit, HLG transfer,
BT.2020 colour. In an ordinary Rec. 709 edit that looks flat and grey, and the
lecture chain's crops and plates are Rec. 709. This script converts it and
applies a mild grade in one pass, and writes a new file beside the source. The
source is never changed. lecture-video calls it by itself for an HLG clip and
searches the settings (see lecture_run.py); run it by hand only to try one.

  python3 hdr_grade.py CLIP.MOV                  grade with the default settings
  python3 hdr_grade.py CLIP.MOV --preview 300    before/after still at 5:00, no render
  python3 hdr_grade.py CLIP.MOV --white-ref 880,1035,140,55@300
                                                 white balance from a white object
                                                 (x,y,w,h in the upright frame, at
                                                 that second)

Grade settings:
  --wb-strength 0      0 = no white balance (the default), 1 = the white
                       reference exactly neutral. Below 1 keeps some room warmth.
  --exposure 1.0       linear gain; raising it clips bright walls and slides first
  --midtone 0.88       below 1 lifts faces and walls, black and white stay fixed
  --contrast 0.28      S-curve strength, 0 = none
  --saturation 0.94

The defaults lift midtones, which suits a dim, warm room. On a bright white
wall in neutral light they push the wall and face highlights to white; there
--exposure 0.45 --midtone 0.95 --contrast 0.15 holds them (the wall near luma
200, the face near 145). Measure the --preview PNG before a full run. A clipped
white reference is a reason for --wb-strength 0.

Why a LUT: a stock Homebrew ffmpeg has no zscale and no HLG tone mapping, so
the conversion is computed from the BT.2100 formulas into a 65-point 3D LUT and
applied with ffmpeg's lut3d. The colour tags are rewritten in the bitstream
with hevc_metadata, because ffmpeg otherwise keeps the input's HLG tags. The
run is checked: same frame count as the source, Rec. 709 tags. Output:
<stem>-graded.mov, HEVC 10-bit 40 Mbit/s (VideoToolbox), audio copied.

Needs numpy: run it with the plugin's own Python, $TV_DATA/venv/bin/python
(setup installs numpy there). PQ (HDR10 / Dolby Vision) footage is not
handled: export it as SDR from the phone or the editor.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

os.environ["PATH"] = os.pathsep.join(
    [os.environ.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin"])

A, B, C = 0.17883277, 0.28466892, 0.55991073
WHITE_CODE = 0.90   # SDR code value for HLG reference white (signal 0.75)
KNEE = 0.60         # linear SDR level where the highlight shoulder starts
M2020_TO_709 = np.array([
    [1.6605, -0.5876, -0.0728],
    [-0.1246, 1.1329, -0.0083],
    [-0.0182, -0.1006, 1.1187],
])
# White reference used when none is measured: neutral, so the default
# white balance changes nothing (wb_strength 0 makes it irrelevant anyway).
DEFAULT_WHITE = (220.0, 220.0, 220.0)


def die(msg):
    sys.exit("hdr_grade: " + msg)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def probe(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height,color_transfer,color_primaries:stream_side_data=rotation",
               "-of", "json", str(path)], stdout=subprocess.PIPE).stdout
    return json.loads(out)["streams"][0]


def frame_count(path, decode=False):
    """Packets by default (fast). decode=True counts the frames a player shows:
    a clip cut with -c copy keeps pre-roll packets that are never displayed."""
    what = ("-count_frames", "nb_read_frames") if decode else ("-count_packets", "nb_read_packets")
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", what[0],
               "-show_entries", "stream=" + what[1], "-of", "csv=p=0", str(path)],
              stdout=subprocess.PIPE, text=True).stdout
    return int(out.strip().strip(","))


# ---- the conversion ---------------------------------------------------------

def hlg_inv_oetf(e):
    e = np.clip(e, 0, 1)
    return np.where(e <= 0.5, e * e / 3.0, (np.exp((e - C) / A) + B) / 12.0)


def ootf(rgb, gamma=1.2):
    y = 0.2627 * rgb[..., 0] + 0.6780 * rgb[..., 1] + 0.0593 * rgb[..., 2]
    return rgb * (np.maximum(y, 1e-9) ** (gamma - 1.0))[..., None]


def shoulder(x):
    span = 1.0 - KNEE
    over = np.maximum(x - KNEE, 0)
    return np.where(x <= KNEE, x, KNEE + span * (1 - np.exp(-over / span)))


def convert(rgb, g):
    """HLG BT.2020 R'G'B' (0..1) -> graded Rec. 709 R'G'B' (0..1)."""
    lin = ootf(hlg_inv_oetf(rgb))
    ref = ootf(hlg_inv_oetf(np.array([[0.75, 0.75, 0.75]])))[0, 0]
    lin = lin * (WHITE_CODE ** 2.4) / ref
    lin = np.clip(lin @ M2020_TO_709.T, 0, None)
    w = g["white"]
    gains = np.array([(w[1] / w[0]) ** 2.4, 1.0, (w[1] / w[2]) ** 2.4]) ** g["wb_strength"]
    lin = shoulder(lin * gains * g["exposure"])
    v = np.clip(lin, 0, 1) ** (1 / 2.4)
    v = v ** g["midtone"]
    v = (1 - g["contrast"]) * v + g["contrast"] * (v * v * (3 - 2 * v))
    y = (0.2126 * v[..., 0] + 0.7152 * v[..., 1] + 0.0722 * v[..., 2])[..., None]
    return np.clip(y + (v - y) * g["saturation"], 0, 1)


def write_lut(path, g, n=65):
    s = np.linspace(0, 1, n)
    b, gg, r = np.meshgrid(s, s, s, indexing="ij")   # .cube: red fastest
    out = convert(np.stack([r, gg, b], axis=-1).reshape(-1, 3), g)
    with open(path, "w") as f:
        f.write('TITLE "hdr-grade"\nLUT_3D_SIZE %d\n' % n)
        np.savetxt(f, out, fmt="%.6f")


IN_RGB = "scale=in_color_matrix=bt2020:in_range=tv:out_range=pc,format=gbrp16le"


def grab(path, t, vf, w, h):
    """One frame at second t through filter vf, as an h x w x 3 float array (0..255)."""
    raw = run(["ffmpeg", "-nostdin", "-v", "error", "-ss", str(t), "-i", str(path),
               "-frames:v", "1", "-vf", vf + ",format=rgb24", "-f", "rawvideo", "-"],
              stdout=subprocess.PIPE).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(float)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip")
    ap.add_argument("-o", "--out")
    ap.add_argument("--preview", type=float, metavar="SECONDS",
                    help="write a before/after PNG at this second and stop")
    ap.add_argument("--white-ref", metavar="X,Y,W,H@SECONDS")
    ap.add_argument("--wb-strength", type=float, default=0.0)
    ap.add_argument("--exposure", type=float, default=1.0)
    ap.add_argument("--midtone", type=float, default=0.88)
    ap.add_argument("--contrast", type=float, default=0.28)
    ap.add_argument("--saturation", type=float, default=0.94)
    ap.add_argument("--bitrate", default="40M")
    a = ap.parse_args()

    src = Path(a.clip).resolve()
    if not src.exists():
        die("no such file: %s" % src)
    st = probe(src)
    if st.get("color_transfer") != "arib-std-b67":
        die("%s is not HLG HDR (transfer: %s). Nothing to convert."
            % (src.name, st.get("color_transfer", "unknown")))
    w, h = st["width"], st["height"]
    rot = 0
    for sd in st.get("side_data_list", []):
        rot = int(sd.get("rotation", rot) or 0)
    if rot in (90, -90, 270, -270):
        w, h = h, w   # ffmpeg turns the picture upright before the filters

    g = dict(white=DEFAULT_WHITE, wb_strength=a.wb_strength, exposure=a.exposure,
             midtone=a.midtone, contrast=a.contrast, saturation=a.saturation)
    tmp = Path(tempfile.mkdtemp(prefix="hdr-grade-"))
    lut = tmp / "grade.cube"

    if a.white_ref:
        try:
            box, t = a.white_ref.split("@")
            x, y, bw, bh = (int(v) for v in box.split(","))
            t = float(t)
        except ValueError:
            die("--white-ref wants X,Y,W,H@SECONDS, e.g. 880,1035,140,55@300")
        plain = dict(g, wb_strength=0.0, exposure=1.0, midtone=1.0, contrast=0.0, saturation=1.0)
        write_lut(lut, plain)
        img = grab(src, t, "%s,lut3d=file='%s'" % (IN_RGB, lut), w, h)
        patch = img[y:y + bh, x:x + bw].reshape(-1, 3)
        if not len(patch):
            die("--white-ref box is outside the %dx%d frame" % (w, h))
        g["white"] = tuple(patch.mean(0))
        print("white reference measured: R %.0f  G %.0f  B %.0f" % g["white"])
        if max(g["white"]) > 250:
            print("WARNING: the white reference is clipped; pick a white object that is not blown out")

    write_lut(lut, g)
    graded_vf = "%s,lut3d=file='%s',format=rgb24" % (IN_RGB, lut)

    if a.preview is not None:
        before = grab(src, a.preview, "null", w, h)   # how a plain player shows it
        after = grab(src, a.preview, graded_vf, w, h)
        both = np.concatenate([before, after], axis=1).astype(np.uint8)
        out = src.with_name(src.stem + "-grade-preview.png")
        run(["ffmpeg", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "%dx%d" % (2 * w, h),
             "-i", "-", "-y", str(out)], input=both.tobytes())
        print("preview (left: before, right: after): %s" % out)
        return

    out = Path(a.out).resolve() if a.out else src.with_name(src.stem + "-graded.mov")
    if out.exists():
        die("%s exists; move it away or pass -o" % out.name)
    # setparams: in one encoding pass the encoder takes the colour tags from the
    # frames, which still carry the source's HLG/BT.2020 tags. -color_trc alone
    # is ignored.
    vf = ("%s,lut3d=file='%s',scale=out_color_matrix=bt709:out_range=tv,format=p010le,"
          "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv" % (IN_RGB, lut))
    print("grading %s -> %s" % (src.name, out.name))
    run(["ffmpeg", "-nostdin", "-v", "error", "-stats", "-i", str(src),
         "-map", "0:v:0", "-map", "0:a?", "-vf", vf, "-fps_mode", "passthrough",
         "-c:v", "hevc_videotoolbox", "-profile:v", "main10", "-b:v", a.bitrate, "-tag:v", "hvc1",
         "-bsf:v", "hevc_metadata=colour_primaries=1:transfer_characteristics=1:"
                   "matrix_coefficients=1:video_full_range_flag=0",
         "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
         "-c:a", "copy", "-movflags", "+faststart", str(out)])

    n_in, n_out = frame_count(src), frame_count(out)
    if n_in != n_out:
        print("packet counts differ (%d vs %d); counting decoded frames" % (n_in, n_out))
        n_in, n_out = frame_count(src, decode=True), frame_count(out, decode=True)
    tags = probe(out)
    ok = n_in == n_out and tags.get("color_transfer") == "bt709"
    print("frames: source %d, graded %d" % (n_in, n_out))
    print("tags:   %s / %s" % (tags.get("color_primaries"), tags.get("color_transfer")))
    if not ok:
        die("CHECK FAILED — do not use %s" % out.name)
    print("OK  %s" % out)


if __name__ == "__main__":
    main()
