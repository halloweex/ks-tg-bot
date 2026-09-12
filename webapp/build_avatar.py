"""Build the bot's avatar — a still logo, and a slow-light loop of that logo.

Why the still one is the real deliverable: a bot cannot hold Telegram Premium,
and every official client gates avatar animation on the *photo owner's* premium
flag (tdesktop `validateVideoUserpic()` wants `peer->isPremium()`, Android
`ImageLocation.getForUser()` wants `isPremiumUser(user)`, iOS/macOS/Web K the
same). So the loop plays only on the profile screen and in the full-screen
avatar viewer, and power-saving kills it even there. The frame at
`main_frame_timestamp` is what every customer sees in the chat list, in the chat
header and beside every message — so frame 0 is a clean mark, and the motion
never touches the geometry: all 180 frames reuse one mark raster and only
composite light over it.

The mark is the brand's own flower mask, the same one `build_cards.py` paints.
Three numbers below are measured off that mask rather than assumed:

  * its centroid sits on its bounding-box centre (offset -0.02%, -0.09% of the
    box), so geometric centring *is* optical centring. No nudge is applied.
  * the farthest ink is 0.556 of the box width from the centre, not the
    half-diagonal 0.663 — the petals stop short of the corners. At
    MARK_FRACTION the ink clears Telegram's circular crop by 13%.
  * the petal gaps are radial notches at 87 and 267 degrees; coverage along a
    45-degree ray is 1.00, i.e. solid petal. A translating band cannot "enter
    through" a radial notch, so the band's direction is an aesthetic choice,
    not a geometric one: rendered side by side, an axis-aligned band reads as a
    stripe cutting the mark in half, while a band along the petal axis reads as
    light running down the petals. Hence BAND_ANGLE.

Run: .venv/bin/python webapp/build_avatar.py
Set: see webapp/set_avatar.py
"""
from __future__ import annotations

import math
import pathlib
import subprocess

import imageio_ffmpeg
from PIL import Image

HERE = pathlib.Path(__file__).parent

# 800x800 is Telegram's own recommendation for a profile video
# (core.telegram.org/api/files); TDLib caps the envelope at square, <=10s,
# width 160-1280, <=2MB. This lands far inside all of it.
CANVAS = 800
STILL = 1024
FPS = 30
DURATION = 6.0
FRAMES = int(FPS * DURATION)

# Fraction of the canvas taken by the mark's BOUNDING BOX. The canvas is the
# square; Telegram shows the inscribed circle.
MARK_FRACTION = 0.78

# The crossing sits in the middle of the loop; the rest is the clean mark, so
# the loop closes on itself without any symmetry trick.
CROSS_FROM, CROSS_TO = 1.20, 3.80
# Half-width of the light band, as a fraction of the mark's box width. Tuned
# down from 0.30 until the lit area at the peak came under 30% of the mark.
BAND_HALF_WIDTH = 0.103
# Orientation of the bright stripe: along the petal axis, upper-left to
# lower-right, the conventional direction for a highlight.
BAND_ANGLE = 135.0

BURGUNDY = (0x83, 0x15, 0x31)
LIME = (0xD0, 0xD1, 0x51)
CREAM = (0xF9, 0xEF, 0xD6)


