"""Build the bot's description media — the block above START in an empty chat.

Telegram calls this the "description picture"; MTProto carries it as
botInfo.description_photo / botInfo.description_document ("Description animation
in MPEG4 format"), and customers see it in a block titled "What can this bot
do?" before they ever tap Start. The text of that screen is DESCRIPTION in
bot/profile.py; this is the picture beside it.

**There is no API for it.** Not Bot API (no method, and the changelog has never
mentioned one), not MTProto (bots.setBotInfo#10cf3123 is text-only and nothing
else writes those two fields), not TDLib — which gives the game away by exposing
photo_/animation_ read-only plus `edit_description_media_link_`, "the internal
link, which can be used to edit the photo or animation shown in the chat with
the bot if the chat is empty". Clients open @BotFather; they do not call a
method. So this script builds the file and a human uploads it, in the BotFather
panel under "Welcome message" -> "Set Welcome Picture".

**GIF, not mp4, and the sizes are enumerated.** Verbatim from that panel:

    Upload a photo for the bot's start page, 640x360 pixels. You can also use
    a GIF animation, 320x180, 640x360 or 960x540 pixels.
    People will see the description and image when they open a chat with your
    bot, in a block titled 'What can this bot do?'.

So an mp4 is not offered at all — an mp4 uploaded here does nothing, which is
how this was found out. The animated route is a GIF, which Telegram converts to
MPEG4 itself and stores in description_document. mp4s are still built below,
because core.telegram.org/bots/features says "a photo or video" for the older
BotFather chat flow, but the GIF is the deliverable.

Why 16:9 regardless: Android lays out a fixed 16:9 box and CENTRE-CROPS anything
else (BotHelpCell.java: `photoHeight = (int) (width * 0.5625) // 16:9`, with
ImageReceiver's isAspectFit defaulting to false), while iOS and Desktop keep the
real aspect. Every size BotFather names is already 16:9.

Why the mp4s carry no audio track: Telegram Desktop only treats an mp4 as an
*animation* if it has no audio stream, is at most 10MB and is H.264
(media_clip_ffmpeg.cpp isGifv()). With sound it goes as a video. Android has the
same rule via "muted".

Run: .venv/bin/python webapp/build_description.py
"""
from __future__ import annotations

import math
import pathlib
import subprocess

import imageio_ffmpeg
from PIL import Image

HERE = pathlib.Path(__file__).parent

# The sizes BotFather actually names, read off its own panel (see the quote in
# the docstring). 960x540 first: it is the largest ACCEPTED size, so it upscales
# least in Android's box (~810px wide on a 1080p phone). A 1280x720 master was
# built first and is not on the list — the enumeration is real, and overriding
# it with reasoning about upscaling was simply wrong.
SIZES = [(960, 540), (640, 360), (320, 180)]
W, H = SIZES[0]            # build() re-points these per size
FPS = 30
DURATION = 6.0
FRAMES = int(FPS * DURATION)
# The GIF is the robust route: Telegram converts an uploaded GIF to MPEG4
# itself, so it lands in description_document as an animation whatever the
# platform. An mp4 only becomes an animation if the sending client decides it
# is one — on mobile an attached mp4 goes as a video instead.
GIF_FPS = 15

# Composition, as fractions of the canvas. A HORIZONTAL lockup — mark left,
# wordmark right, both on the vertical centre line. birthday.png stacks them,
# but that card is 1.90:1 with the mark centred; stacking inside 16:9 measured
# out at 35% burgundy on each side, which is the "small logo in a big empty
# field" mistake the old avatar made. Side by side uses the width.
MARK_HEIGHT = 0.50         # of canvas height
WORD_WIDTH = 0.36          # of canvas width
LOCKUP_GAP = 0.05          # of canvas width, between mark and wordmark

# Same light as the avatar, so the two assets read as one system.
CROSS_FROM, CROSS_TO = 1.20, 3.80
BAND_HALF_WIDTH = 0.036    # of the canvas width; tuned to the lit-share cap
BAND_ANGLE = 135.0

BURGUNDY = (0x83, 0x15, 0x31)
LIME = (0xD0, 0xD1, 0x51)
PINK = (0xF7, 0xC9, 0xDF)
CREAM = (0xF9, 0xEF, 0xD6)


def highlight(base: tuple[int, int, int]) -> tuple[int, int, int]:
    """Where light lands on `base`: towards cream, but never darkening a channel.

    Lime -> cream is a brightening in all three channels, so for the mark this
    is simply cream. Pink is not: its blue is 0xDF against cream's 0xD6, so
    mixing in cream would *drain* the pink rather than light it. Taking the
    channel-wise maximum keeps the rule "light only ever brightens".
    """
    return tuple(max(base[i], CREAM[i]) for i in range(3))


