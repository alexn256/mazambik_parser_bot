"""Draws the "cards" schedule picture — a per-queue alternative to the
provider's own grid, modelled on the СВІТЛО КРЕМЕНЧУК channel's picture
style. A subscriber asks for this one explicitly; it is never sent on its
own, and never replaces the grid.

Colours and geometry were measured off a real sample of that channel's
picture (card 267x~140px, 21px side margins, a 78px orange header), not off
any spec — there isn't one.
"""

import io
import logging
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

ORANGE = (247, 133, 28)
CARD_BG = (242, 242, 242)
WHITE = (255, 255, 255)
TITLE_COLOUR = (17, 24, 61)
SUBTITLE_COLOUR = (110, 112, 118)
TEXT_COLOUR = (25, 28, 36)
EMPTY_COLOUR = (140, 142, 148)
WATERMARK_ALPHA = 28  # out of 255 — faint, legible as decoration not noise

WIDTH = 600
MARGIN = 21
HEADER_H = 78
TITLE_Y = 118
SUBTITLE_Y = 148
CARDS_TOP = 184

COL_GAP = 22
ROW_GAP = 22
CARD_PAD_X = 16
CARD_PAD_TOP = 16
CARD_PAD_BOTTOM = 14
LINE_PITCH = 26
BADGE_W, BADGE_H = 46, 26
BADGE_RADIUS = 13
CARD_RADIUS = 10

HEADER_TEXT = "СВІТЛО КРЕМЕНЧУК"
TITLE_TEXT = "ГРАФІК ПОГОДИННИХ ВІДКЛЮЧЕНЬ"
EMPTY_TEXT = "Без відключень"
WATERMARK_LINE1 = "СВІТЛО КРЕМЕНЧУК"
WATERMARK_LINE2 = "https://t.me/kremen_svitlo"
WATERMARK_ANGLE = 18  # degrees, bottom-left to top-right like the sample

FONT_DIRS = (
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/liberation",
    "/usr/share/fonts/truetype/liberation2",
)


class FontsMissing(RuntimeError):
    """No usable font on this machine — the caller should fall back to text."""


@lru_cache(maxsize=None)
def _font(style: str, size: int) -> ImageFont.FreeTypeFont:
    for directory in FONT_DIRS:
        path = os.path.join(directory, f"LiberationSans-{style}.ttf")
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise FontsMissing(
        "LiberationSans-%s.ttf not found in %s — install fonts-liberation"
        % (style, ", ".join(FONT_DIRS))
    )


def _centre(d, x0, x1, y, text, font, fill):
    d.text(((x0 + x1) / 2, y), text, font=font, fill=fill, anchor="mm")


def _fmt_range(r: dict) -> str:
    return f"{r['start']} - {r['end']}"


def _card_height(n_lines: int) -> int:
    n_lines = max(n_lines, 1)
    content_h = max(BADGE_H, n_lines * LINE_PITCH)
    return CARD_PAD_TOP + content_h + CARD_PAD_BOTTOM


@lru_cache(maxsize=1)
def _watermark_stamp() -> Image.Image:
    """One rotated, semi-transparent copy of the two-line watermark text,
    sized to its own bounding box — pasted once per card, not tiled, which
    is how the sample picture actually uses it."""
    font1 = _font("Bold", 15)
    font2 = _font("Regular", 11)
    pad = 10
    w = max(font1.getlength(WATERMARK_LINE1), font2.getlength(WATERMARK_LINE2)) + pad * 2
    h = 40 + pad * 2
    layer = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text((pad, pad), WATERMARK_LINE1, font=font1, fill=(0, 0, 0, WATERMARK_ALPHA))
    d.text((pad, pad + 22), WATERMARK_LINE2, font=font2, fill=(0, 0, 0, WATERMARK_ALPHA))
    return layer.rotate(WATERMARK_ANGLE, expand=True, resample=Image.BICUBIC)


def _draw_watermark(img: Image.Image, cx: int, cy: int) -> None:
    stamp = _watermark_stamp()
    img.paste(stamp, (int(cx - stamp.width / 2), int(cy - stamp.height / 2)), stamp)


def _draw_card(img: Image.Image, d: ImageDraw.ImageDraw, x: int, y: int, w: int,
               label: str, ranges: list[dict], bold: ImageFont.FreeTypeFont,
               reg: ImageFont.FreeTypeFont, badge_font: ImageFont.FreeTypeFont) -> int:
    n_lines = len(ranges) or 1
    h = _card_height(n_lines)
    d.rounded_rectangle([x, y, x + w, y + h], radius=CARD_RADIUS, fill=CARD_BG)

    _draw_watermark(img, x + w // 2, y + h // 2)

    badge_x, badge_y = x + CARD_PAD_X, y + CARD_PAD_TOP
    d.rounded_rectangle([badge_x, badge_y, badge_x + BADGE_W, badge_y + BADGE_H],
                        radius=BADGE_RADIUS, fill=ORANGE)
    _centre(d, badge_x, badge_x + BADGE_W, badge_y + BADGE_H / 2, label, badge_font, WHITE)

    text_x = badge_x + BADGE_W + 12
    first_y = badge_y + BADGE_H / 2
    if ranges:
        for i, r in enumerate(ranges):
            d.text((text_x, first_y + i * LINE_PITCH), _fmt_range(r), font=reg,
                   fill=TEXT_COLOUR, anchor="lm")
    else:
        d.text((text_x, first_y), EMPTY_TEXT, font=reg, fill=EMPTY_COLOUR, anchor="lm")

    return h


def render(schedule: dict, date: str) -> Image.Image:
    """Draw the cards picture for one day's `schedule` (same shape as the
    grid's own: {"1.1": [{"start", "end"}, ...], ...})."""
    bold22 = _font("Bold", 22)
    bold15 = _font("Bold", 15)
    reg13 = _font("Regular", 13)
    reg12 = _font("Regular", 12)

    col_w = (WIDTH - 2 * MARGIN - COL_GAP) // 2
    col_x = (MARGIN, MARGIN + col_w + COL_GAP)

    rows = []
    for q in range(1, 7):
        labels = (f"{q}.1", f"{q}.2")
        heights = [_card_height(len(schedule.get(label, []))) for label in labels]
        rows.append((labels, max(heights)))

    height = CARDS_TOP + sum(h for _, h in rows) + (len(rows) - 1) * ROW_GAP + MARGIN
    img = Image.new("RGB", (WIDTH, height), WHITE)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, WIDTH, HEADER_H], fill=ORANGE)
    _centre(d, 0, WIDTH, HEADER_H / 2, HEADER_TEXT, bold22, WHITE)
    _centre(d, 0, WIDTH, TITLE_Y, TITLE_TEXT, bold15, TITLE_COLOUR)
    _centre(d, 0, WIDTH, SUBTITLE_Y, f"Інформація на {date}", reg13, SUBTITLE_COLOUR)

    y = CARDS_TOP
    for labels, row_h in rows:
        for label, x in zip(labels, col_x):
            _draw_card(img, d, x, y, col_w, label, schedule.get(label, []),
                      bold15, reg12, bold15)
        y += row_h + ROW_GAP

    return img


def render_png(schedule: dict, date: str) -> bytes | None:
    """Cards picture as PNG bytes, or None if it could not be drawn.

    Never raises: a schedule is worth sending as plain text even when the
    picture fails.
    """
    try:
        buf = io.BytesIO()
        render(schedule, date).save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except FontsMissing:
        logger.error("Cards picture skipped: no Liberation fonts installed")
    except Exception:
        logger.exception("Failed to draw the cards picture")
    return None
