# Mazambik Parser Bot

A Telegram bot that automatically parses power outage schedules from a public Telegram channel. The channel publishes schedules as images — the bot recognizes them via OCR and sends a text representation to you in a private message.

## Features

- **Image parsing** — recognizes 12 queues (6 × 2 sub-queues) with outage time ranges
- **Change tracking** — if multiple schedules are published during the day, shows what changed (diff)
- **Automatic monitoring** — listens to the channel in real time, reacts to new images

### Example output

```
⚡ Графік відключень на 03.04.2026 (станом на 10:43)

🟡 1 черга
1.1 → 10:00 – 11:30, 16:00 – 18:00
1.2 → 10:30 – 12:00, 16:30 – 18:00
🟢 2 черга
2.1 → 11:00 – 12:30
2.2 → 11:30 – 13:00
🟠 3 черга
3.1 → 12:00 – 13:30
3.2 → 12:00 – 14:00
🔵 4 черга
4.1 → 13:00 – 14:30
4.2 → немає відключень
🌸 5 черга
5.1 → 08:00 – 09:30
5.2 → 08:30 – 10:00
🟣 6 черга
6.1 → 09:00 – 10:30
6.2 → 09:30 – 11:00

📋 Зміни:
❌ Черга 1.1: прибрали 16:00–18:00
⏱ Черга 2.2: скоротили (було 11:30–13:00 → стало 11:30–12:30)
```

## Tech stack

- Python 3.12
- OpenCV + Tesseract OCR (image parsing)
- Telethon (channel monitoring)
- Telegram Bot API via httpx (sending messages)

## Quick start

### 1. System dependencies

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-ukr
```

### 2. Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configuration

Copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

| Variable | Where to get it |
|---|---|
| `TELETHON_API_ID` | https://my.telegram.org → API development tools |
| `TELETHON_API_HASH` | Same page |
| `TELETHON_SESSION_STRING` | Run `python generate_session.py` |
| `BOT_TOKEN` | @BotFather in Telegram |
| `CHANNEL_USERNAME` | Channel username without `@` |
| `USER_CHAT_ID` | Your numeric Telegram ID (get it via @userinfobot) |

### 4. Generate session string (one-time)

```bash
python generate_session.py
```

The script will ask for your phone number and confirmation code. Paste the resulting string into `TELETHON_SESSION_STRING` in `.env`.

### 5. Run

```bash
python main.py
```

### Docker

```bash
docker build -t mazambik-bot .
docker run -d --restart=unless-stopped --env-file .env --name mazambik-bot mazambik-bot
```

The `--restart=unless-stopped` flag ensures the bot automatically restarts after server reboots.

**Useful commands:**

```bash
# View logs
docker logs -f mazambik-bot

# Stop the bot
docker stop mazambik-bot

# Start after manual stop
docker start mazambik-bot

# Rebuild and redeploy after code changes
docker stop mazambik-bot && docker rm mazambik-bot
docker build -t mazambik-bot . && docker run -d --restart=unless-stopped --env-file .env --name mazambik-bot mazambik-bot
```

## Testing the parser

You can test the parser standalone on a sample image:

```bash
python parser.py photo_2026-04-03_10-46-24.jpg
```

## Project structure

```
config.py           — configuration from .env
main.py             — entry point
monitor.py          — Telegram channel monitoring (Telethon)
parser.py           — image parsing (OpenCV + Tesseract)
state.py            — state management (JSON)
diff.py             — schedule diff computation
formatter.py        — message formatting
sender.py           — sending via Bot API
generate_session.py — Telethon session string generator
```

## Deployment

Recommended options:

- **Railway** — $5/mo free credit, sufficient for this bot
- **Oracle Cloud** — always-free ARM instance
- **VPS** — any cheap VPS with Docker
