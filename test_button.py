import asyncio
from config import USER_CHAT_ID
from main import send_current_schedule, send_tomorrow_schedule

async def run():
    print("--- Поточний графік ---")
    await send_current_schedule(USER_CHAT_ID)

    print("--- Графік на завтра ---")
    await send_tomorrow_schedule(USER_CHAT_ID)

asyncio.run(run())
