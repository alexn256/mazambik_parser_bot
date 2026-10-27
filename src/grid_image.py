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
table, and a "Періоди відключень за чергами" list below it. The live parts —
which queues are off right now, the current-time line, the dimmed-past-hours
look, the highlighted current hour — are never computed from this machine's
clock. `poe_source._parse_now_info` reads them straight out of the site's own
response (it already did the work, correctly, in its own timezone) and
`render` only draws what it is given via `now`. A day the current moment
doesn't fall in simply has no such div in the response, so `now` is None and
none of that gets drawn — exactly how the site itself leaves a future or past
day's block alone.
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
BLUE = (13, 71, 161)
TEXT, MUTED = (49, 49, 49), (117, 117, 117)
NOW_BG, NOW_TEXT = (227, 242, 253), (13, 71, 161)   # gpv-now-info / gpv-now-h
ACTIVE_RED = (211, 47, 47)                          # gpv-active

# The site's own border colours: th/td get a faint e0e0e0 by default; the hour
# boundary, the label column and the divider between queue pairs are a
# noticeably darker, deliberate line; the whole table sits in a thin frame.
GEN_BORDER = (224, 224, 224)    # e0e0e0
HOUR_BORDER = (158, 158, 158)   # 9e9e9e
LABEL_BORDER = (117, 117, 117)  # 757575
SCROLL_BORDER = (189, 189, 189)  # bdbdbd, .gpv-scroll's own frame
DASH_ALPHA = 0.35               # rgba(0,0,0,.35) half-hour divider inside a cell
ROW_SEAM_ALPHA = 0.6            # rgba(255,255,255,.6) seam under every cell

PAST_OPACITY = 0.45  # .gpv-past

CELL_W, CELL_H = 28, 24
# Half-hour cells touch directly — the site's own border-right/bottom on them
# is 0 or near-invisible. What dividers exist (hour boundary, half-hour mark,
# queue-pair gap) are drawn afterward, overlaying the cells' own edge pixels.
PITCH_X, PITCH_Y = CELL_W, CELL_H
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
NOTE_TEXT = (
    "Відключення електроенергії відбувається не раніше зазначеного часу початку черги, "
    "впродовж 0,5 год. Увімкнення електроенергії відбувається в останні 0,5 години "
    "вимкненої черги."
)
NOTE_COLOUR = (97, 97, 97)  # 616161

NOW_BAR_W = 4                           # border-left width of the status box
NOW_BOX_TEXT_X = TBL_L + NOW_BAR_W + 8  # bar + its own padding, before the text starts

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


def _darken(colour: tuple, alpha: float = DASH_ALPHA) -> tuple:
    """`colour` blended toward black, matching an rgba(0,0,0,alpha) overlay."""
    return tuple(int(c * (1 - alpha)) for c in colour)


def _lighten(colour: tuple, alpha: float = ROW_SEAM_ALPHA) -> tuple:
    """`colour` blended toward white, matching an rgba(255,255,255,alpha) overlay."""
    return tuple(int(c * (1 - alpha) + 255 * alpha) for c in colour)


def _dashed_vline(d, x, y0, y1, colour, dash=3, gap=2):
    y = y0
    while y < y1:
        d.line([x, y, x, min(y + dash, y1)], fill=colour)
        y += dash + gap


def _slot(time_str: str) -> int:
    return int(time_str[:2]) * 2 + (time_str[3:] == "30")


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


def _table_top(intro, live=False, now_box_lines=3) -> int:
    """Where the table header starts, after intro/caption/status-box/legend."""
    if not intro:
        return TABLE_TOP_BARE
    y = INTRO_TOP + (len(intro) - 1) * INTRO_PITCH + CAPTION_GAP
    if live:
        y += NOW_BOX_GAP + NOW_BOX_PAD_TOP + (now_box_lines - 1) * INTRO_PITCH + NOW_BOX_PAD_BOTTOM
    y += LEGEND_GAP + 14  # one legend row
    return int(y + TABLE_GAP)


