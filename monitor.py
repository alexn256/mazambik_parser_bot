import asyncio
import logging
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta

from telethon import TelegramClient
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

UKRAINE_TZ = timezone(timedelta(hours=3))
POLL_INTERVAL = 60  # seconds between channel checks

UA_MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4,
    "травня": 5, "червня": 6, "липня": 7, "серпня": 8,
    "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
}

DATE_RE = re.compile(r"(\d{1,2})\s+(" + "|".join(UA_MONTHS) + r")", re.IGNORECASE)


def _parse_caption_date(caption: str, msg_date: datetime) -> str | None:
    """Extract date from caption like 'Графік на 24 березня'."""
    match = DATE_RE.search(caption)
    if not match:
        return None
    day = int(match.group(1))
    month = UA_MONTHS[match.group(2).lower()]
    year = msg_date.astimezone(UKRAINE_TZ).year
    return f"{day:02d}.{month:02d}.{year}"


def _is_schedule_message(msg) -> bool:
    """Return True if the message is a schedule photo."""
    if not msg.photo:
        return False
    caption = msg.message or ""
    return "графік" in caption.lower()


def create_client(api_id: int, api_hash: str, session_string: str) -> TelegramClient:
    """Create a Telethon client with a string session."""
    return TelegramClient(
        StringSession(session_string),
        api_id,
        api_hash,
        connection_retries=-1,
        retry_delay=1,
    )


async def _process_message(msg, callback) -> None:
    """Download and process a single schedule message."""
    local_dt = msg.date.astimezone(UKRAINE_TZ)
    timestamp = local_dt.strftime("%H:%M")
    date = _parse_caption_date(msg.message or "", msg.date)
    logger.info("Processing schedule message id=%d date=%s time=%s", msg.id, date, timestamp)

    tmp_dir = tempfile.gettempdir()
    path = await msg.download_media(file=os.path.join(tmp_dir, "schedule_"))
    if path is None:
        logger.warning("Failed to download media for message id=%d", msg.id)
        return
    try:
        await callback(path, date, timestamp)
    finally:
        if os.path.exists(path):
            os.unlink(path)


async def monitor_channel(client: TelegramClient, channel: str, callback) -> None:
    """Poll the channel every POLL_INTERVAL seconds for new schedule messages.

    Tracks the last seen message ID so each message is processed exactly once.
    On startup, processes any recent messages missed while the bot was offline.
    """
    logger.info("Starting channel polling for: %s", channel)

    last_id: int = 0

    # Set baseline to the latest message ID — do not reprocess historical messages.
    # If a message was missed during downtime, admin can upload it manually.
    async for msg in client.iter_messages(channel, limit=1):
        last_id = msg.id

    logger.info("Channel polling active, last_id=%d", last_id)

    while True:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            async for msg in client.iter_messages(channel, min_id=last_id, limit=20):
                if _is_schedule_message(msg):
                    await _process_message(msg, callback)
                last_id = max(last_id, msg.id)
        except Exception:
            logger.exception("Error polling channel, will retry in %ds", POLL_INTERVAL)
