import asyncio
import hashlib
import json
import logging
import os
import re
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import httpx

from config import (
    BOT_TOKEN,
    CHANNEL_USERNAME,
    HISTORY_FILE_PATH,
    POE_POLL_INTERVAL,
    POE_SOURCE_ENABLED,
    QUEUE_LABELS,
    QUEUES_FILE_PATH,
    STATE_FILE_PATH,
    TELEGRAM_SOURCE_ENABLED,
    SUBSCRIBERS_FILE_PATH,
    TELETHON_API_HASH,
    TELETHON_API_ID,
    TELETHON_SESSION_STRING,
    USER_CHAT_ID,
)
from diff import compute_diff
from formatter import (
    QUEUE_EMOJI,
    SWITCH_WINDOW_MINUTES,
    format_schedule,
    format_stamp,
)
from queue_lookup import MAJOR_CITIES, QueueLookup
from monitor import create_client, monitor_channel
from parser import parse_schedule_image
from poe_source import PoeParseError, fetch_days, format_stamp_ua, has_outages
from grid_image import render_png
from sender import BROADCAST_DELAY, broadcast, send_message, send_photo
from history import load_history, record_day, save_history
from state import build_state, is_new_day, load_state, save_state
from stats import compute_stats
from subscribers import (
    add_subscriber,
    load_subscribers,
    remove_subscriber,
    set_subscriber_queue,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# httpx logs every request at INFO, and a Bot API URL carries the token in its
# path — that put the bot's credentials into the deployment logs on every poll.
logging.getLogger("httpx").setLevel(logging.WARNING)

UKRAINE_TZ = timezone(timedelta(hours=3))

try:
    QUEUES = QueueLookup(QUEUES_FILE_PATH)
    logger.info("Loaded queue lookup: %d places", len(QUEUES._data))
except Exception:
    logger.exception("Failed to load queue lookup; 'find my queue' disabled")
    QUEUES = None

# Per-chat state for the "which queue is mine?" wizard.
# In-memory only: on redeploy the user just restarts the lookup, no data lost.
fq_state: dict[int, dict] = {}



def _time_to_minutes(t: str) -> int:
    h, m = map(int, t.split(":"))
    return h * 60 + m


def _end_to_minutes(t: str) -> int:
    """Range END as minutes: '00:00' means midnight (1440), not day start."""
    minutes = _time_to_minutes(t)
    return 24 * 60 if minutes == 0 else minutes


def _format_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if h > 0 and m > 0:
        return f"{h} год {m} хв"
    elif h > 0:
        return f"{h} год"
    return f"{m} хв"


def _minutes_to_time(minutes: int) -> str:
    """Minutes since midnight as HH:MM, folding 1440 back to 00:00."""
    minutes %= 24 * 60
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _restoration_hint(outage: dict, now_minutes: int) -> str | None:
    """Warn that power may return before the printed end of the outage.

    Waiting for the light is where the soft half hour actually hurts: the range
    says 15:00, the provider may switch at 14:35. Ranges no longer than the
    switching window are all edge and have nothing useful to say.
    """
    start = _time_to_minutes(outage["start"])
    end = _end_to_minutes(outage["end"])
    window_start = end - SWITCH_WINDOW_MINUTES
    if window_start <= start:
        return None
    if now_minutes < window_start:
        return f"💡 Світло може з'явитись раніше — з {_minutes_to_time(window_start)}"
    return "💡 Світло має з'явитись найближчим часом"


def _find_next_range(ranges: list, after_minutes: int) -> dict | None:
    for r in sorted(ranges, key=lambda x: _time_to_minutes(x["start"])):
        if _time_to_minutes(r["start"]) > after_minutes:
            return r
    return None


async def send_current_status(chat_id: int) -> None:
    """Send real-time power status: is there power now, and when does it change?"""
    queue = load_subscribers(SUBSCRIBERS_FILE_PATH).get(chat_id)
    if not queue:
        await send_message(BOT_TOKEN, chat_id,
            "ℹ️ Оберіть свою чергу щоб дізнатись поточний статус.\n"
            "Натисніть «⚙️ Моя черга» у меню /start.")
        return

    state = load_state(STATE_FILE_PATH)
    today = datetime.now(UKRAINE_TZ).strftime("%d.%m.%Y")
    entry = state.get(today)
    if not entry:
        await send_message(BOT_TOKEN, chat_id, "ℹ️ Графік на сьогодні ще не отримано.")
        return

    ranges = entry["schedule"].get(queue, [])
    now = datetime.now(UKRAINE_TZ)
    now_m = now.hour * 60 + now.minute

    # Check if currently in outage (end "00:00" = midnight, not day start)
    current_outage = next(
        (r for r in ranges
         if _time_to_minutes(r["start"]) <= now_m < _end_to_minutes(r["end"])),
        None,
    )

    lines = []
    if current_outage:
        remaining = _end_to_minutes(current_outage["end"]) - now_m
        next_outage = _find_next_range(ranges, _end_to_minutes(current_outage["end"]))
        lines.append(f"🔴 Зараз відключення · черга {queue}")
        lines.append(f"до {current_outage['end']} (ще {_format_duration(remaining)})")
        hint = _restoration_hint(current_outage, now_m)
        if hint:
            lines.append(hint)
        if next_outage:
            lines.append(f"Далі: світло з {current_outage['end']} до {next_outage['start']}")
        else:
            lines.append(f"Далі: світло з {current_outage['end']} до кінця дня")
        image_name = "power_off.png"
    else:
        next_outage = _find_next_range(ranges, now_m)
        if next_outage:
            remaining = _time_to_minutes(next_outage["start"]) - now_m
            lines.append(f"💡 Зараз є світло · черга {queue}")
            lines.append(f"до {next_outage['start']} (ще {_format_duration(remaining)})")
            lines.append(f"Далі: відключення {next_outage['start']} – {next_outage['end']}")
        else:
            lines.append(f"💡 Зараз є світло · черга {queue}")
            # "більше не" only makes sense if there were outages earlier today
            lines.append("Відключень на сьогодні не заплановано" if not ranges
                         else "Відключень більше не заплановано на сьогодні")
        image_name = "power_on.png"

    text = "\n".join(lines)
    image_path = os.path.join(os.path.dirname(__file__), "assets", image_name)
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(image_path, "rb") as f:
                resp = await client.post(
                    f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                    data={"chat_id": chat_id, "caption": text},
                    files={"photo": (image_name, f, "image/png")},
                )
        if resp.status_code != 200:
            raise RuntimeError(f"sendPhoto returned {resp.status_code}")
    except Exception:
        logger.exception("Failed to send status photo, falling back to text")
        await send_message(BOT_TOKEN, chat_id, text)


# Rendered pictures, keyed by what is drawn in them, least-recently-used last.
# Bounded: a superseded schedule stops being asked for and falls out, so a long
# run of amendments cannot pile drawings up in memory.
_pictures: OrderedDict[str, dict] = OrderedDict()
PICTURE_CACHE_SIZE = 4

# Telegram's limit on the text it will show under a photo
CAPTION_LIMIT = 1024

# Consecutive unreachable polls before the log says so once, loudly
UNREACHABLE_ALERT_AFTER = 3

# Held for the whole read-state -> diff -> broadcast -> write-state sequence, so
# the site poller and the Telegram fallback can never run it at the same time.
_broadcast_lock = asyncio.Lock()


def _picture_key(schedule: dict, stamp: str, intro) -> str:
    payload = json.dumps([schedule, stamp, intro], sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _grid_picture(parsed: dict) -> dict | None:
    """The provider's table as a picture, or None when there is nothing to draw.

    Keyed by the drawing's own contents rather than by date: an amended
    schedule cannot collide with the one it replaced, and the same day asked
    for twice costs nothing the second time.
    """
    if not has_outages(parsed.get("schedule") or {}):
        return None

    stamp = format_stamp_ua(parsed.get("updated_at") or parsed.get("date"))
    intro = parsed.get("intro") or []
    key = _picture_key(parsed["schedule"], stamp, intro)

    cached = _pictures.get(key)
    if cached:
        _pictures.move_to_end(key)   # today's grid stays put while amendments churn
        return cached

    png = render_png(parsed["schedule"], stamp, intro)
    if png is None:
        return None

    entry = {"png": png, "file_id": None}
    _pictures[key] = entry
    while len(_pictures) > PICTURE_CACHE_SIZE:
        _pictures.popitem(last=False)
    return entry


async def _send_schedule(chat_id: int, picture: dict | None, text: str) -> None:
    """Deliver one schedule: the picture with the text under it where possible.

    A caption keeps it to a single Bot API call per subscriber, which halves
    what a broadcast costs. Telegram caps captions, so a long schedule still
    goes as a photo followed by its text — and if the photo fails either way,
    the text is sent on its own rather than lost.

    The picture is uploaded only the first time: Telegram files it under a
    file_id, and naming that id afterwards skips the upload entirely.
    """
    if not picture:
        await send_message(BOT_TOKEN, chat_id, text)
        return

    caption = text if len(text) <= CAPTION_LIMIT else None
    sent = await send_photo(BOT_TOKEN, chat_id,
                            picture["file_id"] or picture["png"], caption=caption)
    if sent and not picture["file_id"]:
        picture["file_id"] = sent

    if caption is None or not sent:
        await send_message(BOT_TOKEN, chat_id, text)


def _provider_stamp_line(parsed: dict) -> str | None:
    """Attribution line for answers that carry no grid of their own.

    Says nothing at all rather than citing a source we cannot date — a
    schedule recognised from a screenshot has no provider stamp.
    """
    stamp = format_stamp(parsed)
    if stamp == "?":
        return None
    return f"За даними Полтаваобленерго станом на {stamp}."


def _cancellation_message(parsed: dict) -> str:
    """Wording for a schedule that was published and then called off.

    Deliberately short: the previous schedule is void, and a full grid of
    twelve "немає відключень" lines would bury that single fact.
    """
    lines = [f"✅ Графік на {parsed['date']} скасовано — відключень не прогнозується."]
    source_line = _provider_stamp_line(parsed)
    if source_line:
        lines.append(source_line)
    return "\n".join(lines)


async def _send_day_schedule(chat_id: int, date: str, entry: dict, when: str) -> None:
    """Send one day's schedule, or the provider's "nothing planned" notice.

    Since the provider is polled directly, an empty day is a published fact, not
    a gap in our knowledge — and it deserves a plain answer rather than a grid
    of twelve "немає відключень" lines.
    """
    queue = load_subscribers(SUBSCRIBERS_FILE_PATH).get(chat_id)
    parsed = {
        "date": date,
        "timestamp": entry.get("last_timestamp"),
        "updated_at": entry.get("updated_at"),
        "intro": entry.get("intro"),
        "schedule": entry["schedule"],
    }

    if not has_outages(parsed["schedule"]):
        lines = [f"🟢 На {when} ({date}) відключень не прогнозується."]
        source_line = _provider_stamp_line(parsed)
        if source_line:
            lines.append(source_line)
        await send_message(BOT_TOKEN, chat_id, "\n".join(lines))
        return

    await _send_schedule(chat_id, _grid_picture(parsed),
                         format_schedule(parsed, diff=None, is_first=True, queue_filter=queue))


async def send_current_schedule(chat_id: int) -> None:
    """Send today's schedule to a single user."""
    state = load_state(STATE_FILE_PATH)
    today = datetime.now(UKRAINE_TZ).strftime("%d.%m.%Y")
    entry = state.get(today)
    if not entry:
        await send_message(BOT_TOKEN, chat_id,
            "ℹ️ Графік на сьогодні ще не опубліковано. "
            "Щойно він з'явиться — надішлемо автоматично.")
        return
    await _send_day_schedule(chat_id, today, entry, "сьогодні")


async def send_tomorrow_schedule(chat_id: int) -> None:
    """Send tomorrow's schedule if already published, or a fallback message."""
    state = load_state(STATE_FILE_PATH)
    tomorrow = (datetime.now(UKRAINE_TZ) + timedelta(days=1)).strftime("%d.%m.%Y")
    entry = state.get(tomorrow)
    if not entry:
        await send_message(BOT_TOKEN, chat_id,
            "ℹ️ Графік на завтра ще не опубліковано. "
            "Зазвичай він з'являється ввечері.")
        return
    await _send_day_schedule(chat_id, tomorrow, entry, "завтра")


async def process_image(image_path: str, date: str | None = None, timestamp: str | None = None) -> bool:
    """Fallback source: OCR a schedule screenshot, then run the common pipeline."""
    logger.info("Processing image: %s", image_path)

    try:
        parsed = parse_schedule_image(image_path)
    except Exception:
        logger.exception("Failed to parse schedule image")
        subs = load_subscribers(SUBSCRIBERS_FILE_PATH)
        await broadcast(BOT_TOKEN, list(subs.keys()), "❌ Не вдалось розпізнати графік")
        return False

    # Use date/timestamp from message if watermark OCR failed
    if date and not parsed["date"]:
        parsed["date"] = date
    if timestamp and not parsed["timestamp"]:
        parsed["timestamp"] = timestamp
    parsed.setdefault("source", "telegram")

    return await process_parsed(parsed)


async def process_parsed(parsed: dict) -> bool:
    """Common pipeline: diff against stored state -> format -> send -> save state.

    Both sources feed this. Whichever arrives first wins; the other one then
    produces an empty diff and is dropped, so the same schedule is never
    announced twice.

    Serialised: a broadcast takes as long as it takes to reach every
    subscriber, and a second schedule arriving meanwhile would read the state
    its predecessor has not written yet, diff against it, and then overwrite —
    losing one of the two updates. Waiting costs a delay; interleaving costs
    data.
    """
    async with _broadcast_lock:
        return await _process_parsed(parsed)


async def _process_parsed(parsed: dict) -> bool:
    parsed_date = parsed.get("date")
    logger.info(
        "Schedule for date=%s updated_at=%s source=%s",
        parsed_date,
        parsed.get("updated_at") or parsed.get("timestamp"),
        parsed.get("source", "?"),
    )

    state = load_state(STATE_FILE_PATH)
    first_update = is_new_day(state, parsed_date)

    diff = None
    if not first_update and parsed_date:
        stored = state[parsed_date]["schedule"]
        diff = compute_diff(stored, parsed["schedule"])
        if not diff:
            _refresh_stamp(state, parsed)
            logger.info("No changes detected, skipping notification")
            return False
        if has_outages(stored) and not has_outages(parsed["schedule"]):
            # The provider called the whole day off. Everyone planned around
            # the old schedule, so this goes out regardless of queue.
            logger.info("Schedule for %s cancelled by the provider", parsed_date)
            subscribers = load_subscribers(SUBSCRIBERS_FILE_PATH)
            await broadcast(BOT_TOKEN, list(subscribers.keys()),
                            _cancellation_message(parsed))
            _persist(state, parsed)
            return True

        if not has_outages(stored) and has_outages(parsed["schedule"]):
            # All we had for this date was a silently recorded "nothing
            # planned". A grid arriving afterwards is the schedule's first
            # publication, not an amendment to one — announce it as such,
            # to everyone, instead of listing every queue as a change.
            logger.info("Schedule published for %s after a quiet forecast", parsed_date)
            first_update = True
            diff = None
    elif first_update and parsed_date and not has_outages(parsed["schedule"]):
        # The provider announced "no outages planned" for a date we had not
        # seen. Worth recording so /schedule can answer, not worth a broadcast —
        # otherwise every quiet day would wake every subscriber.
        _persist(state, parsed)
        logger.info("No outages announced for %s, recorded without notifying", parsed_date)
        return False

    subscribers = load_subscribers(SUBSCRIBERS_FILE_PATH)
    if not subscribers:
        logger.warning("No subscribers, skipping send")
        return False

    picture = _grid_picture(parsed)

    for chat_id, queue in subscribers.items():
        if not first_update and diff is not None and queue:
            user_diff = [c for c in diff if c["queue"] == queue]
            if not user_diff:
                continue  # no changes relevant to this user's queue
        else:
            user_diff = diff

        msg = format_schedule(parsed, user_diff, first_update, queue_filter=queue)
        await _send_schedule(chat_id, picture, msg)
        await asyncio.sleep(BROADCAST_DELAY)

    _persist(state, parsed)
    return True


def _refresh_stamp(state: dict, parsed: dict) -> None:
    """Record a re-publication that left the grid untouched.

    The provider can restamp a schedule without changing a single cell. There is
    nothing to announce, but "станом на" should still tell the truth, so the
    stamp is written without touching update_count — that counter tracks
    announced changes, and no change was announced.

    A schedule recognised from a screenshot carries no provider stamp, so it
    never overwrites one taken from the site.
    """
    updated_at = parsed.get("updated_at")
    entry = state.get(parsed.get("date"))
    if not entry or not updated_at or entry.get("updated_at") == updated_at:
        return

    entry["updated_at"] = updated_at
    entry["last_timestamp"] = parsed.get("timestamp")
    entry["source"] = parsed.get("source")
    save_state(state, STATE_FILE_PATH)
    logger.info("Stamp for %s refreshed to %s (schedule unchanged)",
                parsed["date"], updated_at)


def _persist(state: dict, parsed: dict) -> None:
    """Write the schedule to state and to the history archive."""
    parsed_date = parsed.get("date")
    if not parsed_date:
        return

    new_state = build_state(state, parsed)
    save_state(new_state, STATE_FILE_PATH)
    logger.info("State saved (update #%d for %s)", new_state[parsed_date]["update_count"], parsed_date)

    try:
        history = load_history(HISTORY_FILE_PATH)
        save_history(record_day(history, parsed_date, parsed["schedule"]), HISTORY_FILE_PATH)
    except Exception:
        logger.exception("Failed to save history")


def _is_stale(date: str) -> bool:
    """True for a schedule whose day is already over.

    The provider only ever publishes today and tomorrow, and tomorrow's grid
    appears in the evening. So a past date can only mean the endpoint served
    something stale — and since state keeps just two days, that date may well
    have been pruned, which would make the bot announce yesterday's outages
    as news.
    """
    try:
        day = datetime.strptime(date, "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return False
    return day < datetime.now(UKRAINE_TZ).date()


async def poll_site() -> None:
    """Poll poe.pl.ua — the provider itself — for published schedules.

    The endpoint answers year-round: outside outage periods it returns the
    "not expected" notices instead of grids, so a quiet reply is a healthy
    reply, not a failure.
    """
    logger.info("Polling poe.pl.ua every %ds", POE_POLL_INTERVAL)
    unreachable = 0

    while True:
        try:
            if _broadcast_lock.locked():
                # Fetching now would queue up behind the broadcast and then act
                # on data read before it finished. Next tick refetches instead.
                logger.info("Broadcast still running, skipping this poll")
                await asyncio.sleep(POE_POLL_INTERVAL)
                continue

            days = await fetch_days()
            if not days:
                logger.debug("poe.pl.ua has published nothing yet")
            for day in days:
                if _is_stale(day.get("date")):
                    logger.info("Ignoring stale schedule for %s", day.get("date"))
                    continue
                await process_parsed(day)
            unreachable = 0
        except PoeParseError:
            # Shape changed on their side: louder than a network blip, because
            # it means the parser needs a human, and the bot is now blind.
            logger.exception("poe.pl.ua markup no longer matches the parser")
            unreachable = 0
        except httpx.TransportError as e:
            # Some hosts cannot reach the provider at all — it drops their
            # packets, which surfaces as a connect timeout every single poll.
            # A stack trace per attempt buries the log in something that is a
            # property of where the bot runs, not a fault in it.
            unreachable += 1
            logger.warning("poe.pl.ua unreachable (%s), attempt %d; retrying in %ds",
                           type(e).__name__, unreachable, POE_POLL_INTERVAL)
            if unreachable == UNREACHABLE_ALERT_AFTER:
                logger.error(
                    "poe.pl.ua has been unreachable for %d polls (~%d min). If this "
                    "host is blocked, set POE_PROXY or move the bot closer to the "
                    "provider; the Telegram fallback keeps working meanwhile.",
                    unreachable, unreachable * POE_POLL_INTERVAL // 60)
        except Exception:
            logger.exception("Failed to fetch schedule from poe.pl.ua, retrying in %ds",
                             POE_POLL_INTERVAL)

        await asyncio.sleep(POE_POLL_INTERVAL)


def _queue_emoji(queue: str) -> str:
    return QUEUE_EMOJI.get(queue.split(".")[0], "⚡")


def _safe_int(s: str) -> int | None:
    try:
        return int(s)
    except ValueError:
        return None


async def _send_keyboard(client, chat_id, text, keyboard) -> None:
    await client.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "reply_markup": {"inline_keyboard": keyboard}},
    )


async def send_fq_start(client: httpx.AsyncClient, chat_id: int) -> None:
    """Begin the 'which queue is mine?' wizard: pick a city or type a village."""
    if QUEUES is None:
        await send_message(BOT_TOKEN, chat_id, "ℹ️ Довідник черг тимчасово недоступний.")
        return
    fq_state[chat_id] = {"step": "place"}
    keyboard = [
        [{"text": MAJOR_CITIES[i].replace("м. ", ""), "callback_data": f"fq_city_{i}"},
         {"text": MAJOR_CITIES[i + 1].replace("м. ", ""), "callback_data": f"fq_city_{i + 1}"}]
        for i in range(0, len(MAJOR_CITIES), 2)
    ]
    await _send_keyboard(client, chat_id,
        "🔍 <b>Яка у мене черга?</b>\n\n"
        "Оберіть місто або напишіть назву населеного пункту (напр.: <i>Потоки</i>):",
        keyboard)


# Small places show all streets as buttons; large ones (cities) need typing.
FQ_BUTTON_LIMIT = 12


async def _fq_after_place(client: httpx.AsyncClient, chat_id: int, place: str) -> None:
    """Place is known: whole-village → answer; few streets → buttons; else ask."""
    n = QUEUES.place_streets_count(place)
    if n == 0:
        entries = QUEUES.whole_entries(place)
        queues = sorted({e["queue"] for e in entries})
        if not entries:
            await send_message(BOT_TOKEN, chat_id, f"ℹ️ Немає даних по «{place}».")
        elif len(queues) == 1:
            await _fq_send_result(client, chat_id, place, None, queues)
        else:
            # same name in several districts → let the user pick their area
            lines = [f"📍 <b>{place}</b> зустрічається у кількох районах.",
                     "Оберіть свій:"]
            keyboard = [[{"text": f"{e.get('area', '?')} · {_queue_emoji(e['queue'])} черга {e['queue']}",
                          "callback_data": f"fq_sub_{e['queue']}"}] for e in entries]
            await _send_keyboard(client, chat_id, "\n".join(lines), keyboard)
        fq_state.pop(chat_id, None)
        return
    if n <= FQ_BUTTON_LIMIT:
        await _fq_send_street_candidates(client, chat_id, place, QUEUES.street_keys(place),
            prompt=f"📍 {place}\nОберіть вашу вулицю:")
        return
    fq_state[chat_id] = {"step": "street", "place": place}
    await send_message(BOT_TOKEN, chat_id,
        f"📍 {place}\nНапишіть назву вашої вулиці:")


async def _fq_send_street_candidates(client, chat_id, place, keys,
                                     prompt="Можливо, ви мали на увазі:") -> None:
    fq_state[chat_id] = {"step": "street", "place": place, "streets": keys}
    keyboard = [[{"text": k, "callback_data": f"fq_street_{i}"}] for i, k in enumerate(keys)]
    keyboard.append([{"text": "🔄 Обрати інше місто", "callback_data": "find_queue"}])
    await _send_keyboard(client, chat_id, prompt, keyboard)


async def _fq_send_result(client, chat_id, place, street, queues) -> None:
    """Show the resolved queue(s) and offer to subscribe when unambiguous."""
    loc = place if street is None else f"{place}, {street}"
    if len(queues) == 1:
        q = queues[0]
        text = f"{_queue_emoji(q)} Ваша черга: <b>{q}</b>\n<i>{loc}</i>"
        keyboard = [[{"text": f"✅ Отримувати сповіщення для {q}",
                      "callback_data": f"fq_sub_{q}"}]]
        await _send_keyboard(client, chat_id, text, keyboard)
    else:
        lines = [f"<i>{loc}</i> — кілька черг:"]
        for q in queues:
            lines.append(f"{_queue_emoji(q)} черга {q}")
        lines.append("\nОберіть свою чергу для сповіщень:")
        keyboard = [[{"text": f"{_queue_emoji(q)} {q}", "callback_data": f"fq_sub_{q}"}
                     for q in queues]]
        await _send_keyboard(client, chat_id, "\n".join(lines), keyboard)


async def _fq_send_street(client, chat_id, place, street_key) -> None:
    """Street chosen: single queue → answer; split street → list houses + buttons."""
    entries = QUEUES.street_entries(place, street_key)
    queues = sorted({e["queue"] for e in entries})
    if len(queues) == 1:
        await _fq_send_result(client, chat_id, place, street_key, queues)
        fq_state.pop(chat_id, None)
        return
    # Split street: show each queue's houses so the user finds their own number.
    lines = [f"📍 {place}, <b>{street_key}</b>",
             "Вулиця поділена між чергами. Знайдіть свій будинок і оберіть чергу:\n"]
    keyboard_row = []
    for e in sorted(entries, key=lambda x: x["queue"]):
        q = e["queue"]
        houses = ", ".join(e["houses"]) if e["houses"] else "—"
        if len(houses) > 300:
            houses = houses[:300] + "…"
        lines.append(f"{_queue_emoji(q)} <b>{q}</b> — буд.: {houses}")
        keyboard_row.append({"text": f"{_queue_emoji(q)} {q}", "callback_data": f"fq_sub_{q}"})
    fq_state.pop(chat_id, None)
    await _send_keyboard(client, chat_id, "\n".join(lines), [keyboard_row])


async def handle_fq_text(client: httpx.AsyncClient, chat_id: int, text: str) -> None:
    """Handle a free-text message while the user is in the wizard."""
    state = fq_state.get(chat_id)
    if not state or QUEUES is None:
        return
    if state["step"] == "place":
        place = QUEUES.resolve_place(text)
        if place:
            await _fq_after_place(client, chat_id, place)
            return
        matches = QUEUES.search_places(text)
        if not matches:
            await send_message(BOT_TOKEN, chat_id,
                "🤷 Не знайшов такого населеного пункту. Спробуйте ще раз:")
        elif len(matches) == 1:
            await _fq_after_place(client, chat_id, matches[0])
        else:
            fq_state[chat_id] = {"step": "place", "places": matches}
            keyboard = [[{"text": p, "callback_data": f"fq_place_{i}"}]
                        for i, p in enumerate(matches)]
            await _send_keyboard(client, chat_id, "Можливо, ви мали на увазі:", keyboard)
    elif state["step"] == "street":
        place = state["place"]
        matches = QUEUES.search_streets(place, text)
        if not matches:
            # never dead-end: for a small place show every street; otherwise
            # let the user retry or restart
            if QUEUES.place_streets_count(place) <= FQ_BUTTON_LIMIT:
                await _fq_send_street_candidates(client, chat_id, place,
                    QUEUES.street_keys(place),
                    prompt="🤷 Не знайшов. Ось вулиці цього населеного пункту:")
            else:
                await _send_keyboard(client, chat_id,
                    "🤷 Не знайшов такої вулиці. Спробуйте написати інакше "
                    "або почніть заново:",
                    [[{"text": "🔄 Обрати інше місто", "callback_data": "find_queue"}]])
        elif len(matches) == 1:
            await _fq_send_street(client, chat_id, place, matches[0])
        else:
            await _fq_send_street_candidates(client, chat_id, place, matches)


async def send_start_message(client: httpx.AsyncClient, chat_id: int) -> None:
    """Send welcome message with bot image and inline buttons."""
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Підписатись", "callback_data": "subscribe"},
                {"text": "❌ Відписатись", "callback_data": "unsubscribe"},
            ],
            [
                {"text": "📋 Поточний графік", "callback_data": "show_current"},
                {"text": "📅 Графік на завтра", "callback_data": "show_tomorrow"},
            ],
            [
                {"text": "⚡ Є світло зараз?", "callback_data": "show_status"},
            ],
            [
                {"text": "🔍 Яка у мене черга?", "callback_data": "find_queue"},
            ],
            [
                {"text": "⚙️ Моя черга", "callback_data": "select_queue"},
                {"text": "📊 Статистика", "callback_data": "show_stats"},
            ],
        ]
    }
    caption = (
        "Привіт! Цей бот надсилає графік відключень електроенергії.\n\n"
        "📋 Поточний графік — розклад на сьогодні\n"
        "📅 Графік на завтра — якщо вже опубліковано\n"
        "⚡ Є світло зараз? — поточний статус прямо зараз\n"
        "🔍 Яка у мене черга? — визначити чергу за адресою\n"
        "⚙️ Моя черга — персональні сповіщення по своїй черзі\n"
        "📊 Статистика — години відключень за тиждень/місяць\n\n"
        "З побажаннями та зауваженнями звертайтесь до @M_AHTS."
    )
    image_path = os.path.join(os.path.dirname(__file__), "assets", "bot_title.png")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(image_path, "rb") as f:
            resp = await client.post(url, data={
                "chat_id": chat_id,
                "caption": caption,
                "reply_markup": json.dumps(keyboard),
            }, files={"photo": ("bot_title.png", f, "image/png")})
        if resp.status_code != 200:
            raise RuntimeError(f"sendPhoto returned {resp.status_code}")
    except Exception:
        logger.exception("Failed to send start photo, falling back to text")
        await client.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": caption, "reply_markup": keyboard},
        )


