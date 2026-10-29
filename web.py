import os
import asyncio
import threading
import sqlite3
from flask import Flask, jsonify
from flask_cors import CORS
from config import settings
from database import init_db
from bot import client # Импортируем клиента Telethon

flask_app = Flask(__name__)
CORS(flask_app, resources={r"/*": {"origins": "*"}})

@flask_app.route('/api/lessons.json')
def api_lessons_json():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT b.title as book_title, b.author, b.slug as book_slug, 
               l.id, l.lesson_title, l.part, l.audio_url 
        FROM lessons l JOIN books b ON l.book_id = b.id ORDER BY b.title, l.part
    """)
    lessons = [dict(r) for r in c.fetchall()]
    conn.close()

    data = {"books": {}}
    for l in lessons:
        book = l["book_title"]
        if book not in data["books"]:
            data["books"][book] = {"author": l["author"], "slug": l["book_slug"], "lessons": []}
        data["books"][book]["lessons"].append({
            "id": l["id"], "part": l["part"], "title": l["lesson_title"], "audio_url": l["audio_url"]
        })
    return jsonify(data)

@flask_app.route('/')
def index():
    return "API is running successfully!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    flask_app.run(host='0.0.0.0', port=port, use_reloader=False)

async def main():
    # 1. Flask в фоне
    threading.Thread(target=run_flask, daemon=True).start()
    print("Flask сервер запущен.", flush=True)

    # 2. Telethon в главном потоке
    print("Запускаю Telegram-бота...", flush=True)
    await client.start(bot_token=settings.bot_token)
    print("Бот успешно подключен к Telegram!", flush=True)
    
    await client.run_until_disconnected()

if __name__ == "__main__":
    init_db()
    asyncio.run(main())
