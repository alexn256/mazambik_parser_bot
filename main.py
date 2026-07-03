import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from config import (
    BOT_TOKEN,
    CHANNEL_USERNAME,
    HISTORY_FILE_PATH,
    QUEUE_LABELS,
    QUEUES_FILE_PATH,
    STATE_FILE_PATH,
    SUBSCRIBERS_FILE_PATH,
    TELETHON_API_HASH,
    TELETHON_API_ID,
    TELETHON_SESSION_STRING,
    USER_CHAT_ID,
)
from diff import compute_diff
from formatter import QUEUE_EMOJI, format_schedule
from queue_lookup import MAJOR_CITIES, QueueLookup
from monitor import create_client, monitor_channel
from parser import parse_schedule_image
from sender import broadcast, send_message
from history import load_history, record_day, save_history
from state import build_state, get_latest_state, is_new_day, load_state, save_state
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


def _format_duration(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    if h > 0 and m > 0:
        return f"{h} год {m} хв"
    elif h > 0:
        return f"{h} год"
    return f"{m} хв"


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
        await send_message(BOT_TOKEN, chat_id,
            "ℹ️ Графік на сьогодні ще не отримано.")
        return

    ranges = entry["schedule"].get(queue, [])
    now = datetime.now(UKRAINE_TZ)
    now_m = now.hour * 60 + now.minute

    # Check if currently in outage
    current_outage = next(
        (r for r in ranges
         if _time_to_minutes(r["start"]) <= now_m < _time_to_minutes(r["end"])),
        None,
    )

    lines = []
    if current_outage:
        remaining = _time_to_minutes(current_outage["end"]) - now_m
        next_outage = _find_next_range(ranges, _time_to_minutes(current_outage["end"]))
        lines.append(f"🔴 Зараз відключення · черга {queue}")
        lines.append(f"до {current_outage['end']} (ще {_format_duration(remaining)})")
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
            lines.append("Відключень більше не заплановано на сьогодні")
        image_name = "power_on.png"

    image_path = os.path.join(os.path.dirname(__file__), "assets", image_name)
    async with httpx.AsyncClient(timeout=30) as client:
        with open(image_path, "rb") as f:
            await client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
                data={"chat_id": chat_id, "caption": "\n".join(lines)},
                files={"photo": (image_name, f, "image/png")},
            )


async def send_current_schedule(chat_id: int) -> None:
    """Send today's schedule (or latest if today's not available) to a single user."""
    state = load_state(STATE_FILE_PATH)
    today = datetime.now(UKRAINE_TZ).strftime("%d.%m.%Y")
    if today not in state:
        await send_message(BOT_TOKEN, chat_id,
            "ℹ️ Графік на сьогодні ще не отримано. Очікуйте публікації у каналі.")
        return
    date, entry = today, state[today]
    queue = load_subscribers(SUBSCRIBERS_FILE_PATH).get(chat_id)
    parsed = {"date": date, "timestamp": entry.get("last_timestamp"), "schedule": entry["schedule"]}
    await send_message(BOT_TOKEN, chat_id, format_schedule(parsed, diff=None, is_first=True, queue_filter=queue))


async def send_tomorrow_schedule(chat_id: int) -> None:
    """Send tomorrow's schedule if already published, or a fallback message."""
    state = load_state(STATE_FILE_PATH)
    tomorrow = (datetime.now(UKRAINE_TZ) + timedelta(days=1)).strftime("%d.%m.%Y")
    entry = state.get(tomorrow)
    if not entry:
        await send_message(BOT_TOKEN, chat_id,
            "ℹ️ Графік на завтра ще не опубліковано.")
        return
    queue = load_subscribers(SUBSCRIBERS_FILE_PATH).get(chat_id)
    parsed = {"date": tomorrow, "timestamp": entry.get("last_timestamp"), "schedule": entry["schedule"]}
    await send_message(BOT_TOKEN, chat_id, format_schedule(parsed, diff=None, is_first=True, queue_filter=queue))


async def process_image(image_path: str, date: str | None = None, timestamp: str | None = None) -> bool:
    """Full pipeline: parse image -> diff -> format -> send -> save state."""
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

    logger.info("Parsed schedule for date=%s time=%s", parsed["date"], parsed["timestamp"])

    state = load_state(STATE_FILE_PATH)
    parsed_date = parsed.get("date")
    first_update = is_new_day(state, parsed_date)

    diff = None
    if not first_update and parsed_date:
        diff = compute_diff(state[parsed_date]["schedule"], parsed["schedule"])
        if not diff:
            logger.info("No changes detected, skipping notification")
            return False

    subscribers = load_subscribers(SUBSCRIBERS_FILE_PATH)
    if not subscribers:
        logger.warning("No subscribers, skipping send")
        return False

    for chat_id, queue in subscribers.items():
        if not first_update and diff is not None and queue:
            user_diff = [c for c in diff if c["queue"] == queue]
            if not user_diff:
                continue  # no changes relevant to this user's queue
        else:
            user_diff = diff

        msg = format_schedule(parsed, user_diff, first_update, queue_filter=queue)
        await send_message(BOT_TOKEN, chat_id, msg)

    if parsed_date:
        new_state = build_state(state, parsed)
        save_state(new_state, STATE_FILE_PATH)
        logger.info("State saved (update #%d for %s)", new_state[parsed_date]["update_count"], parsed_date)

        try:
            history = load_history(HISTORY_FILE_PATH)
            save_history(record_day(history, parsed_date, parsed["schedule"]), HISTORY_FILE_PATH)
        except Exception:
            logger.exception("Failed to save history")

    return True


def _queue_emoji(queue: str) -> str:
    return QUEUE_EMOJI.get(queue.split(".")[0], "⚡")


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
    with open(image_path, "rb") as f:
        await client.post(url, data={
            "chat_id": chat_id,
            "caption": caption,
            "reply_markup": json.dumps(keyboard),
        }, files={"photo": ("bot_title.png", f, "image/png")})


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
                        chat_id = cq["message"]["chat"]["id"]
                        data = cq.get("data", "")

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
                            idx = int(data[len("fq_city_"):])
                            if 0 <= idx < len(MAJOR_CITIES):
                                await _fq_after_place(client, chat_id, MAJOR_CITIES[idx])

                        elif data.startswith("fq_place_"):
                            await answer_callback(client, cq["id"], "")
                            idx = int(data[len("fq_place_"):])
                            places = fq_state.get(chat_id, {}).get("places", [])
                            if 0 <= idx < len(places):
                                await _fq_after_place(client, chat_id, places[idx])

                        elif data.startswith("fq_street_"):
                            await answer_callback(client, cq["id"], "")
                            idx = int(data[len("fq_street_"):])
                            st = fq_state.get(chat_id, {})
                            streets = st.get("streets", [])
                            if st.get("place") and 0 <= idx < len(streets):
                                await _fq_send_street(client, chat_id, st["place"], streets[idx])

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
                        await answer_callback(client, "", "")
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

    client = create_client(TELETHON_API_ID, TELETHON_API_HASH, TELETHON_SESSION_STRING)
    await client.connect()
    logger.info("Bot is running.")

    await asyncio.gather(
        monitor_channel(client, CHANNEL_USERNAME, process_image),
        poll_commands(),
    )


if __name__ == "__main__":
    asyncio.run(main())
