"""Draw the outage grid as a picture, the way poe.pl.ua draws it.

Subscribers are used to seeing the provider's table, so the bot sends one
alongside the text. It is drawn from the parsed schedule rather than captured
from the page: no browser, no screenshot service, and it works for schedules
recognised from a Telegram screenshot too.

Colours, geometry, type sizes and weights were measured off real screenshots of
the site — cell 28x24 separated by a 1px rule, table text at 12px, the intro
block at 13px, the timestamp at 11px — so the result reads as a screenshot of
that table rather than as a chart of our own.

The provider redesigned the page on 2026-10-27: a two-row hour header instead
of the old four-row one, a colour legend, a "Зараз ..." status box above the
table, and a "Періоди відключень за чергами" list below it. Four of those five
additions are live — which queues are off right now, the current-time line,
the dimmed-past-hours look, and the highlighted current hour — so `render`
takes an optional `now_minutes` and the caller must re-render (never reuse a
cached picture) for a schedule that covers today, exactly like the site
recomputes them on every page load.
"""

import io
import logging
import os
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# The provider's own legend colours (gpv-s1/2/3 in its stylesheet)
ON, OFF, SW = (123, 198, 126), (217, 83, 79), (245, 194, 66)
WHITE, HDR_BG = (255, 255, 255), (245, 245, 245)
RULE, INNER, BLUE = (224, 224, 224), (189, 189, 189), (13, 71, 161)
TEXT, MUTED = (49, 49, 49), (117, 117, 117)
NOW_BG, NOW_TEXT = (227, 242, 253), (13, 71, 161)   # gpv-now-info / gpv-now-h
ACTIVE_RED = (211, 47, 47)                          # gpv-active

PAST_OPACITY = 0.45  # .gpv-past

CELL_W, CELL_H = 28, 24
PITCH_X, PITCH_Y = CELL_W + 1, CELL_H + 1
TBL_L = 7
LABEL_R = TBL_L + 55                                # single "1.1"-style column
GRID_X = LABEL_R + 1
GRID_R = GRID_X + 48 * PITCH_X - 1

HDR_HOUR_H, HDR_HALF_H = 26, 20                     # "00–01" row, "00"/"30" row

INTRO_X, INTRO_TOP, INTRO_PITCH = 13, 22, 18   # left margin, first line centre, line pitch
CAPTION_GAP = 23.5                             # last intro line -> caption
NOW_BOX_GAP = 14                               # caption's baseline -> status box top
NOW_BOX_PAD_TOP, NOW_BOX_PAD_BOTTOM = 14, 10   # box top -> first line; last line -> box bottom
LEGEND_GAP = 8                                 # status box (or caption) -> legend
TABLE_GAP = 12                                 # legend -> table top
TABLE_TOP_BARE = 5                             # when the provider published no intro

CAPTION_TEXT = "Порядок відключення черг"
PERIODS_CAPTION = "Періоди відключень за чергами"

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


def _runs(d, x, y, runs, size=13):
    """Draw one line of (text, bold) runs left to right, returning the end x."""
    for text, is_bold in runs:
        font = _font("Bold" if is_bold else "Regular", size)
        d.text((x, y), text, font=font, fill=TEXT, anchor="lm")
        x += d.textlength(text, font=font)
    return x


def _dim(colour: tuple) -> tuple:
    """`colour` blended toward white at PAST_OPACITY, matching .gpv-past."""
    return tuple(int(c * PAST_OPACITY + 255 * (1 - PAST_OPACITY)) for c in colour)


def _slot(time_str: str) -> int:
    return int(time_str[:2]) * 2 + (time_str[3:] == "30")


def _minutes(time_str: str) -> int:
    h, m = time_str.split(":")
    return int(h) * 60 + int(m)


def _range_end_minutes(end: str) -> int:
    return 1440 if end == "00:00" else _minutes(end)


def _is_off(ranges: list[dict], now_minutes: int) -> bool:
    return any(_minutes(r["start"]) <= now_minutes < _range_end_minutes(r["end"])
               for r in ranges)


def _fmt_period(r: dict) -> str:
    # The site writes a midnight-ending range as "...–24:00", not "–00:00"
    end = "24:00" if r["end"] == "00:00" else r["end"]
    return f"{r['start']}–{end}"


# The provider's own cell classes: gpv-s1 on, gpv-s2 off, gpv-s3 switching
PROVIDER_COLOURS = {"1": ON, "2": OFF, "3": SW}


def _row_cells(ranges: list[dict], painted: str | None = None) -> list[tuple]:
    """One queue row as 48 half-hour colours.

    When the provider's own cells are available they are used verbatim — no
    reconstruction can beat the source. Schedules recognised from a screenshot
    carry only ranges, so those are rebuilt: the last half-hour of a range is
    the switching window the provider paints yellow.

    A range running to midnight is the exception. Its block continues into the
    next day, so the switching half-hour falls on the other side of midnight
    and today's last cell stays off — which is exactly what the provider draws.
    """
    if painted and len(painted) == 48:
        return [PROVIDER_COLOURS[c] for c in painted]

    cells = [ON] * 48
    for r in ranges:
        start = _slot(r["start"])
        end = _slot(r["end"]) or 48
        for i in range(start, end):
            cells[i] = OFF
        if end < 48:
            cells[end - 1] = SW
    return cells


