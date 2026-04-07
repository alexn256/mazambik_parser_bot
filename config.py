import os
from dotenv import load_dotenv

load_dotenv()

TELETHON_API_ID = int(os.environ["TELETHON_API_ID"])
TELETHON_API_HASH = os.environ["TELETHON_API_HASH"]
TELETHON_SESSION_STRING = os.environ["TELETHON_SESSION_STRING"]

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_USERNAME = os.environ["CHANNEL_USERNAME"]
USER_CHAT_ID = int(os.environ["USER_CHAT_ID"])

STATE_FILE_PATH = os.getenv("STATE_FILE_PATH", "./state.json")
SUBSCRIBERS_FILE_PATH = os.getenv("SUBSCRIBERS_FILE_PATH", "./subscribers.json")
HISTORY_FILE_PATH = os.getenv("HISTORY_FILE_PATH", "./history.json")

# Queue labels in grid order: row 1 then row 2, left to right
QUEUE_LABELS = [
    "1.1", "2.1", "3.1", "4.1", "5.1", "6.1",
    "1.2", "2.2", "3.2", "4.2", "5.2", "6.2",
]
