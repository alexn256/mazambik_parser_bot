"""One-time script to generate a Telethon session string.

Run locally:
    python generate_session.py

It will ask for your phone number and auth code.
Copy the resulting session string to TELETHON_SESSION_STRING in .env.
"""
import os
from dotenv import load_dotenv
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

load_dotenv()

api_id = int(os.environ["TELETHON_API_ID"])
api_hash = os.environ["TELETHON_API_HASH"]

with TelegramClient(StringSession(), api_id, api_hash) as client:
    session_string = client.session.save()
    print("\n=== Your session string (copy to .env) ===")
    print(session_string)
    print("===========================================\n")
