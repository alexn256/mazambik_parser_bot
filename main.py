import asyncio
import logging

from config import (
    BOT_TOKEN,
    CHANNEL_USERNAME,
    STATE_FILE_PATH,
    TELETHON_API_HASH,
    TELETHON_API_ID,
    TELETHON_SESSION_STRING,
    USER_CHAT_ID,
)
from diff import compute_diff
from formatter import format_schedule
from monitor import create_client, setup_handler
from parser import parse_schedule_image
from sender import send_message
from state import build_state, is_new_day, load_state, save_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def process_image(image_path: str) -> None:
    """Full pipeline: parse image -> diff -> format -> send -> save state."""
    logger.info("Processing image: %s", image_path)

    try:
        parsed = parse_schedule_image(image_path)
    except Exception:
        logger.exception("Failed to parse schedule image")
        await send_message(BOT_TOKEN, USER_CHAT_ID, "\u274c Не вдалось розпізнати графік")
        return

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

    success = await send_message(BOT_TOKEN, USER_CHAT_ID, message)
    if success:
        new_state = build_state(parsed, prev_state)
        save_state(new_state, STATE_FILE_PATH)
        logger.info("State saved (update #%d)", new_state["update_count"])
    else:
        logger.error("Failed to send message, state not updated")


async def main():
    client = create_client(TELETHON_API_ID, TELETHON_API_HASH, TELETHON_SESSION_STRING)
    setup_handler(client, CHANNEL_USERNAME, process_image)

    logger.info("Starting monitor for channel: %s", CHANNEL_USERNAME)
    await client.start()
    logger.info("Bot is running. Waiting for new schedule images...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
