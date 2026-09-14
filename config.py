import os
from dotenv import load_dotenv

load_dotenv()

TELETHON_API_ID = int(os.environ["TELETHON_API_ID"])
TELETHON_API_HASH = os.environ["TELETHON_API_HASH"]
TELETHON_SESSION_STRING = os.environ["TELETHON_SESSION_STRING"]

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHANNEL_USERNAME = os.environ["CHANNEL_USERNAME"]
USER_CHAT_ID = int(os.environ["USER_CHAT_ID"])

def _flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


# poe.pl.ua is the primary source: the provider's own page, with the provider's
# own "last updated" stamp. The Telegram channel stays on as a fallback for the
# case where the site is unreachable but someone reposts a screenshot.
POE_SOURCE_ENABLED = _flag("POE_SOURCE_ENABLED")
POE_POLL_INTERVAL = int(os.getenv("POE_POLL_INTERVAL", "300"))
TELEGRAM_SOURCE_ENABLED = _flag("TELEGRAM_SOURCE_ENABLED")

STATE_FILE_PATH = os.getenv("STATE_FILE_PATH", "./state.json")
SUBSCRIBERS_FILE_PATH = os.getenv("SUBSCRIBERS_FILE_PATH", "./subscribers.json")
HISTORY_FILE_PATH = os.getenv("HISTORY_FILE_PATH", "./history.json")
QUEUES_FILE_PATH = os.getenv("QUEUES_FILE_PATH", "./krem_queues.json")

# Queue labels in grid order: row 1 then row 2, left to right
QUEUE_LABELS = [
    "1.1", "2.1", "3.1", "4.1", "5.1", "6.1",
    "1.2", "2.2", "3.2", "4.2", "5.2", "6.2",
]