LEGEND_SWATCH_W = 16  # the site's own .gpv-legend i{width:16px}


def _legend_items(live: bool) -> list[tuple]:
    """(colour, text, swatch_width, bordered) per legend entry.

    Every swatch is a 16px bordered square except "Поточний час", which the
    site draws as a bare 3px sliver with no border — it's a miniature of the
    line itself, not a colour key.
    """
    items = [
        (ON, "«Увімкнено», електроенергія наявна", LEGEND_SWATCH_W, True),
        (OFF, "«Вимкнено», електроенергія відсутня", LEGEND_SWATCH_W, True),
        (SW, "Час, необхідний для перемикань. Електроенергії може не бути",
         LEGEND_SWATCH_W, True),
    ]
    if live:
        items.append((BLUE, "Поточний час", 3, False))
        items.append((_dim(ON), "Час, що минув", LEGEND_SWATCH_W, True))
    return items


def render(schedule: dict, stamp: str, intro: list | None = None,
           cells: dict | None = None, *, now: dict | None = None,
           periods: dict | None = None) -> Image.Image:
    """Draw the grid.

    `intro` is the provider's preamble as lines of runs. `cells` are the
    provider's painted half-hours per queue, used verbatim when present.

    `now` is `poe_source._parse_now_info`'s result, or None for a day the
    current moment does not fall in (already ended, or not started yet) — the
    picture then looks exactly like the site shows it for that day: no status
    box, no current-time line, nothing dimmed. Everything live-looking is the
    site's own figure, not computed here: which queues are on, off or
    switching, and exactly where the line sits within its half-hour cell.

    `periods` is `poe_source._parse_periods`'s result — the site's own
    "Періоди відключень" ranges and which queues are `gpv-active` (off) right
    now. When it is None (a schedule recognised from a screenshot has no page
    to read this from), both fall back to `schedule`'s own merged ranges,
    with no queue marked active since there is no "now" to judge it against.
    """
    live = now is not None
    now_slot = _slot(now["interval"][0]) if live else None
    line_frac = (now_slot + now["line_pct"] / 100) if live else None
    now_hour = int(now["interval"][0][:2]) if live else None
    on_labels = now["on"] if live else []
    off_labels = now["off"] if live else []
    switching_labels = now["switching"] if live else []
    now_box_lines = 3 + (1 if switching_labels else 0)

    # row labels and the periods list turn red only for a queue the site
    # itself currently marks gpv-active (strictly off, not mid-switch)
    off_now = {label: info["active"] for label, info in (periods or {}).items()}

    top = _table_top(intro, live, now_box_lines)
    grid_y = top + HDR_HOUR_H + HDR_HALF_H
    grid_b = grid_y + 13 * PITCH_Y  # the 12 queues, plus the site's own trailing blank row
    periods_top = grid_b + 20
    periods_rows = -(-12 // 4)  # 4 columns
    note_y = periods_top + 16 + periods_rows * 20 + 16
    stamp_y = note_y + 18
    height = stamp_y + 10
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

        if live:
            box_top = y + NOW_BOX_GAP
            ty = box_top + NOW_BOX_PAD_TOP
            box_bottom = ty + (now_box_lines - 1) * INTRO_PITCH + NOW_BOX_PAD_BOTTOM
            d.rectangle([TBL_L, box_top, GRID_R, box_bottom], fill=NOW_BG)
            d.rectangle([TBL_L, box_top, TBL_L + NOW_BAR_W, box_bottom], fill=BLUE)
            interval_start, interval_end = now["interval"]
            _runs(d, NOW_BOX_TEXT_X, ty,
                  [(f"Зараз {now['time']}", True),
                   (f" (інтервал {interval_start}–{interval_end})", False)])
            ty += INTRO_PITCH
            _runs(d, NOW_BOX_TEXT_X, ty,
                  [("«Увімкнено», електроенергія наявна: ", False), (", ".join(on_labels), True)])
            ty += INTRO_PITCH
            _runs(d, NOW_BOX_TEXT_X, ty,
                  [("«Вимкнено», електроенергія відсутня: ", False), (", ".join(off_labels), True)])
            if switching_labels:
                ty += INTRO_PITCH
                _runs(d, NOW_BOX_TEXT_X, ty,
                      [("Час, необхідний для перемикань. Електроенергії може не бути: ", False),
                       (", ".join(switching_labels), True)])
            y = box_bottom

        legend_y = y + LEGEND_GAP + 7
        lx = INTRO_X
        for colour, text, swatch_w, bordered in _legend_items(live):
            d.rectangle([lx, legend_y - 6, lx + swatch_w, legend_y + 6], fill=colour,
                        outline=HOUR_BORDER if bordered else None)
            lx += swatch_w + 6
            d.text((lx, legend_y), text, font=_font("Regular", 11), fill=TEXT, anchor="lm")
            lx += d.textlength(text, font=_font("Regular", 11)) + 18

    # Table header: one row of "HH–HH" per hour, one row of "00"/"30" per half-hour.
    # Cells touch directly (no gap); a hairline shows only where the site draws
    # one — the generic e0e0e0 divider under row 1 and between the half-hour
    # sub-labels, the darker 9e9e9e line at each hour boundary.
    d.rectangle([TBL_L - 1, top, GRID_R, grid_y - 1], fill=HDR_BG)
    d.line([TBL_L - 1, top + HDR_HOUR_H, GRID_R, top + HDR_HOUR_H], fill=GEN_BORDER)

    for hour in range(24):
        x0 = GRID_X + hour * 2 * PITCH_X
        x1 = x0 + 2 * PITCH_X
        if hour == now_hour:
            d.rectangle([x0, top, x1 - 1, top + HDR_HOUR_H - 1], fill=NOW_BG)
            _centre(d, x0, x1, top + HDR_HOUR_H / 2, f"{hour:02d}–{(hour + 1) % 24:02d}",
                    reg, fill=NOW_TEXT)
        else:
            _centre(d, x0, x1, top + HDR_HOUR_H / 2, f"{hour:02d}–{(hour + 1) % 24:02d}", reg)
        for half, half_label in enumerate(("00", "30")):
            hx0 = x0 + half * PITCH_X
            _centre(d, hx0, hx0 + PITCH_X, top + HDR_HOUR_H + HDR_HALF_H / 2, half_label, reg,
                    fill=MUTED)
            if half:
                d.line([hx0, top + HDR_HOUR_H, hx0, grid_y - 1], fill=GEN_BORDER)

    d.rectangle([TBL_L - 1, top, LABEL_R, grid_y - 1], fill=HDR_BG)
    d.line([LABEL_R, top, LABEL_R, grid_b - 1], fill=LABEL_BORDER)
    _centre(d, TBL_L, LABEL_R, (top + grid_y) / 2 - 1, "Черга", reg)

    # Header/body boundary — the darker "gpv-first" line, not the generic one
    d.line([TBL_L - 1, grid_y - 1, GRID_R, grid_y - 1], fill=LABEL_BORDER)

    for row in range(12):
        queue, sub = row // 2 + 1, row % 2 + 1
        label = f"{queue}.{sub}"
        y0 = grid_y + row * PITCH_Y
        is_off = off_now.get(label, False)
        d.rectangle([TBL_L - 1, y0, LABEL_R - 1, y0 + PITCH_Y - 1], fill=WHITE)
        _centre(d, TBL_L, LABEL_R, y0 + PITCH_Y / 2, label, bold,
                fill=ACTIVE_RED if is_off else TEXT)
        # the label column's own divider is a plain visible grey, every row —
        # unlike the grid cells, .gpv-q never gets the near-invisible override
        d.line([TBL_L - 1, y0 + PITCH_Y - 1, LABEL_R - 1, y0 + PITCH_Y - 1], fill=GEN_BORDER)

        row_cells = _row_cells(schedule.get(label, []), (cells or {}).get(label))
        bottom_y = y0 + CELL_H - 1
        for i, colour in enumerate(row_cells):
            x = GRID_X + i * PITCH_X
            if live and i < now_slot:
                colour = _dim(colour)
            d.rectangle([x, y0, x + CELL_W - 1, y0 + CELL_H - 1], fill=colour)
            if i % 2:  # the half-hour mark inside this hour: a dashed divider
                _dashed_vline(d, x, y0, y0 + CELL_H, _darken(colour))

        if live:
            # The site's own <span class="gpv-line"> sits inside each cell, not
            # as one continuous overlay — so every row's own seam still shows
            # through it, and it never reaches into the trailing blank row.
            line_x = GRID_X + line_frac * PITCH_X
            # .gpv-line is width:2px with a 1px white halo each side (box-shadow)
            d.rectangle([line_x - 2, y0, line_x + 1, y0 + CELL_H - 1], fill=WHITE)  # halo
            d.rectangle([line_x - 1, y0, line_x, y0 + CELL_H - 1], fill=BLUE)

        # every row's own faint seam, even between a queue's own two rows —
        # drawn last so it still cuts across the current-time line too
        for i, colour in enumerate(row_cells):
            x = GRID_X + i * PITCH_X
            if live and i < now_slot:
                colour = _dim(colour)
            d.line([x, bottom_y, x + CELL_W - 1, bottom_y], fill=_lighten(colour))
        if sub == 1 and row:  # queue-pair boundary: "1.2 | 2.1", not "1.1 | 1.2"
            d.line([TBL_L - 1, y0, GRID_R, y0], fill=LABEL_BORDER)

    # The site always leaves one trailing blank row below 6.2, same height,
    # label column and grid both plain white, with no vertical dividers at all
    queues_bottom = grid_y + 12 * PITCH_Y
    d.rectangle([TBL_L - 1, queues_bottom, GRID_R, grid_b - 1], fill=WHITE)

    for hour in range(1, 24):  # hour boundary, spanning header + the 12 queue rows
        x = GRID_X + hour * 2 * PITCH_X
        d.line([x, top, x, queues_bottom - 1], fill=HOUR_BORDER)

    # The site's own .gpv-scroll frame around the whole table
    d.rectangle([TBL_L - 1, top, GRID_R, grid_b - 1], outline=SCROLL_BORDER)

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
        label_ranges = periods[label]["ranges"] if periods and label in periods \
            else schedule.get(label, [])
        d.text((px, py), label, font=_font("Bold", 11), fill=colour, anchor="lm")
        px += d.textlength(label, font=_font("Bold", 11)) + 5
        d.text((px, py), "—", font=body_font, fill=TEXT, anchor="lm")
        px += d.textlength("— ", font=body_font)
        ranges_txt = ", ".join(_fmt_period(r) for r in label_ranges)
        d.text((px, py), ranges_txt or "—", font=body_font, fill=TEXT, anchor="lm")
        underline_y = py + 10
        d.line([TBL_L + col * col_w, underline_y, TBL_L + (col + 1) * col_w - 16, underline_y],
               fill=(238, 238, 238))

    d.text((TBL_L, note_y), NOTE_TEXT, font=_font("Regular", 11), fill=NOTE_COLOUR, anchor="lm")
    d.text((width - 14, stamp_y), stamp, font=_font("Regular", 10), fill=TEXT, anchor="rm")
    return img


def render_png(schedule: dict, stamp: str, intro: list | None = None,
               cells: dict | None = None, *, now: dict | None = None,
               periods: dict | None = None) -> bytes | None:
    """Grid as PNG bytes, or None if it could not be drawn.

    Never raises: a schedule is worth sending as plain text even when the
    picture fails.
    """
    try:
        buf = io.BytesIO()
        render(schedule, stamp, intro, cells, now=now, periods=periods).save(
            buf, format="PNG", optimize=True)
        return buf.getvalue()
    except FontsMissing:
        logger.error("Schedule image skipped: no Liberation fonts installed")
    except Exception:
        logger.exception("Failed to draw the schedule image")
    return None
