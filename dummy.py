"""Test script: processes a sample image and sends the result to Telegram.

Usage:
    python dummy.py                          # first schedule (clears state)
    python dummy.py --second                 # second schedule (shows diff)
    python dummy.py --reset                  # clear state only
"""
import asyncio
import logging
import sys
import os

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from config import BOT_TOKEN, STATE_FILE_PATH, USER_CHAT_ID
from main import process_image
from state import load_state

IMAGES = {
    "first": "photo_2026-04-03_10-46-24.jpg",
    "second": "photo_2026-04-03_11-50-24.jpg",
}


async def run():
    args = sys.argv[1:]

    if "--reset" in args:
        if os.path.exists(STATE_FILE_PATH):
            os.remove(STATE_FILE_PATH)
            print("State cleared.")
        else:
            print("No state file found.")
        return

    image_key = "second" if "--second" in args else "first"
    image_path = IMAGES[image_key]

    if not os.path.exists(image_path):
        print(f"Image not found: {image_path}")
        sys.exit(1)

    state = load_state(STATE_FILE_PATH)
    print(f"Current state: {state}")
    print(f"Processing: {image_path}")

    await process_image(image_path)
    print("Done. Check Telegram.")


if __name__ == "__main__":
    asyncio.run(run())
