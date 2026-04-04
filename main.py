import asyncio
import logging

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
from state import build_state, is_new_day, load_state, save_state
from subscribers import add_subscriber, load_subscribers, remove_subscriber

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def send_current_schedule(chat_id: int) -> None:
    """Send the current saved schedule to a single user, or a fallback message."""
    state = load_state(STATE_FILE_PATH)
    if not state:
        await send_message(BOT_TOKEN, chat_id,
            "ℹ️ Графік ще не отримано. Очікуйте публікації у каналі.")
        return

    parsed = {
        "date": state.get("date"),
        "timestamp": state.get("last_timestamp"),
        "schedule": state["schedule"],
    }
    message = format_schedule(parsed, diff=None, is_first=True)
    await send_message(BOT_TOKEN, chat_id, message)


async def process_image(image_path: str, date: str | None = None, timestamp: str | None = None) -> None:
    """Full pipeline: parse image -> diff -> format -> send -> save state."""
    logger.info("Processing image: %s", image_path)

    try:
        parsed = parse_schedule_image(image_path)
    except Exception:
        logger.exception("Failed to parse schedule image")
        await broadcast(BOT_TOKEN, load_subscribers(SUBSCRIBERS_FILE_PATH), "❌ Не вдалось розпізнати графік")
        return

    # Use date/timestamp from message if watermark OCR failed
    if date and not parsed["date"]:
        parsed["date"] = date
    if timestamp and not parsed["timestamp"]:
        parsed["timestamp"] = timestamp

    logger.info("Parsed schedule for date=%s time=%s", parsed["date"], parsed["timestamp"])

    prev_state = load_state(STATE_FILE_PATH)
    first_update = is_new_day(prev_state, parsed.get("date"))

    diff = None
    if not first_update and prev_state:
        diff = compute_diff(prev_state["schedule"], parsed["schedule"])
        if not diff:
            logger.info("No changes detected, skipping notification")
            return

    message = format_schedule(parsed, diff, first_update)
    subscribers = load_subscribers(SUBSCRIBERS_FILE_PATH)

    if not subscribers:
        logger.warning("No subscribers, skipping send")
        return

    await broadcast(BOT_TOKEN, subscribers, message)
    new_state = build_state(parsed, prev_state)
    save_state(new_state, STATE_FILE_PATH)
    logger.info("State saved (update #%d)", new_state["update_count"])


async def send_start_message(client: httpx.AsyncClient, chat_id: int) -> None:
    """Send welcome message with inline subscribe/unsubscribe buttons."""
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
                ],
            ]
        }
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
