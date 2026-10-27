import os
import asyncio
import threading
import sqlite3
from flask import Flask, jsonify
from flask_cors import CORS
from pyrogram import idle
from config import settings
from database import init_db
from bot import app as pyro_app

# ==========================================
# 1. НАСТРОЙКА FLASK (API ДЛЯ САЙТА)
# ==========================================
flask_app = Flask(__name__)
CORS(flask_app)

@flask_app.route('/api/lessons.json')
def api_lessons_json():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT b.title as book_title, b.author, b.slug as book_slug, 
               l.id, l.lesson_title, l.audio_url 
        FROM lessons l JOIN books b ON l.book_id = b.id ORDER BY b.title, l.id
    """)
    lessons = [dict(r) for r in c.fetchall()]
    conn.close()

    data = {"books": {}}
    for l in lessons:
        book = l["book_title"]
        if book not in data["books"]:
            data["books"][book] = {"author": l["author"], "slug": l["book_slug"], "lessons": []}
        data["books"][book]["lessons"].append({
            "id": l["id"], "title": l["lesson_title"], "audio_url": l["audio_url"]
        })
    return jsonify(data)

@flask_app.route('/')
def index():
    return "API is running successfully!"

# ==========================================
# 2. ФУНКЦИЯ ЗАПУСКА FLASK В ПОТОКЕ
# ==========================================
def run_flask():
    # Render автоматически передает порт в переменную PORT
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port, use_reloader=False)

# ==========================================
# 3. ГЛАВНАЯ АСИНХРОННАЯ ФУНКЦИЯ
# ==========================================
async def main():
    # 1. Запускаем Flask в фоновом потоке
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Flask сервер запущен в фоновом потоке.", flush=True)

    # 2. Запускаем Pyrogram-бота в главном потоке
    print("Запускаю Telegram-бота...", flush=True)
    await pyro_app.start()
    print("Бот успешно подключен к Telegram и слушает сообщения!", flush=True)

    # 3. Блокируем главный поток, чтобы бот работал бесконечно
    await idle()

    # 4. Корректная остановка (сработает, если Render выключит сервис)
    await pyro_app.stop()
    print("Бот остановлен.", flush=True)

# ==========================================
# 4. ТОЧКА ВХОДА
# ==========================================
if __name__ == "__main__":
    init_db() # Создаем БД перед запуском
    asyncio.run(main()) # Запускаем всё
