import os
import asyncio
import threading
import sqlite3
from flask import Flask, jsonify
from flask_cors import CORS
from config import settings
from database import init_db
from bot import start_bot

# ==========================================
# 1. НАСТРОЙКА FLASK
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
# 2. ЗАПУСК FLASK В ФОНОВОМ ПОТОКЕ
# ==========================================
def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port, use_reloader=False)

# ==========================================
# 3. ГЛАВНАЯ АСИНХРОННАЯ ФУНКЦИЯ
# ==========================================
async def main():
    # 1. Запускаем Flask в фоне
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Flask сервер запущен в фоновом потоке.", flush=True)

    # 2. Запускаем Telethon-бота в главном потоке
    print("Запускаю Telegram-бота (Telethon)...", flush=True)
    await start_bot() # Эта функция сама заблокирует поток до отключения

# ==========================================
# 4. ТОЧКА ВХОДА
# ==========================================
if __name__ == "__main__":
    init_db()
    asyncio.run(main())
