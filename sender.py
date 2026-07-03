import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
# Telegram allows ~30 messages/second across chats; a small gap between
# broadcast sends keeps us far from the limit as subscribers grow.
BROADCAST_DELAY = 0.05


async def send_message(bot_token: str, chat_id: int, text: str) -> bool:
    """Send a text message via Telegram Bot API.

    Retries transient failures with backoff, honours 429 retry_after, and
    gives up immediately on permanent errors (400 bad request, 403 blocked).

    Returns True on success, False on failure.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await client.post(url, json=payload)
            except httpx.RequestError as e:
                logger.warning("Request failed: %s (attempt %d)", e, attempt)
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(2 ** attempt)  # 2s, 4s
                continue

            if resp.status_code == 200:
                return True

            if resp.status_code == 429:
                retry_after = 5
                try:
                    retry_after = int(resp.json()["parameters"]["retry_after"])
                except (KeyError, ValueError, TypeError):
                    pass
                logger.warning("Rate limited for chat %d, waiting %ds", chat_id, retry_after)
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(retry_after)
                continue

            if resp.status_code in (400, 403):
                # 403: user blocked the bot; 400: malformed request — retrying can't help
                logger.warning("Permanent error %d for chat %d: %s",
                               resp.status_code, chat_id, resp.text)
                return False

            logger.warning("Bot API returned %d: %s (attempt %d)",
                           resp.status_code, resp.text, attempt)
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(2 ** attempt)

    logger.error("Failed to send message to chat %d after %d attempts", chat_id, MAX_ATTEMPTS)
    return False


async def broadcast(bot_token: str, chat_ids: list[int], text: str) -> None:
    """Send a message to all subscribers."""
    for chat_id in chat_ids:
        success = await send_message(bot_token, chat_id, text)
        if not success:
            logger.error("Failed to send message to chat_id=%d", chat_id)
        await asyncio.sleep(BROADCAST_DELAY)
