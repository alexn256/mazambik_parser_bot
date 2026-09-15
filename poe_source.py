"""Primary schedule source: poe.pl.ua itself.

The provider's page renders the outage grid through an AJAX call; we call the
same endpoint and parse its response instead of waiting for someone to repost a
screenshot to Telegram. Two endpoints, identical response shape:

    GET  /customs/dynamicgpv-info.php           -> whatever the homepage shows now
    POST /customs/newgpv-info.php               -> one specific date
         seldate={"date_in": "DD-MM-YYYY"}      (dashes; dots give HTTP 400)

The response is a bare HTML fragment: one or more `<div class="gpvinfodetail">`
blocks, one per day, each ending with the provider's own "last updated" stamp —
which is the authoritative answer to "when did this schedule appear or change",
far more precise than the post time of a Telegram message.

A day under outages also carries a `table.turnoff-scheduleui-table`: 12 rows
(черга.підчерга, top to bottom) x 48 half-hour cells. Cell classes come from the
provider's own legend:

    light_1  "Увімкнено", електроенергія наявна
    light_2  "Вимкнено", електроенергія відсутня
    light_3  Час, необхідний для перемикань. Електроенергії може не бути

`light_3` is the switching half-hour that bookends an outage — the provider
states the cut happens within 0.5h of the queue start and power returns in the
last 0.5h, so a published range covers the switching cells too. We therefore
merge `light_2` and `light_3` runs into one outage range, which reproduces the
ranges the provider publishes in text.
"""

import html as html_mod
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://www.poe.pl.ua"
LIVE_ENDPOINT = f"{BASE_URL}/customs/dynamicgpv-info.php"
DATE_ENDPOINT = f"{BASE_URL}/customs/newgpv-info.php"

# The endpoint is an internal one meant for the site's own JS; it rejects
# requests that do not look like they came from the page.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": f"{BASE_URL}/",
    "X-Requested-With": "XMLHttpRequest",
}

REQUEST_TIMEOUT = 20.0

# Optional proxy for provider requests only; empty means connect directly
POE_PROXY = os.getenv("POE_PROXY", "").strip()

UA_MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4,
    "травня": 5, "червня": 6, "липня": 7, "серпня": 8,
    "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}

ROWS = 12          # 6 черг x 2 підчерги
CELLS_PER_ROW = 48  # half-hour slots

CELL_ON = "light_1"
OUTAGE_CLASSES = ("light_2", "light_3")

BLOCK_MARKER = '<div class="gpvinfodetail"'

# "10 квітня 2026 22:18" — the provider's update stamp
STAMP_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(UA_MONTHS) + r")\s+(\d{4})\s+(\d{1,2}):(\d{2})"
)
# "10 квітня 2026" — the date the schedule applies to
DATE_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(UA_MONTHS) + r")\s+(\d{4})")

STAMP_DIV_RE = re.compile(r"text-align:\s*end[^>]*>([^<]+)<", re.IGNORECASE)
TBODY_RE = re.compile(r"<tbody[^>]*>(.*?)</tbody>", re.DOTALL | re.IGNORECASE)
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.DOTALL | re.IGNORECASE)
CELL_RE = re.compile(r'<td[^>]*class="(light_[123])"', re.IGNORECASE)


class PoeParseError(Exception):
    """The response did not look like a schedule we know how to read."""


def _slot_time(index: int) -> str:
    """Half-hour slot boundary as HH:MM. Index 48 is midnight, not 24:00."""
    if index == CELLS_PER_ROW:
        return "00:00"
    return f"{index // 2:02d}:{'30' if index % 2 else '00'}"


def _queue_label(row_index: int) -> str:
    """Row order in the table is 1.1, 1.2, 2.1, 2.2, ... 6.2."""
    return f"{row_index // 2 + 1}.{row_index % 2 + 1}"


def _cells_to_ranges(cells: list[str]) -> list[dict]:
    """Collapse a row of 48 half-hour cells into outage ranges."""
    ranges = []
    i = 0
    while i < len(cells):
        if cells[i] == CELL_ON:
            i += 1
            continue
        start = i
        while i < len(cells) and cells[i] != CELL_ON:
            i += 1
        ranges.append({"start": _slot_time(start), "end": _slot_time(i)})
    return ranges


def _parse_table(block: str) -> dict[str, list[dict]] | None:
    """Parse the outage grid out of a day block. Returns None if it has no grid."""
    tbody = TBODY_RE.search(block)
    if not tbody:
        return None

    rows = ROW_RE.findall(tbody.group(1))
    if len(rows) != ROWS:
        raise PoeParseError(f"expected {ROWS} queue rows, got {len(rows)}")

    schedule = {}
    for index, row in enumerate(rows):
        cells = CELL_RE.findall(row)
        if len(cells) != CELLS_PER_ROW:
            raise PoeParseError(
                f"row {index} ({_queue_label(index)}): "
                f"expected {CELLS_PER_ROW} cells, got {len(cells)}"
            )
        schedule[_queue_label(index)] = _cells_to_ranges(cells)

    return schedule


