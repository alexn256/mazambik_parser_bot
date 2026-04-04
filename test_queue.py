import asyncio
from config import USER_CHAT_ID, SUBSCRIBERS_FILE_PATH
from main import send_current_schedule
from subscribers import load_subscribers, set_subscriber_queue

async def run():
    # Show full schedule (no filter)
    print("--- Повний графік ---")
    await send_current_schedule(USER_CHAT_ID)
    await asyncio.sleep(1)

    # Set queue filter to 3.2
    set_subscriber_queue(USER_CHAT_ID, "3.2", SUBSCRIBERS_FILE_PATH)
    print("--- Графік для черги 3.2 ---")
    await send_current_schedule(USER_CHAT_ID)
    await asyncio.sleep(1)

    # Reset back to all queues
    set_subscriber_queue(USER_CHAT_ID, None, SUBSCRIBERS_FILE_PATH)
    print("Черга скинута на 'всі'")

asyncio.run(run())
