"""Build the two cards the bot sends, from the brand's own artwork.

The mark and the wordmark are lifted off the identity pages of the 2026
strategy deck (masks in this directory) and repainted; the body copy is set in
Helvetica, because the deck embeds no font that can be extracted and a wrong
serif is more visibly wrong than a neutral grotesque.

Rendered with PyMuPDF rather than ImageMagick: the ImageMagick on this machine
is built without FreeType and cannot draw text at all.
"""
from __future__ import annotations

import pathlib
import subprocess

import pymupdf

W, H = 1200, 630
PINK, BURGUNDY, LIME = "#F7C9DF", "#831531", "#D0D151"
REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"
BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
HERE = pathlib.Path(__file__).parent


def rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def tinted(mask: str, colour: str, out: str, opacity: float = 1.0) -> str:
    """One of the masks, painted and optionally faded, as a transparent PNG."""
    size = subprocess.run(["magick", "identify", "-format", "%wx%h", str(HERE / mask)],
                          capture_output=True, text=True).stdout
    cmd = ["magick", "-size", size, f"xc:{colour}", str(HERE / mask),
           "-alpha", "off", "-compose", "CopyOpacity", "-composite"]
    if opacity < 1:
        cmd += ["-alpha", "set", "-channel", "A", "-evaluate", "multiply",
                str(opacity), "+channel"]
    cmd.append(str(HERE / out))
    subprocess.run(cmd, check=True)
    return str(HERE / out)


def card(background: str, blocks, images, out: pathlib.Path, quality: int = 92) -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.draw_rect(pymupdf.Rect(0, 0, W, H), color=None, fill=rgb(background))
    for path, rect in images:
        page.insert_image(pymupdf.Rect(*rect), filename=path, keep_proportion=True)
    for text, rect, size, colour, font, align in blocks:
        page.insert_textbox(pymupdf.Rect(*rect), text, fontsize=size,
                            fontname="brand" + font[-8:-4], fontfile=font,
                            color=rgb(colour), align=align, lineheight=1.25)
    pix = page.get_pixmap(dpi=72)
    # Through PNG and ImageMagick rather than pix.pil_save: Pillow is not
    # installed here, and the JPEG is only needed because the Bot API insists
    # an inline photo result is one.
    png = out.with_suffix(".png")
    pix.save(png)
    if out.suffix == ".jpg":
        subprocess.run(["magick", str(png), "-quality", str(quality), str(out)],
                       check=True)
        png.unlink()


# --- the invitation: what the friend is being offered, said on the card -----
card(
    PINK,
    blocks=[
        ("−10% на перше\nзамовлення", (72, 195, 760, 380), 62, BURGUNDY, BOLD, 0),
        ("Бот, який показує твої замовлення, нагадує про улюблені засоби "
         "й пише, щойно вони знову зʼявляються.",
         (74, 420, 700, 560), 27, BURGUNDY, REGULAR, 0),
    ],
    images=[
        (tinted("flower_trim.png", BURGUNDY, "f_faded.png", 0.20), (820, 115, 1160, 411)),
        (tinted("wordmark_trim.png", BURGUNDY, "w_burgundy.png"), (72, 64, 332, 114)),
    ],
    out=HERE / "invite_card.jpg",
)

# --- the birthday card: the greeting is the caption, the card is the brand ---
card(
    BURGUNDY,
    blocks=[],
    images=[
        (tinted("flower_trim.png", LIME, "f_lime.png"), (428, 87, 772, 387)),
        (tinted("wordmark_trim.png", PINK, "w_pink.png"), (430, 477, 770, 542)),
    ],
    out=HERE / "birthday_card.png",
)
print("built")
