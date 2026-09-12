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
method. So this script builds the file and a human uploads it:

    @BotFather -> /mybots -> the bot -> Edit Bot -> Edit Description Picture
    (attach as Photo/Video, not as a file; /empty clears it)

Why the geometry is not negotiable: Telegram documents no size for this slot
anywhere, but Android lays out a fixed 16:9 box and CENTRE-CROPS anything else
(BotHelpCell.java: `photoHeight = (int) (width * 0.5625) // 16:9`, with
ImageReceiver's isAspectFit defaulting to false), while iOS and Desktop keep the
real aspect. Off-ratio artwork would therefore be cropped on one platform and
intact on another. 16:9 it is. 1280x720 rather than the 640x360 BotFather's own
prompt suggests, because Android sizes the box in screen pixels (~0.7 of the
short side, so ~810px wide on a 1080p phone) and 640 would upscale.

Why the mp4 is H.264 with no audio track: Telegram Desktop only treats an mp4 as
an *animation* if it has no audio stream, is at most 10MB and is H.264
(media_clip_ffmpeg.cpp isGifv()). With sound it is sent as a video and never
reaches this slot. Android has the same rule via "muted".

Run: .venv/bin/python webapp/build_description.py
"""
from __future__ import annotations

import math
import pathlib
import subprocess

import imageio_ffmpeg
from PIL import Image

HERE = pathlib.Path(__file__).parent

W, H = 1280, 720           # exactly 16:9
FPS = 30
DURATION = 6.0
FRAMES = int(FPS * DURATION)

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


def build() -> None:
    mark, word = masks()
    base = still(mark, word)

    jpg = HERE / "description.jpg"
    base.save(jpg, quality=95, subsampling=0, optimize=True)

    frames = HERE / "description_frames"
    frames.mkdir(exist_ok=True)
    for old in frames.glob("*.png"):
        old.unlink()
    for i in range(FRAMES):
        frame(i, base, mark, word).save(frames / f"{i:03d}.png")

    mp4 = HERE / "description.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                    "-framerate", str(FPS), "-i", str(frames / "%03d.png"),
                    "-c:v", "libx264", "-preset", "veryslow", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-profile:v", "high",
                    "-g", str(FPS * 3), "-movflags", "+faststart",
                    # -an is load-bearing: an audio stream makes Telegram send
                    # this as a video instead of an animation, and the slot
                    # takes an animation.
                    "-an", str(mp4)], check=True)

    print(f"description.jpg  {W}x{H}  {jpg.stat().st_size / 1024:6.0f} KB")
    print(f"description.mp4  {W}x{H}  {mp4.stat().st_size / 1024:6.0f} KB  "
          f"{DURATION:g}s @ {FPS}fps")


if __name__ == "__main__":
    build()
