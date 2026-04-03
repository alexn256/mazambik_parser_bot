import logging
import httpx

logger = logging.getLogger(__name__)


async def send_message(bot_token: str, chat_id: int, text: str) -> bool:
    """Send a text message via Telegram Bot API.

    Returns True on success, False on failure.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(3):
            try:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    return True
                logger.warning(
                    "Bot API returned %d: %s (attempt %d)",
                    resp.status_code, resp.text, attempt + 1,
                )
            except httpx.RequestError as e:
                logger.warning("Request failed: %s (attempt %d)", e, attempt + 1)

    logger.error("Failed to send message after 3 attempts")
    return False


async def broadcast(bot_token: str, chat_ids: list[int], text: str) -> None:
    """Send a message to all subscribers."""
    for chat_id in chat_ids:
        success = await send_message(bot_token, chat_id, text)
        if not success:
            logger.error("Failed to send message to chat_id=%d", chat_id)
