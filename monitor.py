import logging
import os
import tempfile

from telethon import TelegramClient, events
from telethon.sessions import StringSession

logger = logging.getLogger(__name__)


def create_client(api_id: int, api_hash: str, session_string: str) -> TelegramClient:
    """Create a Telethon client with a string session."""
    return TelegramClient(StringSession(session_string), api_id, api_hash)


def setup_handler(client: TelegramClient, channel: str, callback):
    """Register a handler for new photo messages in the given channel.

    Args:
        client: Telethon client.
        channel: Channel username or numeric ID.
        callback: Async function(image_path: str) called with the downloaded image path.
    """

    @client.on(events.NewMessage(chats=channel))
    async def on_new_message(event):
        if not event.photo:
            return

        logger.info("New photo in channel, downloading...")
        tmp_dir = tempfile.gettempdir()
        path = await event.download_media(file=os.path.join(tmp_dir, "schedule_"))

        if path is None:
            logger.warning("Failed to download media")
            return

        try:
            await callback(path)
        finally:
            if os.path.exists(path):
                os.unlink(path)
