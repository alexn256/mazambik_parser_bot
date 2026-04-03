# Mazambik Parser Bot

Telegram-бот для автоматического распознавания графиков отключений электроэнергии.

Публичный Telegram-канал публикует расписание отключений в виде картинок. Бот мониторит канал, распознаёт картинку через OCR и отправляет текстовое представление графика в личные сообщения.

## Возможности

- **Парсинг картинки** — распознаёт 12 очередей (6 x 2 подочереди) с временными диапазонами отключений
- **Отслеживание изменений** — если за день публикуется несколько графиков, показывает что изменилось (diff)
- **Автоматический мониторинг** — слушает канал в реальном времени, реагирует на новые картинки

### Пример вывода

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

## Стек

- Python 3.12
- OpenCV + Tesseract OCR (парсинг картинок)
- Telethon (мониторинг канала)
- Telegram Bot API через httpx (отправка сообщений)

## Быстрый старт

### 1. Системные зависимости

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-ukr
```

### 2. Python зависимости

```bash
pip install -r requirements.txt
```

### 3. Конфигурация

Скопируй `.env.example` в `.env` и заполни:

```bash
cp .env.example .env
```

| Переменная | Откуда взять |
|---|---|
| `TELETHON_API_ID` | https://my.telegram.org → API development tools |
| `TELETHON_API_HASH` | Там же |
| `TELETHON_SESSION_STRING` | Запусти `python generate_session.py` |
| `BOT_TOKEN` | @BotFather в Telegram |
| `CHANNEL_USERNAME` | Username канала без `@` |
| `USER_CHAT_ID` | Твой числовой ID (можно узнать через @userinfobot) |

### 4. Генерация session string (одноразово)

```bash
python generate_session.py
```

Скрипт запросит номер телефона и код подтверждения. Полученную строку вставь в `TELETHON_SESSION_STRING` в `.env`.

### 5. Запуск

```bash
python main.py
```

### Docker

```bash
docker build -t mazambik-bot .
docker run --env-file .env mazambik-bot
```

## Тестирование парсера

Можно протестировать парсер отдельно на картинке:

```bash
python parser.py photo_2026-04-03_10-46-24.jpg
```

## Структура проекта

```
config.py           — конфигурация из .env
main.py             — точка входа
monitor.py          — мониторинг Telegram-канала (Telethon)
parser.py           — парсинг картинки (OpenCV + Tesseract)
state.py            — управление состоянием (JSON)
diff.py             — вычисление разницы между графиками
formatter.py        — форматирование сообщений
sender.py           — отправка через Bot API
generate_session.py — генерация Telethon session string
```

## Деплой

Рекомендуемые варианты:

- **Railway** — $5/мес free credit, достаточно для этого бота
- **Oracle Cloud** — always-free ARM instance
- **VPS** — любой дешёвый VPS с Docker
