# Mazambik Parser Bot — Project Context

## What is this project?

Telegram-бот, который автоматически парсит графики отключений электроэнергии из публичного Telegram-канала. Канал публикует расписание в виде картинок — бот распознаёт их через OCR и отправляет текстовое представление пользователю в личные сообщения.

## Problem

Графики отключений публикуются только как картинки (не текст). Их неудобно читать с телефона, нельзя искать по очереди, нельзя отслеживать изменения. За день график может обновляться несколько раз — различия между версиями не видны без ручного сравнения.

## How the source images work

Картинки-расписания имеют фиксированную структуру (источник: МОСАМБІК МЕДІА):

- **Верхняя часть** — цветная сетка-график (не парсится)
- **Watermark в центре** — "МОСАМБІК МЕДІА HH:MM D.M.YYYY" (время создания графика)
- **Нижняя часть** (ниже "Годинні відключення електроенергії") — 12 цветных блоков в сетке 2x6:
  - Строка 1: Черга 1.1, 2.1, 3.1, 4.1, 5.1, 6.1
  - Строка 2: Черга 1.2, 2.2, 3.2, 4.2, 5.2, 6.2
  - Каждый блок содержит временные диапазоны отключений (например "10:00 - 11:30") или пуст (нет отключений)

Размер sample-картинок: 1303x965. Все 12 блоков — 209x186 px.

## Architecture

```
main.py           — точка входа, оркестрация pipeline
monitor.py        — Telethon userbot: слушает канал, ловит новые фото
parser.py         — OpenCV + Tesseract: картинка -> структурированные данные
state.py          — JSON-файл для хранения текущего суточного состояния
diff.py           — сравнение двух графиков, генерация списка изменений
formatter.py      — форматирование в текст с эмодзи для Telegram
sender.py         — отправка сообщений через Bot API (httpx)
config.py         — загрузка конфигурации из .env
generate_session.py — одноразовая утилита для генерации Telethon session string
```

### Data flow

```
Telegram Channel (photo) 
  -> monitor.py (Telethon, скачивает картинку)
  -> parser.py (OpenCV: вырезает 12 блоков, Tesseract: OCR каждого)
  -> state.py (загружает предыдущее состояние)
  -> diff.py (если не первый график за день — считает разницу)
  -> formatter.py (форматирует текст)
  -> sender.py (отправляет пользователю через Bot API)
  -> state.py (сохраняет новое состояние)
```

### Image parsing pipeline (parser.py)

1. **Box extraction**: OpenCV находит 12 цветных блоков через HSV-маску (saturation > 80) + contour detection. Fallback — фиксированная сетка (откалиброванная по sample-картинкам).
2. **OCR каждого блока**: обрезка заголовка (30% сверху) -> grayscale -> Otsu threshold -> upscale 3x -> Tesseract (`--psm 6 -l ukr`)
3. **Regex**: `(\d{1,2})[:\.](\d{2})\s*[-—~]+\s*(\d{1,2})[:\.](\d{2})` — извлекает временные диапазоны из OCR-текста
4. **Watermark**: OCR средней полосы картинки, несколько порогов яркости

### State management (state.py)

`state.json` хранит:
- `date` — дата графика (из watermark)
- `last_timestamp` — время последнего обновления
- `schedule` — расписание по всем 12 очередям
- `update_count` — порядковый номер обновления за день

Сброс при смене дня. Атомарная запись (tmp + rename).

### Diff algorithm (diff.py)

Сравнивает старое и новое расписание для каждой очереди:
- Полное исчезновение/появление отключений
- Удалённые/добавленные диапазоны
- Сокращение/расширение перекрывающихся диапазонов (через overlap detection)

## Tech stack

- **Python 3.12**
- **Telethon** — userbot для мониторинга публичного канала (StringSession)
- **OpenCV** (`opencv-python-headless`) — обработка изображений, извлечение блоков
- **Tesseract OCR** (`pytesseract`, `tesseract-ocr-ukr`) — распознавание текста
- **httpx** — отправка сообщений через Bot API
- **python-dotenv** — конфигурация

## Configuration (.env)

```
TELETHON_API_ID=         # from my.telegram.org
TELETHON_API_HASH=       # from my.telegram.org
TELETHON_SESSION_STRING= # generated via generate_session.py
BOT_TOKEN=               # from @BotFather
CHANNEL_USERNAME=        # channel to monitor (without @)
USER_CHAT_ID=            # your numeric Telegram user ID
STATE_FILE_PATH=         # optional, default: ./state.json
```

## Development commands

```bash
# Install dependencies (local)
pip install -r requirements.txt

# System dependency
sudo apt-get install tesseract-ocr tesseract-ocr-ukr

# Test parser on a sample image
python parser.py photo_2026-04-03_10-46-24.jpg

# Generate Telethon session (interactive, one-time)
python generate_session.py

# Run the bot
python main.py

# Docker
docker build -t mazambik-bot .
docker run --env-file .env mazambik-bot
```

## Current status

- [x] Project structure and all modules created
- [x] Image parsing: box extraction tested and working (12/12 boxes detected correctly)
- [ ] OCR testing (requires tesseract installation)
- [ ] End-to-end test with sample images
- [ ] Telegram integration testing
- [ ] Deployment

## Design decisions

- **Contour detection over hardcoded grid**: основной метод извлечения блоков — через HSV + contours. Более устойчив к небольшим изменениям в шаблоне. Хардкод-сетка как fallback.
- **httpx instead of python-telegram-bot**: для отправки сообщений не нужен полный фреймворк бота — достаточно HTTP-запроса.
- **StringSession for Telethon**: позволяет развернуть на сервере без интерактивной авторизации.
- **No database**: JSON-файл достаточен для суточного состояния одного графика.