async def send_queue_selector(client: httpx.AsyncClient, chat_id: int) -> None:
    """Send inline keyboard for queue selection."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    keyboard = [
        [
            {"text": QUEUE_LABELS[i], "callback_data": f"set_queue_{QUEUE_LABELS[i]}"},
            {"text": QUEUE_LABELS[i + 1], "callback_data": f"set_queue_{QUEUE_LABELS[i + 1]}"},
        ]
        for i in range(0, len(QUEUE_LABELS), 2)
    ]
    keyboard.append([{"text": "🔄 Всі черги", "callback_data": "set_queue_all"}])
    await client.post(url, json={
        "chat_id": chat_id,
        "text": "Оберіть свою чергу. Ви будете отримувати лише її графік та зміни.\n"
                "«Всі черги» — повний графік без фільтру.",
        "reply_markup": {"inline_keyboard": keyboard},
    })


async def send_stats_selector(client: httpx.AsyncClient, chat_id: int) -> None:
    """Send inline keyboard to choose stats period."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    await client.post(url, json={
        "chat_id": chat_id,
        "text": "Оберіть період статистики:",
        "reply_markup": {"inline_keyboard": [[
            {"text": "📅 За тиждень", "callback_data": "stats_7"},
            {"text": "🗓 За місяць", "callback_data": "stats_30"},
        ]]},
    })


