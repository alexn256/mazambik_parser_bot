"""Draw the outage grid as a picture, the way poe.pl.ua draws it.

Subscribers are used to seeing the provider's table, so the bot sends one
alongside the text. It is drawn from the parsed schedule rather than captured
from the page: no browser, no screenshot service, and it works for schedules
recognised from a Telegram screenshot too.

Colours, geometry, type sizes and weights were measured off real screenshots of
the site — cell 28x24 separated by a 1px rule, table text at 12px, the intro
block at 13px, the timestamp at 11px — so the result reads as a screenshot of
that table rather than as a chart of our own.
"""

import io
import logging
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

ON, OFF, SW = (102, 187, 106), (239, 83, 80), (255, 235, 59)
WHITE, HDR_BG = (255, 255, 255), (235, 237, 240)
RULE, INNER, BLUE = (217, 217, 217), (213, 213, 213), (127, 182, 213)
TEXT, MUTED = (49, 49, 49), (150, 150, 150)

CELL_W, CELL_H = 28, 24
PITCH_X, PITCH_Y = CELL_W + 1, CELL_H + 1
TBL_L, LABEL_R, SUB_R, GRID_X = 7, 88, 109, 110
GRID_R = GRID_X + 48 * PITCH_X - 1

INTRO_X, INTRO_TOP, INTRO_PITCH = 13, 22, 18   # left margin, first line centre, line pitch
CAPTION_GAP, TABLE_GAP = 23.5, 17.5            # last intro line -> caption -> table top
TABLE_TOP_BARE = 5                             # when the provider published no intro

CAPTION_TEXT = "Порядок відключення черг"

# Liberation Sans is metric-compatible with the Arial the site is set in.
# Debian ships it as fonts-liberation; the Dockerfile installs it.
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


def _centre(d, x0, x1, y, text, font, fill=TEXT):
    d.text(((x0 + x1) / 2, y), text, font=font, fill=fill, anchor="mm")


def _runs(d, x, y, runs):
    """Draw one intro line, switching weight per run."""
    for text, is_bold in runs:
        font = _font("Bold" if is_bold else "Regular", 13)
        d.text((x, y), text, font=font, fill=TEXT, anchor="lm")
        x += d.textlength(text, font=font)


def _table_top(intro) -> int:
    if not intro:
        return TABLE_TOP_BARE
    return int(INTRO_TOP + (len(intro) - 1) * INTRO_PITCH + CAPTION_GAP + TABLE_GAP)


def _slot(time_str: str) -> int:
    return int(time_str[:2]) * 2 + (time_str[3:] == "30")


def _row_cells(ranges: list[dict]) -> list[tuple]:
    """One queue row as 48 half-hour colours.

    The last half-hour of a range is the provider's switching window, which it
    paints yellow — the same convention poe_source reads back.
    """
    cells = [ON] * 48
    for r in ranges:
        start = _slot(r["start"])
        end = _slot(r["end"]) or 48
        for i in range(start, end):
            cells[i] = OFF
        cells[end - 1] = SW
    return cells


def render(schedule: dict, stamp: str, intro: list | None = None) -> Image.Image:
    """Draw the grid. `intro` is the provider's preamble as lines of runs."""
    top = _table_top(intro)
    grid_y = top + 114
    grid_b = grid_y + 12 * PITCH_Y
    width, height = GRID_R + 14, grid_b + 88

    reg, bold = _font("Regular", 12), _font("Bold", 12)
    img = Image.new("RGB", (width, height), WHITE)
    d = ImageDraw.Draw(img)

    if intro:
        for i, line in enumerate(intro):
            _runs(d, INTRO_X, INTRO_TOP + i * INTRO_PITCH, line)
        _centre(d, TBL_L, GRID_R, top - TABLE_GAP, CAPTION_TEXT, _font("Bold", 13))

    d.rectangle([TBL_L - 1, top, GRID_R, grid_b], fill=HDR_BG, outline=RULE)
    for y in (top + 26, top + 69, top + 113):
        d.line([GRID_X - 1, y, GRID_R, y], fill=RULE)

    # One merged cell spanning the whole header; no band rule may cut through it
    d.rectangle([TBL_L - 1, top, SUB_R, grid_y - 1], fill=HDR_BG, outline=INNER)
    _centre(d, TBL_L, SUB_R, top + 49, "№ черги /", bold)
    _centre(d, TBL_L, SUB_R, top + 68, "підчерги", bold)

    _centre(d, GRID_X, GRID_R, top + 13, "Години доби", bold)
    for hour in range(24):
        x0 = GRID_X + hour * 2 * PITCH_X
        x1 = x0 + 2 * PITCH_X
        _centre(d, x0, x1, top + 40, "з", bold)
        _centre(d, x0, x1, top + 57, f"{hour:02d}:00", bold)
        _centre(d, x0, x1, top + 83, "по", bold)
        _centre(d, x0, x1, top + 100, f"{(hour + 1) % 24:02d}:00", bold)
        if hour:
            d.line([x0 - 1, top + 27, x0 - 1, top + 112], fill=INNER)

    # Rules show through the 1px margin left around every cell below
    d.rectangle([GRID_X - 1, grid_y - 1, GRID_R, grid_b - 1], fill=RULE)

    for queue in range(1, 7):
        y0 = grid_y + (queue - 1) * 2 * PITCH_Y
        y1 = y0 + 2 * PITCH_Y
        d.rectangle([TBL_L - 1, y0 - 1, LABEL_R, y1 - 1], fill=WHITE, outline=INNER)
        _centre(d, TBL_L, LABEL_R, (y0 + y1) / 2 - 1, f"{queue} черга", bold)

    for row in range(12):
        queue, sub = row // 2 + 1, row % 2 + 1
        y0 = grid_y + row * PITCH_Y
        y1 = y0 + PITCH_Y
        d.rectangle([LABEL_R, y0 - 1, SUB_R, y1 - 1], fill=WHITE, outline=INNER)
        _centre(d, LABEL_R, SUB_R, (y0 + y1) / 2 - 1, str(sub), bold)

        for i, colour in enumerate(_row_cells(schedule.get(f"{queue}.{sub}", []))):
            x = GRID_X + i * PITCH_X
            d.rectangle([x, y0, x + CELL_W - 1, y0 + CELL_H - 1], fill=colour)

    arrows_y = grid_b + 45
    d.polygon([(14, arrows_y), (21, arrows_y - 5), (21, arrows_y + 5)], fill=MUTED)
    d.polygon([(GRID_R, arrows_y), (GRID_R - 7, arrows_y - 5), (GRID_R - 7, arrows_y + 5)],
              fill=MUTED)
    d.text((width - 14, height - 25), stamp, font=_font("Regular", 11), fill=TEXT, anchor="rm")
    d.line([0, height - 7, width, height - 7], fill=BLUE)
    return img


def render_png(schedule: dict, stamp: str, intro: list | None = None) -> bytes | None:
    """Grid as PNG bytes, or None if it could not be drawn.

    Never raises: a schedule is worth sending as plain text even when the
    picture fails.
    """
    try:
        buf = io.BytesIO()
        render(schedule, stamp, intro).save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except FontsMissing:
        logger.error("Schedule image skipped: no Liberation fonts installed")
    except Exception:
        logger.exception("Failed to draw the schedule image")
    return None
