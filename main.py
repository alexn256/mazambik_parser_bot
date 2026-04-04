import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx

from config import (
    BOT_TOKEN,
    CHANNEL_USERNAME,
    STATE_FILE_PATH,
    SUBSCRIBERS_FILE_PATH,
    TELETHON_API_HASH,
    TELETHON_API_ID,
    TELETHON_SESSION_STRING,
    USER_CHAT_ID,
)
from diff import compute_diff
from formatter import format_schedule
from monitor import create_client, setup_handler
from parser import parse_schedule_image
from sender import broadcast, send_message
from state import build_state, get_latest_state, is_new_day, load_state, save_state
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

QUEUE_LABELS = ["1.1", "1.2", "2.1", "2.2", "3.1", "3.2",
                "4.1", "4.2", "5.1", "5.2", "6.1", "6.2"]


async def send_current_schedule(chat_id: int) -> None:
    """Send the latest saved schedule to a single user, or a fallback message."""
    state = load_state(STATE_FILE_PATH)
    result = get_latest_state(state)
    if not result:
        await send_message(BOT_TOKEN, chat_id,
            "ℹ️ Графік ще не отримано. Очікуйте публікації у каналі.")
        return
    date, entry = result
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


async def process_image(image_path: str, date: str | None = None, timestamp: str | None = None) -> None:
    """Full pipeline: parse image -> diff -> format -> send -> save state."""
    logger.info("Processing image: %s", image_path)

    try:
        parsed = parse_schedule_image(image_path)
    except Exception:
        logger.exception("Failed to parse schedule image")
        subs = load_subscribers(SUBSCRIBERS_FILE_PATH)
        await broadcast(BOT_TOKEN, list(subs.keys()), "❌ Не вдалось розпізнати графік")
        return

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
            return

    subscribers = load_subscribers(SUBSCRIBERS_FILE_PATH)
    if not subscribers:
        logger.warning("No subscribers, skipping send")
        return

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


async def send_start_message(client: httpx.AsyncClient, chat_id: int) -> None:
    """Send welcome message with inline buttons."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    await client.post(url, json={
        "chat_id": chat_id,
        "text": "Привіт! Цей бот надсилає графік відключень електроенергії.\n"
                "З побажаннями та зауваженнями звертайтесь до @M_AHTS.",
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
                    {"text": "⚙️ Моя черга", "callback_data": "select_queue"},
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
                    params={"offset": offset, "timeout": 30, "allowed_updates": ["message", "callback_query"]},
                )
                if resp.status_code != 200:
                    await asyncio.sleep(5)
                    continue

                updates = resp.json().get("result", [])
                for update in updates:
                    offset = update["update_id"] + 1

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

                    # Handle text commands
                    message = update.get("message", {})
                    text = message.get("text", "")
                    chat_id = message.get("chat", {}).get("id")

                    if not chat_id or not text:
                        continue

                    if text.startswith("/start"):
                        await send_start_message(client, chat_id)

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
    setup_handler(client, CHANNEL_USERNAME, process_image)

    logger.info("Starting monitor for channel: %s", CHANNEL_USERNAME)
    await client.start()
    logger.info("Bot is running. Waiting for new schedule images...")

    await asyncio.gather(
        client.run_until_disconnected(),
        poll_commands(),
    )


if __name__ == "__main__":
    asyncio.run(main())