def placed(name: str, box: tuple[int, int], centre: tuple[int, int]) -> Image.Image:
    """One of the brand masks, scaled to `box` and centred at `centre`."""
    m = Image.open(HERE / name).convert("L")
    m = m.crop(m.point(lambda v: 255 if v > 127 else 0).getbbox())
    m = m.resize(box, Image.LANCZOS)
    a = Image.new("L", (W, H), 0)
    a.paste(m, (centre[0] - box[0] // 2, centre[1] - box[1] // 2))
    return a


def masks() -> tuple[Image.Image, Image.Image]:
    """Mark and wordmark, as two alpha channels, laid out as one lockup."""
    fsrc = Image.open(HERE / "flower_trim.png")
    mh = round(MARK_HEIGHT * H)
    mw = round(mh * fsrc.size[0] / fsrc.size[1])
    wsrc = Image.open(HERE / "wordmark_trim.png")
    ww = round(WORD_WIDTH * W)
    wh = round(ww * wsrc.size[1] / wsrc.size[0])

    gap = round(LOCKUP_GAP * W)
    total = mw + gap + ww
    left = (W - total) // 2
    mark = placed("flower_trim.png", (mw, mh), (left + mw // 2, H // 2))
    word = placed("wordmark_trim.png", (ww, wh), (left + mw + gap + ww // 2, H // 2))
    return mark, word


def still(mark: Image.Image, word: Image.Image) -> Image.Image:
    img = Image.new("RGB", (W, H), BURGUNDY)
    img.paste(Image.new("RGB", (W, H), LIME), (0, 0), mark)
    img.paste(Image.new("RGB", (W, H), PINK), (0, 0), word)
    return img


def travel(u: float) -> float:
    """Slowest in the middle, as in build_avatar.py."""
    s = 2.0 * u - 1.0
    return 0.5 + 0.5 * math.copysign(abs(s) ** 1.4, s)


def band(u: float) -> Image.Image:
    half = BAND_HALF_WIDTH * W
    big = 2 * max(W, H)
    reach = math.hypot(W, H) / 2 + half
    centre = big / 2 - reach + u * 2 * reach
    col = Image.new("L", (1, big), 0)
    p = col.load()
    for y in range(big):
        d = abs(y - centre) / half
        p[0, y] = 0 if d >= 1.0 else round(255 * math.cos(d * math.pi / 2) ** 2)
    f = col.resize((big, big), Image.BILINEAR).rotate(BAND_ANGLE, resample=Image.BICUBIC)
    return f.crop(((big - W) // 2, (big - H) // 2, (big - W) // 2 + W, (big - H) // 2 + H))


def gif_palette() -> Image.Image:
    """A palette built from the ramps the artwork actually contains.

    A generic quantiser wastes entries: median-cut left the worst jump between
    adjacent interior pixels at 14/255 against the render's own floor of 5,
    which banded the light visibly — and raising it from 128 to 256 colours
    changed that number not at all, because the problem is allocation, not
    count. Spending the whole palette on the five ramps that exist — the two
    silhouette edges, the lit edge, and the two highlight ramps — lands on the
    floor: worst jump 5, mean error 0.05/255.
    """
    def ramp(a, b, n):
        return [tuple(round(a[j] + (b[j] - a[j]) * k / (n - 1)) for j in range(3))
                for k in range(n)]

    entries = (ramp(BURGUNDY, LIME, 12)      # antialiased mark edge
               + ramp(BURGUNDY, PINK, 12)    # antialiased wordmark edge
               + ramp(BURGUNDY, CREAM, 12)   # those edges with light on them
               + ramp(LIME, highlight(LIME), 110)
               + ramp(PINK, highlight(PINK), 110))[:256]
    pal = Image.new("P", (1, 1))
    flat = [c for e in entries for c in e]
    pal.putpalette(flat + [0] * (768 - len(flat)))
    return pal


def frame(i: int, base: Image.Image, mark: Image.Image,
          word: Image.Image) -> Image.Image:
    """One frame. The mark and the wordmark are lit separately, because their
    highlight colours differ — see highlight()."""
    t = i / FPS
    if not (CROSS_FROM <= t <= CROSS_TO):
        return base
    u = (t - CROSS_FROM) / (CROSS_TO - CROSS_FROM)
    light = band(travel(u))
    black = Image.new("L", (W, H), 0)
    out = base.copy()
    for element, colour in ((mark, LIME), (word, PINK)):
        out.paste(Image.new("RGB", (W, H), highlight(colour)), (0, 0),
                  Image.composite(light, black, element))
    return out


def one_size(size: tuple[int, int], suffix: str) -> None:
    global W, H
    W, H = size
    mark, word = masks()
    base = still(mark, word)
    frames = [frame(i, base, mark, word) for i in range(FRAMES)]

    jpg = HERE / f"description{suffix}.jpg"
    base.save(jpg, quality=95, subsampling=0, optimize=True)

    tmp = HERE / "description_frames"
    tmp.mkdir(exist_ok=True)
    for old in tmp.glob("*.png"):
        old.unlink()
    for i, f in enumerate(frames):
        f.save(tmp / f"{i:03d}.png")

    mp4 = HERE / f"description{suffix}.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                    "-framerate", str(FPS), "-i", str(tmp / "%03d.png"),
                    "-c:v", "libx264", "-preset", "veryslow", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-profile:v", "high",
                    "-g", str(FPS * 3), "-movflags", "+faststart",
                    # -an is load-bearing: an audio stream makes Telegram send
                    # this as a video instead of an animation, and the slot
                    # takes an animation.
                    "-an", str(mp4)], check=True)

    # One shared palette for every frame — a per-frame palette would make the
    # flat burgundy shimmer as the quantiser re-picks colours — and it is built
    # by hand rather than measured off the frames. See gif_palette().
    step = max(1, round(FPS / GIF_FPS))
    picked = frames[::step]
    palette = gif_palette()
    gif = HERE / f"description{suffix}.gif"
    seq = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in picked]
    seq[0].save(gif, save_all=True, append_images=seq[1:], loop=0,
                duration=round(1000 * step / FPS), optimize=True, disposal=1)

    print(f"description{suffix}: {W}x{H}  jpg {jpg.stat().st_size / 1024:5.0f} KB  "
          f"mp4 {mp4.stat().st_size / 1024:5.0f} KB  "
          f"gif {gif.stat().st_size / 1024:5.0f} KB ({len(seq)} frames)")


def build() -> None:
    # Always suffixed by size: an unsuffixed "description.mp4" is what got
    # uploaded into the avatar slot by mistake once already.
    for size in SIZES:
        one_size(size, f"_{size[0]}x{size[1]}")


if __name__ == "__main__":
    build()
