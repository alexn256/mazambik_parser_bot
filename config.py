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

# Grid layout constants (calibrated from sample images)
# Bottom section starts at this fraction of total image height
BOTTOM_SECTION_START = 0.62
# Grid: 2 rows x 6 columns
GRID_ROWS = 2
GRID_COLS = 6
# Header portion of each box to skip (contains "Черга X.Y" label)
BOX_HEADER_RATIO = 0.30
# Margin inside each box (pixels after crop, will be scaled)
BOX_MARGIN_PX = 4

# Queue labels in grid order: row 1 then row 2, left to right
QUEUE_LABELS = [
    "1.1", "2.1", "3.1", "4.1", "5.1", "6.1",
    "1.2", "2.2", "3.2", "4.2", "5.2", "6.2",
]
