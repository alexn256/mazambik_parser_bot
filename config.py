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

# Queue labels in grid order (row by row, left to right)
QUEUE_LABELS_ROW1 = ["1.1", "2.1", "3.1", "4.1", "5.1", "6.1"]
QUEUE_LABELS_ROW2 = ["1.2", "2.2", "3.2", "4.2", "5.2", "6.2"]
QUEUE_LABELS = QUEUE_LABELS_ROW1 + QUEUE_LABELS_ROW2

# Emoji colors per queue group
QUEUE_EMOJI = {
    "1": "\U0001f7e1",  # 🟡
    "2": "\U0001f7e2",  # 🟢
    "3": "\U0001f7e0",  # 🟠
    "4": "\U0001f535",  # 🔵
    "5": "\U0001f338",  # 🌸
    "6": "\U0001f7e3",  # 🟣
}
