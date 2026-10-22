import asyncio
import io
import json
import re
import sqlite3  # <-- ИСПРАВЛЕНО: добавлен импорт
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime

import requests
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode

from config import settings

# ============================================================
# 1. УТИЛИТЫ: ПАРСИНГ ШАБЛОНА
# ============================================================

EMOJI_DIGIT_MAP = {
    '\u0030\ufe0f\u20e3': '0', '\u0031\ufe0f\u20e3': '1',
    '\u0032\ufe0f\u20e3': '2', '\u0033\ufe0f\u20e3': '3',
    '\u0034\ufe0f\u20e3': '4', '\u0035\ufe0f\u20e3': '5',
    '\u0036\ufe0f\u20e3': '6', '\u0037\ufe0f\u20e3': '7',
    '\u0038\ufe0f\u20e3': '8', '\u0039\ufe0f\u20e3': '9',
}

def emoji_digits_to_int(text: str) -> str:
    for emoji, digit in EMOJI_DIGIT_MAP.items():
        text = text.replace(emoji, digit)
    return text

def parse_template(caption: str) -> dict:
    result = {"book_title": "", "author": "", "lesson_number": 0, "lesson_title": "", "sections": []}

    m = re.search(r"📕\s*Книга\s*[«\"](.+?)[»\"]", caption)
    if m: result["book_title"] = m.group(1).strip()

    m = re.search(r"📘\s*Автор:\s*(.+)", caption)
    if m: result["author"] = m.group(1).strip()

    m = re.search(r"📌\s*Урок\s*(.+)", caption)
    if m:
        raw_lesson = m.group(1).strip()
        converted = emoji_digits_to_int(raw_lesson)
        digits = re.sub(r"\D", "", converted)
        if digits: result["lesson_number"] = int(digits)

    m = re.search(r"📖\s*(.+?)(?=\n|$)", caption)
    if m: result["lesson_title"] = m.group(1).strip().rstrip(":").strip()

    section_lines = re.findall(r"^-\s*(.+)$", caption, re.MULTILINE)
    result["sections"] = [s.strip() for s in section_lines]

    return result

# ============================================================
# 2. РАБОТА С БД
# ============================================================

def init_db():
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            book_title TEXT,
            author TEXT,
            lesson_number INTEGER,
            lesson_title TEXT,
            sections TEXT,
            audio_url TEXT,
            uploaded_by INTEGER,
            uploaded_by_name TEXT,
            created_at TEXT,
            raw_caption TEXT
        )
    """)
    conn.commit()
    conn.close()

def save_lesson(data: dict):
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("""
        INSERT INTO lessons
        (book_title, author, lesson_number, lesson_title, sections, audio_url, uploaded_by, uploaded_by_name, created_at, raw_caption)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["book_title"], data["author"], data["lesson_number"],
        data["lesson_title"], json.dumps(data["sections"], ensure_ascii=False),
        data["audio_url"], data.get("uploaded_by"), data.get("uploaded_by_name"),
        datetime.utcnow().isoformat(), data["raw_caption"]
    ))
    conn.commit()
    conn.close()

# ============================================================
# 3. GITHUB: ЗАГРУЗКА ФАЙЛА
# ============================================================

def github_upload_audio(file_bytes: io.BytesIO, filename: str) -> str:
    headers = {
        "Authorization": f"token {settings.github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    tag = filename.rsplit(".", 1)[0]
    payload = {
        "tag_name": tag, "name": f"Урок {tag}",
        "body": "Автоматическая загрузка из Telegram-бота",
        "draft": False, "prerelease": False,
    }
    resp = requests.post(f"https://api.github.com/repos/{settings.github_repo}/releases", json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    upload_url = resp.json()["upload_url"].split("{")[0]

    file_bytes.seek(0)
    upload_headers = headers.copy()
    upload_headers["Content-Type"] = "application/octet-stream"
    upload_resp = requests.post(f"{upload_url}?name={filename}", headers=upload_headers, data=file_bytes.getvalue(), timeout=120)
    upload_resp.raise_for_status()
    
    return upload_resp.json()["browser_download_url"]

# ============================================================
# 4. ХЕНДЛЕР БОТА
# ============================================================

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Отправь мне **голосовое** или **аудио** с подписью по шаблону:\n\n"
        "📕 Книга «Название»\n"
        "📘 Автор: Имя\n"
        "📌 Урок 2️⃣0️⃣\n"
        "📖 Раздел :\n"
        "Разделы:\n"
        "- Пункт 1\n"
        "- Пункт 2",
        parse_mode=ParseMode.MARKDOWN,
    )

@router.message(F.voice | F.audio)
async def handle_audio(message: Message):
    if settings.admin_ids and message.from_user.id not in settings.admin_ids:
        await message.answer("⛔ У тебя нет прав на загрузку.")
        return

    caption = message.caption
    if not caption:
        await message.answer("❌ Нужна подпись по шаблону.")
        return

    try:
        parsed = parse_template(caption)
    except Exception as e:
        await message.answer(f"❌ Ошибка парсинга: {e}")
        return

    if not parsed["book_title"]:
        await message.answer("❌ Не удалось найти название книги (📕 Книга «...»).")
        return
    if not parsed["lesson_number"]:
        await message.answer("❌ Не удалось определить номер урока (📌 Урок ...).")
        return

    lesson_num = parsed["lesson_number"]
    ext = "ogg" if message.voice else "mp3"
    filename = f"lesson_{lesson_num:03d}.{ext}"

    await message.answer("⏳ Скачиваю аудио и загружаю на GitHub, подожди...")

    try:
        file_id = message.voice.file_id if message.voice else message.audio.file_id
        file_obj = await message.bot.download(file_id)
    except Exception as e:
        await message.answer(f"❌ Ошибка скачивания из Telegram: {e}")
        return

    try:
        audio_url = github_upload_audio(file_obj, filename)
    except Exception as e:
        await message.answer(f"❌ Ошибка загрузки в GitHub: {e}")
        return

    parsed["audio_url"] = audio_url
    parsed["raw_caption"] = caption
    parsed["uploaded_by"] = message.from_user.id
    parsed["uploaded_by_name"] = message.from_user.full_name

    try:
        save_lesson(parsed)
    except Exception as e:
        await message.answer(f"⚠️ Файл загружен, но ошибка БД: {e}\nСсылка: {audio_url}")
        return

    await message.answer(
        f"✅ **Урок {lesson_num} загружен!**\n\n"
        f"📕 {parsed['book_title']}\n"
        f"📘 {parsed['author']}\n"
        f"📌 Урок: {lesson_num}\n"
        f"🔗 [Аудио]({audio_url})",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )

# ============================================================
# 5. ФЕЙКОВЫЙ ВЕБ-СЕРВЕР ДЛЯ RENDER
# ============================================================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

def run_health_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    server.serve_forever()

# ============================================================
# 6. ЗАПУСК
# ============================================================
async def main():
    init_db()
    
    # Запускаем фейковый сервер для Render в фоне
    threading.Thread(target=run_health_server, daemon=True).start()
    
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
