import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from config import (
    BOT_TOKEN,
    CHANNEL_USERNAME,
    HISTORY_FILE_PATH,
    QUEUE_LABELS,
    STATE_FILE_PATH,
    SUBSCRIBERS_FILE_PATH,
    TELETHON_API_HASH,
    TELETHON_API_ID,
    TELETHON_SESSION_STRING,
    USER_CHAT_ID,
)
from diff import compute_diff
from formatter import format_schedule
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

    await send_message(BOT_TOKEN, chat_id, "\n".join(lines))


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


async def send_start_message(client: httpx.AsyncClient, chat_id: int) -> None:
    """Send welcome message with inline buttons."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    await client.post(url, json={
        "chat_id": chat_id,
        "text": (
            "Привіт! Цей бот надсилає графік відключень електроенергії.\n\n"
            "📋 Поточний графік — розклад на сьогодні\n"
            "📅 Графік на завтра — якщо вже опубліковано\n"
            "⚡ Що зараз? — є світло чи ні прямо зараз\n"
            "⚙️ Моя черга — персональні сповіщення по своїй черзі\n"
            "📊 Статистика — години відключень за тиждень/місяць\n\n"
            "З побажаннями та зауваженнями звертайтесь до @M_AHTS."
        ),
        "reply_markup": {
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
                    {"text": "⚡ Що зараз?", "callback_data": "show_status"},
                ],
                [
                    {"text": "⚙️ Моя черга", "callback_data": "select_queue"},
                    {"text": "📊 Статистика", "callback_data": "show_stats"},
                ],
            ]
        }
    })


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