def mark_alpha(canvas: int) -> Image.Image:
    """The flower mask, scaled and centred, as an alpha channel.

    `build_cards.py` uses the same masks the same way: intensity becomes
    opacity, which is what its `-compose CopyOpacity` does.
    """
    m = Image.open(HERE / "flower_trim.png").convert("L")
    m = m.crop(m.point(lambda v: 255 if v > 127 else 0).getbbox())
    w = round(MARK_FRACTION * canvas)
    h = round(w * m.size[1] / m.size[0])
    m = m.resize((w, h), Image.LANCZOS)
    a = Image.new("L", (canvas, canvas), 0)
    a.paste(m, ((canvas - w) // 2, (canvas - h) // 2))
    return a


def still(canvas: int) -> Image.Image:
    """Lime mark on burgundy, edge to edge. No ring, no border, no shadow."""
    img = Image.new("RGB", (canvas, canvas), BURGUNDY)
    img.paste(Image.new("RGB", (canvas, canvas), LIME), (0, 0), mark_alpha(canvas))
    return img


def travel(u: float) -> float:
    """Position along the crossing, on 0..1. Slowest in the middle.

    The panel asked for cubic-bezier(0.33, 0, 0.67, 1) *and* for the light to be
    slowest at mid-crossing; that curve is the fast-in-the-middle one, so the
    intent wins over the curve. An exponent above 1 flattens the centre, and the
    band lingers over the mark instead of whipping across it — the opposite of
    the placeholder-shimmer rhythm. 1.4 rather than 1.7: at 1.7 the measured
    mid-crossing speed fell to 0.12x of average, which reads as a stall.
    """
    s = 2.0 * u - 1.0
    return 0.5 + 0.5 * math.copysign(abs(s) ** 1.4, s)


def band(canvas: int, u: float) -> Image.Image:
    """The light layer at crossing position `u`: a cosine-squared soft band.

    Built as a one-pixel column and stretched, then rotated — the field depends
    only on distance along the travel axis, so this costs one resize and one
    rotate instead of a per-pixel loop.
    """
    half = BAND_HALF_WIDTH * MARK_FRACTION * canvas
    big = 2 * canvas
    # Travel far enough that the band is wholly outside the visible square at
    # both ends: the square's projection on the travel axis is canvas*sqrt(2).
    reach = canvas / math.sqrt(2) + half
    centre = big / 2 - reach + u * 2 * reach
    col = Image.new("L", (1, big), 0)
    p = col.load()
    for y in range(big):
        d = abs(y - centre) / half
        p[0, y] = 0 if d >= 1.0 else round(255 * math.cos(d * math.pi / 2) ** 2)
    f = col.resize((big, big), Image.BILINEAR).rotate(BAND_ANGLE, resample=Image.BICUBIC)
    o = (big - canvas) // 2
    return f.crop((o, o, o + canvas, o + canvas))


def frame(i: int, base: Image.Image, alpha: Image.Image) -> Image.Image:
    """One frame: the still, with light over it only where the mark is."""
    t = i / FPS
    if not (CROSS_FROM <= t <= CROSS_TO):
        return base
    u = (t - CROSS_FROM) / (CROSS_TO - CROSS_FROM)
    lit = Image.composite(band(CANVAS, travel(u)),
                          Image.new("L", (CANVAS, CANVAS), 0), alpha)
    out = base.copy()
    # Clamped at cream by construction: the highlight interpolates lime to
    # cream and nothing brighter than cream exists in the pipeline.
    out.paste(Image.new("RGB", (CANVAS, CANVAS), CREAM), (0, 0), lit)
    return out


def build() -> None:
    alpha = mark_alpha(CANVAS)
    base = still(CANVAS)

    # The still avatar. .JPG because that is what InputProfilePhotoStatic
    # accepts; 4:4:4 because lime on burgundy is a strong chroma edge and 4:2:0
    # smears exactly that.
    jpg = HERE / "avatar.jpg"
    still(STILL).save(jpg, quality=95, subsampling=0, optimize=True)

    frames = HERE / "avatar_frames"
    frames.mkdir(exist_ok=True)
    for old in frames.glob("*.png"):
        old.unlink()
    for i in range(FRAMES):
        frame(i, base, alpha).save(frames / f"{i:03d}.png")

    mp4 = HERE / "avatar.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                    "-framerate", str(FPS), "-i", str(frames / "%03d.png"),
                    "-c:v", "libx264", "-preset", "veryslow", "-crf", "16",
                    "-pix_fmt", "yuv420p", "-profile:v", "high",
                    "-g", str(FPS * 3), "-movflags", "+faststart",
                    "-an", str(mp4)], check=True)

    print(f"avatar.jpg  {STILL}x{STILL}  {jpg.stat().st_size / 1024:6.0f} KB")
    print(f"avatar.mp4  {CANVAS}x{CANVAS}  {mp4.stat().st_size / 1024:6.0f} KB  "
          f"{DURATION:g}s @ {FPS}fps, {FRAMES} frames")


if __name__ == "__main__":
    build()
