<div align="center">
  <img src="assets/logo.png" alt="Svitlo Kremen Bot" width="120"/>

# Svitlo Kremen Bot

A Telegram bot for tracking power outage schedules in Kremenchuk.
Reads schedules straight from [poe.pl.ua](https://www.poe.pl.ua/) — the provider that publishes them — and sends a clean text schedule to subscribers.

**[@svitlo_kremen_bot](https://t.me/svitlo_kremen_bot)**

</div>

## Features

### Straight from the provider
The bot polls the same endpoint the provider's own page calls to draw its grid, every 5 minutes, and broadcasts a schedule as soon as it is published. Nobody has to repost a screenshot first.

"Станом на …" is the provider's own last-updated stamp, so it is the moment the schedule actually appeared or changed. That matters most for schedules published the evening before — a stamp of `09.04.2026 20:11` on a schedule for the 10th is something a reposter's message time could never tell you.

Changes are detected by comparing the grid itself, not the stamp. A restamped but identical schedule wakes nobody; changed cells notify even if the stamp stands still.

A Telegram channel stays wired up as a fallback: if the site is unreachable but a schedule screenshot is posted somewhere, the bot still recognizes it via OCR. Whichever source arrives first wins; the other produces no diff and is dropped, so nothing is announced twice. Either source can be turned off — see [Configuration](#configuration).

### The provider's table, as a picture
Every schedule message is preceded by the grid subscribers are used to seeing on the site — all six queues, half-hour cells, the "обсяг черг" preamble and the provider's timestamp.

It is drawn from the parsed schedule with Pillow, not captured from the page: no headless browser in the image, and it works for schedules recognised from a screenshot too. If it cannot be drawn — missing fonts, anything unexpected — the text message still goes out.

### Personal notifications by queue
Each subscriber selects their sub-queue (e.g. `3.2`). The bot sends only the information relevant to that queue — with a progress bar and total hours without power.

### Change tracking
When a published schedule is amended, the bot shows the current picture *and* what moved, so a subscriber who opens the chat hours later does not have to reconstruct anything:

```
🔄 Оновлення графіку на 10.04.2026 (станом на 08:30)

🟡 1 черга
  1.2 · 00:30–03:00, 07:30–10:00, 13:30–16:00, 19:30–20:30

🟥🟥🟥🟥🟩🟩🟩🟩🟩🟩🟩🟩

🕯️ 8.5 год без світла
💡 15.5 год зі світлом

📋 Зміни:
⏰ Черга 1.2: розширили (було 13:30–15:30 → стало 13:30–16:00)

⏱ Вимикають упродовж 30 хв після початку,
   вмикають в останні 30 хв інтервалу.
```

An amendment only reaches subscribers whose own queue moved. A first publication and a cancellation reach everyone:

```
✅ Графік на 11.04.2026 скасовано — відключень не прогнозується.
За даними Полтаваобленерго станом на 10.04.2026 23:15.
```

### Quiet days are an answer, not a silence
The provider publishes today and, from the evening, tomorrow. When it announces that no outages are expected, that is a fact worth stating plainly — and different from having heard nothing at all:

```
🟢 На завтра (11.04.2026) відключень не прогнозується.
За даними Полтаваобленерго станом на 10.04.2026 20:30.
```

Quiet days are recorded but not broadcast — otherwise every calm day would wake every subscriber.

### What's the status right now?
The bot answers in real time (with a themed picture): is there power or not, how long until the next outage or restoration.

```
💡 Зараз є світло · черга 3.2
до 14:30 (ще 1 год 20 хв)
Далі: відключення 14:30 – 16:00
```

During an outage it also says when power may come back early, because the last half hour of any range is a switching window:

```
🔴 Зараз відключення · черга 1.2
до 15:00 (ще 1 год 20 хв)
💡 Світло може з'явитись раніше — з 14:30
Далі: світло з 15:00 до 19:30
```

### Find your queue by address
Don't know your queue? The bot resolves it from your address: pick a city or type your village, then type your street — search is typo-tolerant. If a street is split between queues, the bot shows each queue's house numbers so you pick your own. One more tap subscribes you to notifications for that queue.

```
🔍 Яка у мене черга?
📍 м. Кременчук  →  вул. Лесі Українки
🟤 Ваша черга: 5.2
[✅ Отримувати сповіщення для 5.2]
```

Covers the whole Kremenchuk branch: Kremenchuk, Horishni Plavni, Kobeliaky, Hlobyne and ~200 villages (many resolve instantly — the entire settlement is in one queue).

### Statistics
View outage hours for the last 7 or 30 days for your queue.

```
📊 Статистика за 7 днів — черга 3.2

10.04.2026  🟥🟥🟥🟩🟩🟩🟩🟩🟩🟩🟩🟩  6.0 год
09.04.2026  🟥🟥🟩🟩🟩🟩🟩🟩🟩🟩🟩🟩  4.0 год

Середнє: 5.0 год/день
```

## Schedule format

Full schedule (no queue filter):

```
⚡ Графік відключень на 10.04.2026 (станом на 09.04.2026 20:11)

🟡 1 черга
  1.1 · 00:00–02:30, 07:00–09:30, 13:00–15:00, 19:00–20:30
  1.2 · 00:30–03:00, 07:30–10:00, 13:30–15:30, 19:30–20:30
🟢 2 черга
  2.1 · 02:00–03:30, 08:00–10:30, 14:00–16:00, 20:00–21:00
  2.2 · 02:30–04:00, 08:30–10:30, 14:30–16:30, 20:30–21:30
...
🟣 6 черга
  6.1 · 00:00–00:30, 06:00–08:30, 12:00–14:00, 18:00–19:30
  6.2 · 00:00–01:00, 06:30–09:00, 12:30–14:30, 18:30–20:00

⏱ Вимикають упродовж 30 хв після початку,
   вмикають в останні 30 хв інтервалу.
```

Personal schedule (queue selected) adds a day summary:

```
🟡 1 черга
  1.2 · 00:30–03:00, 07:30–10:00, 13:30–15:30, 19:30–20:30

🟥🟥🟥🟥🟩🟩🟩🟩🟩🟩🟩🟩

🕯️ 8.0 год без світла
💡 16.0 год зі світлом
```

### Why the ranges have soft edges

The provider's grid is half-hour cells in three colours: on, off, and a switching window it labels *"Час, необхідний для перемикань. Електроенергії може не бути"*. Its own rules say the cut happens within 30 minutes of the queue's start time and power returns during the last 30 minutes of the block.

So a published range spans its switching halves, and both edges are soft by the same half hour. The bot merges the switching cells into the range — producing the same times the provider publishes in text — and states the caveat once at the foot of a message rather than beside each of the ~48 ranges a full schedule prints.

## Configuration

Copy `.env.example` to `.env`. Beyond the Telegram credentials:

| Variable | Default | Meaning |
| --- | --- | --- |
| `POE_SOURCE_ENABLED` | `1` | Poll poe.pl.ua — the primary source |
| `POE_POLL_INTERVAL` | `300` | Seconds between polls |
| `TELEGRAM_SOURCE_ENABLED` | `1` | Keep the screenshot-OCR fallback running |

## Tech stack

- Python 3.12
- httpx (poe.pl.ua polling — the primary source)
- Pillow + fonts-liberation (draws the schedule picture)
- OpenCV + Tesseract OCR (screenshot fallback)
- Telethon (channel monitoring for the fallback)
- Telegram Bot API