def _parse_stamp(block: str) -> str | None:
    """Provider's update stamp as 'DD.MM.YYYY HH:MM'."""
    divs = STAMP_DIV_RE.findall(block)
    if not divs:
        return None
    match = STAMP_RE.search(divs[-1])
    if not match:
        return None
    day, month, year, hour, minute = match.groups()
    return f"{int(day):02d}.{UA_MONTHS[month]:02d}.{year} {int(hour):02d}:{minute}"


def _parse_intro(block: str) -> list[list[tuple[str, bool]]]:
    """The "обсяг черг" preamble the provider prints above its table.

    Returned as one list of (text, bold) runs per line, because the provider
    bolds the parts that carry the numbers — the date, each time range, each
    queue count — and the picture we draw reproduces that.
    """
    head = block.split("<table", 1)[0]
    head = re.sub(r"<p\b.*?</p>", "", head, flags=re.DOTALL)  # the caption, drawn separately
    head = re.sub(r"<div\b[^>]*>|</div>", "", head)

    lines = []
    for raw in re.split(r"<br\s*/?>", head):
        runs = []
        for bold_text, plain in re.findall(r"<b\b[^>]*>(.*?)</b>|([^<]+)", raw, re.DOTALL):
            text = html_mod.unescape(re.sub(r"<[^>]+>", "", bold_text or plain))
            text = re.sub(r"\s+", " ", text)
            if text.strip():
                runs.append((text, bool(bold_text)))
        if runs:
            runs[0] = (runs[0][0].lstrip(), runs[0][1])
            runs[-1] = (runs[-1][0].rstrip(), runs[-1][1])
            lines.append(runs)
    return lines


def _parse_day(block: str) -> dict:
    """Parse one `gpvinfodetail` block into the shape the bot pipeline expects."""
    match = DATE_RE.search(block)
    if not match:
        raise PoeParseError("no schedule date found in block")
    day, month, year = match.groups()
    date = f"{int(day):02d}.{UA_MONTHS[month]:02d}.{year}"

    updated_at = _parse_stamp(block)
    schedule = _parse_table(block)

    return {
        "date": date,
        # HH:MM kept for compatibility with the image pipeline's `timestamp`
        "timestamp": updated_at.split(" ")[1] if updated_at else None,
        "updated_at": updated_at,
        "intro": _parse_intro(block) if schedule is not None else [],
        "schedule": schedule if schedule is not None else {
            _queue_label(i): [] for i in range(ROWS)
        },
        "has_grid": schedule is not None,
        "source": "poe.pl.ua",
    }


def parse_response(html: str) -> list[dict]:
    """Parse a raw endpoint response into a list of day dicts, oldest block first.

    An empty response is valid — it means the provider has published nothing for
    that date, which is different from "published: no outages expected".
    """
    if not html or not html.strip():
        return []

    starts = [m.start() for m in re.finditer(re.escape(BLOCK_MARKER), html)]
    if not starts:
        raise PoeParseError("response contains no gpvinfodetail blocks")

    bounds = starts + [len(html)]
    return [_parse_day(html[bounds[i]:bounds[i + 1]]) for i in range(len(starts))]


async def _request(client: httpx.AsyncClient, date: str | None) -> str:
    if date is None:
        response = await client.get(LIVE_ENDPOINT, headers=HEADERS,
                                    timeout=REQUEST_TIMEOUT)
    else:
        response = await client.post(
            DATE_ENDPOINT,
            headers=HEADERS,
            data={"seldate": '{"date_in": "%s"}' % date},
            timeout=REQUEST_TIMEOUT,
        )
    response.raise_for_status()
    return response.text


async def fetch_days(
    date: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Fetch schedules from the provider.

    `date` is `DD-MM-YYYY`; omit it to ask for whatever the homepage shows,
    which is today plus tomorrow once tomorrow's schedule is published. Dates
    before 15.12.2024 live behind a different endpoint and format, and are not
    supported here.
    """
    if client is not None:
        return parse_response(await _request(client, date))

    # The provider drops traffic from some hosting providers outright — the
    # symptom is a connect timeout, not a refusal. POE_PROXY routes just these
    # requests through somewhere it does answer, without moving the whole bot.
    async with httpx.AsyncClient(follow_redirects=True, proxy=POE_PROXY or None) as owned:
        return parse_response(await _request(owned, date))


def format_stamp_ua(stamp: str | None) -> str:
    """Turn "10.04.2026 22:18" back into the provider's own "10 квітня 2026 22:18".

    The picture we draw is meant to pass for a screenshot of the site, and the
    site prints its timestamp in words.
    """
    if not stamp:
        return ""
    match = re.match(r"(\d{2})\.(\d{2})\.(\d{4})(?:\s+(\d{2}:\d{2}))?$", stamp.strip())
    if not match:
        return stamp
    day, month, year, time = match.groups()
    names = {num: name for name, num in UA_MONTHS.items()}
    text = f"{int(day)} {names[int(month)]} {year}"
    return f"{text} {time}" if time else text


def has_outages(schedule: dict) -> bool:
    """True if any queue has at least one outage range."""
    return any(ranges for ranges in schedule.values())
