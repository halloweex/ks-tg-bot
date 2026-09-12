"""Put the built avatar on the bot, or take it off again.

A one-off operation, not runtime code. Telegram stores the profile photo
server-side and it survives deploys, so this does not belong in
`bot/profile.py`: `apply()` runs on every start, and a profile photo cannot be
reused by file_id ("Profile photos can't be reused and can only be uploaded as
a new file"), so putting it there would re-upload the same file on every
restart. `webapp/` is not copied into the image (Dockerfile:19-21), which is
exactly where a tool like this should live.

    .venv/bin/python webapp/set_avatar.py probe
    .venv/bin/python webapp/set_avatar.py set webapp/avatar.mp4 --yes
    .venv/bin/python webapp/set_avatar.py remove --yes

`probe` only reads, and saves whatever avatar is there now next to this file —
`set` overwrites it and Telegram keeps no copy, so the backup is the only way
back. `set` and `remove` refuse to run without --yes.

Needs BOT_TOKEN in the environment or in .env.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

from aiogram import Bot
from aiogram.types import (FSInputFile, InputProfilePhotoAnimated,
                           InputProfilePhotoStatic)

HERE = pathlib.Path(__file__).parent
ROOT = HERE.parent
# Telegram's documented envelope for a profile video: square MPEG4, up to
# 1080x1080 (core.telegram.org/api/files, 800x800 recommended); TDLib's
# inputChatPhotoAnimation adds "at most 10 seconds, width 160-1280, at most
# 2MB". The Bot API page itself documents no limits at all, so these are the
# tightest numbers anyone states.
MAX_BYTES = 2 * 1024 * 1024
MAX_SECONDS = 10.0
MIN_SIDE, MAX_SIDE = 160, 1280


def token() -> str:
    import os
    if os.environ.get("BOT_TOKEN"):
        return os.environ["BOT_TOKEN"]
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "BOT_TOKEN":
                return value.strip()
    sys.exit("BOT_TOKEN is not set, and .env has no BOT_TOKEN line.")


def preflight(path: pathlib.Path) -> None:
    """Catch a file Telegram would reject, before spending the upload on it."""
    size = path.stat().st_size
    print(f"  {path.name}: {size / 1024:.0f} KB")
    if size > MAX_BYTES:
        sys.exit(f"  too big: {size} bytes, limit {MAX_BYTES}")
    if path.suffix.lower() != ".mp4":
        return
    try:
        import imageio_ffmpeg
    except ImportError:
        print("  (imageio-ffmpeg missing, skipping the video checks)")
        return
    meta = next(imageio_ffmpeg.read_frames(str(path)))
    w, h = meta["size"]
    dur = meta.get("duration") or 0.0
    print(f"  {w}x{h}, {dur:.2f}s")
    if w != h:
        sys.exit("  not square")
    if not MIN_SIDE <= w <= MAX_SIDE:
        sys.exit(f"  side {w} outside {MIN_SIDE}-{MAX_SIDE}")
    if dur > MAX_SECONDS:
        sys.exit(f"  {dur:.2f}s is over the {MAX_SECONDS:g}s limit")


async def probe(bot: Bot) -> None:
    me = await bot.get_me()
    print(f"bot: @{me.username}  id {me.id}")
    photos = await bot.get_user_profile_photos(user_id=me.id, limit=1)
    print(f"profile photos on record: {photos.total_count}")
    if not photos.photos:
        print("no avatar set — nothing to overwrite, nothing to back up")
        return
    biggest = max(photos.photos[0], key=lambda s: s.width * s.height)
    print(f"current: {biggest.width}x{biggest.height}  file_id {biggest.file_id[:24]}...")
    out = HERE / f"avatar_backup_{biggest.file_unique_id}.jpg"
    await bot.download(biggest, destination=out)
    print(f"backed up to {out.relative_to(ROOT)}  ({out.stat().st_size / 1024:.0f} KB)")
    print("NOTE: this is Telegram's re-encoded copy, and only the still frame. "
          "An animated avatar cannot be downloaded back through the Bot API.")


async def put(bot: Bot, path: pathlib.Path) -> None:
    if path.suffix.lower() == ".mp4":
        # main_frame_timestamp=0.0 is also the default; it is named here because
        # frame 0 is the deliberate clean-mark frame and every small surface
        # shows it rather than the loop.
        photo = InputProfilePhotoAnimated(animation=FSInputFile(path),
                                          main_frame_timestamp=0.0)
    else:
        photo = InputProfilePhotoStatic(photo=FSInputFile(path))
    print(f"setMyProfilePhoto -> {type(photo).__name__}: {await bot.set_my_profile_photo(photo=photo)}")


async def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--yes"]
    yes = "--yes" in sys.argv
    if not args or args[0] not in ("probe", "set", "remove"):
        sys.exit(__doc__)
    bot = Bot(token())
    try:
        if args[0] == "probe":
            await probe(bot)
            return
        if args[0] == "set":
            if len(args) < 2:
                sys.exit("set needs a file")
            path = pathlib.Path(args[1])
            print(f"about to replace the live bot's avatar with {path}:")
            preflight(path)
            if not yes:
                sys.exit("refusing without --yes (run `probe` first to back up "
                         "whatever is there now)")
            await put(bot, path)
        else:
            print("about to remove the live bot's avatar, leaving it with none")
            if not yes:
                sys.exit("refusing without --yes")
            print(f"removeMyProfilePhoto: {await bot.remove_my_profile_photo()}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
