import logging
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta

from telethon import TelegramClient, events
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)

UKRAINE_TZ = timezone(timedelta(hours=3))

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


def create_client(api_id: int, api_hash: str, session_string: str) -> TelegramClient:
    """Create a Telethon client with a string session."""
    return TelegramClient(
        StringSession(session_string),
        api_id,
        api_hash,
        connection_retries=-1,
        retry_delay=1,
    )


def setup_handler(client: TelegramClient, channel: str, callback):
    """Register a handler for new photo messages in the given channel.

    callback: async function(image_path, date, timestamp) where date and
    timestamp are extracted from the message (fallback to None if not found).
    """

    @client.on(events.NewMessage(chats=channel))
    async def on_new_message(event):
        if not event.photo:
            return

        caption = event.message.message or ""
        if "графік" not in caption.lower():
            logger.info("Skipping photo without schedule caption")
            return

        msg_date = event.message.date  # UTC datetime
        local_dt = msg_date.astimezone(UKRAINE_TZ)
        timestamp = local_dt.strftime("%H:%M")
        date = _parse_caption_date(caption, msg_date)

        logger.info("New schedule photo: date=%s time=%s", date, timestamp)

        tmp_dir = tempfile.gettempdir()
        path = await event.download_media(file=os.path.join(tmp_dir, "schedule_"))

        if path is None:
            logger.warning("Failed to download media")
            return

        try:
            await callback(path, date, timestamp)
        finally:
            if os.path.exists(path):
                os.unlink(path)