async def answer_callback(client: httpx.AsyncClient, callback_query_id: str, text: str) -> None:
    """Answer a callback query (dismisses the loading indicator on the button)."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
    await client.post(url, json={"callback_query_id": callback_query_id, "text": text})


async def poll_commands() -> None:
    """Poll Bot API for commands and inline button callbacks."""
    url_base = f"https://api.telegram.org/bot{BOT_TOKEN}"
    offset = 0

    async with httpx.AsyncClient(timeout=60) as client:
        while True:
            try:
                resp = await client.get(
                    f"{url_base}/getUpdates",
                    params={"offset": offset, "timeout": 30, "allowed_updates": ["message", "callback_query", "channel_post"]},
                )
                if resp.status_code != 200:
                    await asyncio.sleep(5)
                    continue

                updates = resp.json().get("result", [])
                for update in updates:
                    offset = update["update_id"] + 1

                    # Handle channel post (bot must be admin of the channel)
                    if "channel_post" in update:
                        post = update["channel_post"]
                        caption = post.get("caption") or ""
                        if post.get("photo") and "графік" in caption.lower():
                            file_id = post["photo"][-1]["file_id"]
                            logger.info("Channel post with schedule received, processing...")
                            try:
                                file_resp = await client.get(
                                    f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                                    params={"file_id": file_id},
                                )
                                file_path_tg = file_resp.json()["result"]["file_path"]
                                img_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path_tg}"
                                img_resp = await client.get(img_url)
                                import tempfile, os as _os
                                suffix = _os.path.splitext(file_path_tg)[1] or ".jpg"
                                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                                    f.write(img_resp.content)
                                    tmp_path = f.name
                                try:
                                    await process_image(tmp_path)
                                finally:
                                    if _os.path.exists(tmp_path):
                                        _os.unlink(tmp_path)
                            except Exception:
                                logger.exception("Failed to process channel post photo")
                        continue

                    # Handle inline button press
                    if "callback_query" in update:
                        cq = update["callback_query"]
                        # message is absent for expired callbacks
                        chat_id = (cq.get("message") or {}).get("chat", {}).get("id")
                        data = cq.get("data", "")
                        if not chat_id:
                            await answer_callback(client, cq["id"], "")
                            continue

                        if data == "subscribe":
                            added = add_subscriber(chat_id, SUBSCRIBERS_FILE_PATH)
                            if added:
                                logger.info("New subscriber: %d", chat_id)
                                await answer_callback(client, cq["id"],
                                    "✅ Ви підписались на графік відключень.\n"
                                    "З побажаннями та зауваженнями звертайтесь до @M_AHTS."
                                )
                            else:
                                await answer_callback(client, cq["id"], "ℹ️ Ви вже підписані.")

                        elif data == "unsubscribe":
                            removed = remove_subscriber(chat_id, SUBSCRIBERS_FILE_PATH)
                            if removed:
                                logger.info("Unsubscribed: %d", chat_id)
                                await answer_callback(client, cq["id"], "✅ Ви відписались від графіку відключень.")
                            else:
                                await answer_callback(client, cq["id"], "ℹ️ Ви не були підписані.")

                        elif data == "show_current":
                            await answer_callback(client, cq["id"], "📋 Надсилаю поточний графік...")
                            await send_current_schedule(chat_id)

                        elif data == "show_tomorrow":
                            await answer_callback(client, cq["id"], "📅 Надсилаю графік на завтра...")
                            await send_tomorrow_schedule(chat_id)

                        elif data == "select_queue":
                            await answer_callback(client, cq["id"], "")
                            await send_queue_selector(client, chat_id)

                        elif data == "show_status":
                            await answer_callback(client, cq["id"], "⚡ Перевіряю...")
                            await send_current_status(chat_id)

                        elif data == "show_stats":
                            await answer_callback(client, cq["id"], "")
                            await send_stats_selector(client, chat_id)

                        elif data in ("stats_7", "stats_30"):
                            days = 7 if data == "stats_7" else 30
                            await answer_callback(client, cq["id"], "📊 Рахую статистику...")
                            queue = load_subscribers(SUBSCRIBERS_FILE_PATH).get(chat_id)
                            history = load_history(HISTORY_FILE_PATH)
                            msg = compute_stats(history, queue, days)
                            await send_message(BOT_TOKEN, chat_id, msg)

                        elif data.startswith("set_queue_"):
                            queue_value = data[len("set_queue_"):]
                            queue = None if queue_value == "all" else queue_value
                            set_subscriber_queue(chat_id, queue, SUBSCRIBERS_FILE_PATH)
                            if queue:
                                logger.info("Queue set: %d -> %s", chat_id, queue)
                                await answer_callback(client, cq["id"], f"✅ Ваша черга: {queue}")
                            else:
                                logger.info("Queue cleared: %d", chat_id)
                                await answer_callback(client, cq["id"], "✅ Отримуєте повний графік")

                        elif data == "find_queue":
                            await answer_callback(client, cq["id"], "")
                            await send_fq_start(client, chat_id)

                        elif data.startswith("fq_city_"):
                            await answer_callback(client, cq["id"], "")
                            idx = _safe_int(data[len("fq_city_"):])
                            if idx is not None and 0 <= idx < len(MAJOR_CITIES):
                                await _fq_after_place(client, chat_id, MAJOR_CITIES[idx])

                        elif data.startswith("fq_place_"):
                            await answer_callback(client, cq["id"], "")
                            idx = _safe_int(data[len("fq_place_"):])
                            places = fq_state.get(chat_id, {}).get("places", [])
                            if idx is not None and 0 <= idx < len(places):
                                await _fq_after_place(client, chat_id, places[idx])
                            elif not places:
                                # wizard state lost (e.g. after redeploy)
                                await send_fq_start(client, chat_id)

                        elif data.startswith("fq_street_"):
                            await answer_callback(client, cq["id"], "")
                            idx = _safe_int(data[len("fq_street_"):])
                            st = fq_state.get(chat_id, {})
                            streets = st.get("streets", [])
                            if (idx is not None and st.get("place")
                                    and 0 <= idx < len(streets)):
                                await _fq_send_street(client, chat_id, st["place"], streets[idx])
                            elif not streets:
                                # wizard state lost (e.g. after redeploy)
                                await send_fq_start(client, chat_id)

                        elif data.startswith("fq_sub_"):
                            queue = data[len("fq_sub_"):]
                            # set_subscriber_queue also registers the subscriber
                            set_subscriber_queue(chat_id, queue, SUBSCRIBERS_FILE_PATH)
                            logger.info("Queue set via find_queue: %d -> %s", chat_id, queue)
                            fq_state.pop(chat_id, None)
                            await answer_callback(client, cq["id"],
                                f"✅ Готово! Отримуватимете сповіщення для черги {queue}")
                        continue

                    # Handle text commands and admin photo uploads
                    message = update.get("message", {})
                    chat_id = message.get("chat", {}).get("id")
                    if not chat_id:
                        continue

                    # Admin can send a photo or document to the bot to force-process it
                    # Documents are preferred — Telegram doesn't compress them
                    photo = message.get("photo")
                    document = message.get("document")
                    if chat_id == USER_CHAT_ID and (photo or document):
                        if document:
                            file_id = document["file_id"]
                            logger.info("Admin document received, processing as schedule...")
                        else:
                            file_id = photo[-1]["file_id"]
                            logger.info("Admin photo received, processing as schedule...")
                        try:
                            file_resp = await client.get(
                                f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                                params={"file_id": file_id},
                            )
                            file_path = file_resp.json()["result"]["file_path"]
                            img_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                            img_resp = await client.get(img_url)
                            import tempfile, os as _os
                            suffix = _os.path.splitext(file_path)[1] or ".jpg"
                            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                                f.write(img_resp.content)
                                tmp_path = f.name
                            now = datetime.now(UKRAINE_TZ)
                            caption = message.get("caption") or ""
                            caption_date_match = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", caption)
                            if caption_date_match:
                                d, m, y = caption_date_match.group(1), caption_date_match.group(2), caption_date_match.group(3)
                                forced_date = f"{int(d):02d}.{int(m):02d}.{y}"
                            else:
                                forced_date = now.strftime("%d.%m.%Y")
                            try:
                                changed = await process_image(tmp_path, date=forced_date, timestamp=now.strftime("%H:%M"))
                                if changed:
                                    await send_message(BOT_TOKEN, chat_id, "✅ Графік оброблено.")
                                else:
                                    await send_message(BOT_TOKEN, chat_id, "ℹ️ Змін не виявлено, підписники не сповіщені.")
                            finally:
                                if _os.path.exists(tmp_path):
                                    _os.unlink(tmp_path)
                        except Exception:
                            logger.exception("Failed to process admin photo")
                            await send_message(BOT_TOKEN, chat_id, "❌ Помилка обробки фото.")
                        continue

                    text = message.get("text", "")
                    if not text:
                        continue

                    # A command aborts any in-progress wizard.
                    if text.startswith("/"):
                        fq_state.pop(chat_id, None)
                    elif chat_id in fq_state:
                        await handle_fq_text(client, chat_id, text.strip())
                        continue

                    if text.startswith("/start"):
                        await send_start_message(client, chat_id)

                    elif text.startswith("/status"):
                        if chat_id != USER_CHAT_ID:
                            continue
                        subs = load_subscribers(SUBSCRIBERS_FILE_PATH)
                        state = load_state(STATE_FILE_PATH)

                        lines = [f"👥 Підписників: {len(subs)}"]
                        for cid, queue in subs.items():
                            lines.append(f"  • {cid} — {queue or 'всі черги'}")

                        if state:
                            for date, entry in sorted(state.items()):
                                lines.append(
                                    f"\n📅 {date} — оновлення #{entry['update_count']}, "
                                    f"станом на {entry.get('last_timestamp') or '?'}"
                                )
                        else:
                            lines.append("\nℹ️ Стейт порожній")

                        await send_message(BOT_TOKEN, chat_id, "\n".join(lines))

                    elif text.startswith("/subscribe"):
                        added = add_subscriber(chat_id, SUBSCRIBERS_FILE_PATH)
                        if added:
                            logger.info("New subscriber: %d", chat_id)
                            await send_message(BOT_TOKEN, chat_id,
                                "✅ Ви підписались на графік відключень.\n\n"
                                "З побажаннями та зауваженнями щодо роботи бота звертайтесь до @M_AHTS."
                            )
                        else:
                            await send_message(BOT_TOKEN, chat_id, "ℹ️ Ви вже підписані.")

                    elif text.startswith("/unsubscribe"):
                        removed = remove_subscriber(chat_id, SUBSCRIBERS_FILE_PATH)
                        if removed:
                            logger.info("Unsubscribed: %d", chat_id)
                            await send_message(BOT_TOKEN, chat_id, "✅ Ви відписались від графіку відключень.")
                        else:
                            await send_message(BOT_TOKEN, chat_id, "ℹ️ Ви не були підписані.")

            except Exception:
                logger.exception("Error in poll_commands")
                await asyncio.sleep(5)




async def main():
    # Seed initial subscriber if list is empty
    subs = load_subscribers(SUBSCRIBERS_FILE_PATH)
    if not subs:
        add_subscriber(USER_CHAT_ID, SUBSCRIBERS_FILE_PATH)
        logger.info("Seeded initial subscriber: %d", USER_CHAT_ID)

    tasks = [poll_commands()]

    if POE_SOURCE_ENABLED:
        tasks.append(poll_site())

    if TELEGRAM_SOURCE_ENABLED:
        client = create_client(TELETHON_API_ID, TELETHON_API_HASH, TELETHON_SESSION_STRING)
        await client.connect()
        tasks.append(monitor_channel(client, CHANNEL_USERNAME, process_image))

    if not (POE_SOURCE_ENABLED or TELEGRAM_SOURCE_ENABLED):
        logger.warning("Both schedule sources are disabled — the bot will only answer commands")

    logger.info("Bot is running.")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
