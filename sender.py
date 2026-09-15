import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
# Telegram allows ~30 messages/second across chats; a small gap between
# broadcast sends keeps us far from the limit as subscribers grow.
BROADCAST_DELAY = 0.05


async def _deliver(chat_id: int, kind: str, post):
    """Run one Bot API call with the retry policy every send shares.

    Retries transient failures with backoff, honours 429 retry_after, and
    gives up immediately on permanent errors (400 bad request, 403 blocked).
    Returns the successful response, or None once the attempts run out.
    """
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = await post()
        except httpx.RequestError as e:
            logger.warning("Request failed: %s (attempt %d)", e, attempt)
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(2 ** attempt)  # 2s, 4s
            continue

        if resp.status_code == 200:
            return resp

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
            return None

        logger.warning("Bot API returned %d: %s (attempt %d)",
                       resp.status_code, resp.text, attempt)
        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(2 ** attempt)

    logger.error("Failed to send %s to chat %d after %d attempts", kind, chat_id, MAX_ATTEMPTS)
    return None


async def send_message(bot_token: str, chat_id: int, text: str) -> bool:
    """Send a text message via Telegram Bot API. True on success."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    async with httpx.AsyncClient(timeout=30) as client:
        return await _deliver(chat_id, "message", lambda: client.post(url, json=payload)) is not None


def _file_id(resp) -> str | None:
    """The id Telegram assigned to an uploaded photo, for reuse in later sends."""
    try:
        return resp.json()["result"]["photo"][-1]["file_id"]
    except (KeyError, IndexError, TypeError, ValueError):
        return None


async def send_photo(bot_token: str, chat_id: int, photo: bytes | str,
                     caption: str | None = None) -> str | None:
    """Send a photo via Telegram Bot API. Returns its file_id, or None on failure.

    `photo` is either PNG bytes to upload, or a file_id string Telegram already
    holds. Passing the id back on later sends means the same picture crosses the
    wire once per broadcast instead of once per subscriber.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    data = {"chat_id": chat_id}
    if caption:
        data["caption"] = caption
        data["parse_mode"] = "HTML"

    if isinstance(photo, str):
        data["photo"] = photo
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await _deliver(chat_id, "photo", lambda: client.post(url, data=data))
        return photo if resp is not None else None

    # Uploading bytes is slower than posting a form, hence the longer timeout
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await _deliver(
            chat_id, "photo",
            lambda: client.post(url, data=data,
                                files={"photo": ("schedule.png", photo, "image/png")}),
        )
    return _file_id(resp) if resp is not None else None


async def broadcast(bot_token: str, chat_ids: list[int], text: str) -> None:
    """Send a message to all subscribers."""
    for chat_id in chat_ids:
        success = await send_message(bot_token, chat_id, text)
        if not success:
            logger.error("Failed to send message to chat_id=%d", chat_id)
        await asyncio.sleep(BROADCAST_DELAY)