def _table_top(intro, now_minutes=None) -> int:
    """Where the table header starts, after intro/caption/status-box/legend."""
    if not intro:
        return TABLE_TOP_BARE
    y = INTRO_TOP + (len(intro) - 1) * INTRO_PITCH + CAPTION_GAP
    if now_minutes is not None:
        y += NOW_BOX_GAP + NOW_BOX_PAD_TOP + 2 * INTRO_PITCH + NOW_BOX_PAD_BOTTOM
    y += LEGEND_GAP + 14  # one legend row
    return int(y + TABLE_GAP)


def _legend_items(now_minutes) -> list[tuple]:
    items = [
        (ON, "«Увімкнено», електроенергія наявна"),
        (OFF, "«Вимкнено», електроенергія відсутня"),
        (SW, "Час, необхідний для перемикань. Електроенергії може не бути"),
    ]
    if now_minutes is not None:
        items.append((BLUE, "Поточний час"))
        items.append((_dim(ON), "Час, що минув"))
    return items


def render(schedule: dict, stamp: str, intro: list | None = None,
           cells: dict | None = None, *, now_minutes: int | None = None) -> Image.Image:
    """Draw the grid.

    `intro` is the provider's preamble as lines of runs. `cells` are the
    provider's painted half-hours per queue, used verbatim when present.

    `now_minutes` (0-1439) draws the live parts the site only shows for today:
    the "Зараз ..." status box, a highlighted current hour, a blue line at the
    exact minute, and already-elapsed cells dimmed. Pass None for a day the
    current moment does not fall in — a schedule that already ended, or one
    that has not started yet — the picture then looks exactly like the site
    shows it for that day: no status box, no line, nothing dimmed.
    """
    off_now = ({label: _is_off(ranges, now_minutes) for label, ranges in schedule.items()}
               if now_minutes is not None else {})

    top = _table_top(intro, now_minutes)
    grid_y = top + HDR_HOUR_H + HDR_HALF_H
    grid_b = grid_y + 12 * PITCH_Y
    periods_top = grid_b + 20
    periods_rows = -(-12 // 4)  # 4 columns
    height = periods_top + 16 + periods_rows * 20 + 24
    width = GRID_R + 14

    reg, bold = _font("Regular", 12), _font("Bold", 12)
    img = Image.new("RGB", (width, height), WHITE)
    d = ImageDraw.Draw(img)

    y = INTRO_TOP
    if intro:
        for i, line in enumerate(intro):
            _runs(d, INTRO_X, y + i * INTRO_PITCH, line)
        y += (len(intro) - 1) * INTRO_PITCH
        caption_y = y + CAPTION_GAP
        _centre(d, TBL_L, GRID_R, caption_y, CAPTION_TEXT, _font("Bold", 13))
        y = caption_y

        if now_minutes is not None:
            box_top = y + NOW_BOX_GAP
            ty = box_top + NOW_BOX_PAD_TOP
            box_bottom = ty + 2 * INTRO_PITCH + NOW_BOX_PAD_BOTTOM
            d.rectangle([TBL_L, box_top, GRID_R, box_bottom], fill=NOW_BG)
            d.rectangle([TBL_L, box_top, TBL_L + 3, box_bottom], fill=BLUE)
            on_labels = [q for q in schedule if not off_now.get(q)]
            off_labels = [q for q in schedule if off_now.get(q)]
            slot_start = f"{now_minutes // 60:02d}:{'30' if now_minutes % 60 >= 30 else '00'}"
            slot_end_m = (now_minutes // 30 + 1) * 30
            slot_end = f"{(slot_end_m // 60) % 24:02d}:{slot_end_m % 60:02d}"
            now_txt = f"{now_minutes // 60:02d}:{now_minutes % 60:02d}"
            _runs(d, INTRO_X, ty,
                  [(f"Зараз {now_txt}", True), (f" (інтервал {slot_start}–{slot_end})", False)])
            ty += INTRO_PITCH
            _runs(d, INTRO_X, ty,
                  [("«Увімкнено», електроенергія наявна: ", False), (", ".join(on_labels), True)])
            ty += INTRO_PITCH
            _runs(d, INTRO_X, ty,
                  [("«Вимкнено», електроенергія відсутня: ", False), (", ".join(off_labels), True)])
            y = box_bottom

        legend_y = y + LEGEND_GAP + 7
        lx = INTRO_X
        for colour, text in _legend_items(now_minutes):
            d.rectangle([lx, legend_y - 6, lx + 14, legend_y + 4], fill=colour, outline=INNER)
            lx += 20
            d.text((lx, legend_y), text, font=_font("Regular", 11), fill=TEXT, anchor="lm")
            lx += d.textlength(text, font=_font("Regular", 11)) + 18

    # Table header: one row of "HH–HH" per hour, one row of "00"/"30" per half-hour
    d.rectangle([TBL_L - 1, top, GRID_R, grid_y - 1], fill=HDR_BG, outline=RULE)
    d.rectangle([TBL_L - 1, top, LABEL_R, grid_y - 1], fill=HDR_BG, outline=INNER)
    _centre(d, TBL_L, LABEL_R, (top + grid_y) / 2 - 1, "Черга", bold)

    now_hour = now_minutes // 60 if now_minutes is not None else None
    for hour in range(24):
        x0 = GRID_X + hour * 2 * PITCH_X
        x1 = x0 + 2 * PITCH_X
        if hour == now_hour:
            d.rectangle([x0, top, x1 - 1, top + HDR_HOUR_H - 1], fill=NOW_BG)
            _centre(d, x0, x1, top + HDR_HOUR_H / 2, f"{hour:02d}–{(hour + 1) % 24:02d}",
                    bold, fill=NOW_TEXT)
        else:
            _centre(d, x0, x1, top + HDR_HOUR_H / 2, f"{hour:02d}–{(hour + 1) % 24:02d}", bold)
        if hour:
            d.line([x0 - 1, top, x0 - 1, grid_y - 1], fill=INNER)
        for half, label in enumerate(("00", "30")):
            hx0 = x0 + half * PITCH_X
            _centre(d, hx0, hx0 + PITCH_X, top + HDR_HOUR_H + HDR_HALF_H / 2, label, reg,
                    fill=MUTED)

    d.line([TBL_L - 1, grid_y - 1, GRID_R, grid_y - 1], fill=RULE)

    # Rules show through the 1px margin left around every cell below
    d.rectangle([GRID_X - 1, grid_y - 1, GRID_R, grid_b - 1], fill=RULE)

    for row in range(12):
        queue, sub = row // 2 + 1, row % 2 + 1
        label = f"{queue}.{sub}"
        y0 = grid_y + row * PITCH_Y
        y1 = y0 + PITCH_Y
        is_off = off_now.get(label, False)
        d.rectangle([TBL_L - 1, y0 - 1, LABEL_R, y1 - 1], fill=WHITE, outline=INNER)
        _centre(d, TBL_L, LABEL_R, (y0 + y1) / 2 - 1, label, bold,
                fill=ACTIVE_RED if is_off else TEXT)

        row_cells = _row_cells(schedule.get(label, []), (cells or {}).get(label))
        for i, colour in enumerate(row_cells):
            x = GRID_X + i * PITCH_X
            if now_minutes is not None and i < now_minutes // 30:
                colour = _dim(colour)
            d.rectangle([x, y0, x + CELL_W - 1, y0 + CELL_H - 1], fill=colour)

    if now_minutes is not None:
        slot = now_minutes // 30
        frac = (now_minutes % 30) / 30
        line_x = GRID_X + (slot + frac) * PITCH_X
        d.line([line_x, grid_y - 1, line_x, grid_b - 1], fill=BLUE, width=2)

    # Periods list: each queue's outage ranges, four per row
    _centre(d, TBL_L, GRID_R, periods_top, PERIODS_CAPTION, _font("Bold", 12))
    col_w = (GRID_R - TBL_L) / 4
    body_font = _font("Regular", 11)
    for row in range(12):
        queue, sub = row // 2 + 1, row % 2 + 1
        label = f"{queue}.{sub}"
        col, line_row = row % 4, row // 4
        px = TBL_L + col * col_w
        py = periods_top + 16 + line_row * 20
        colour = ACTIVE_RED if off_now.get(label) else TEXT
        d.text((px, py), label, font=_font("Bold", 11), fill=colour, anchor="lm")
        px += d.textlength(label, font=_font("Bold", 11)) + 5
        d.text((px, py), "—", font=body_font, fill=TEXT, anchor="lm")
        px += d.textlength("— ", font=body_font)
        ranges_txt = ", ".join(_fmt_period(r) for r in schedule.get(label, []))
        d.text((px, py), ranges_txt or "—", font=body_font, fill=TEXT, anchor="lm")

    d.text((width - 14, height - 14), stamp, font=_font("Regular", 11), fill=TEXT, anchor="rm")
    return img


def render_png(schedule: dict, stamp: str, intro: list | None = None,
               cells: dict | None = None, *, now_minutes: int | None = None) -> bytes | None:
    """Grid as PNG bytes, or None if it could not be drawn.

    Never raises: a schedule is worth sending as plain text even when the
    picture fails.
    """
    try:
        buf = io.BytesIO()
        render(schedule, stamp, intro, cells, now_minutes=now_minutes).save(
            buf, format="PNG", optimize=True)
        return buf.getvalue()
    except FontsMissing:
        logger.error("Schedule image skipped: no Liberation fonts installed")
    except Exception:
        logger.exception("Failed to draw the schedule image")
    return None
