import os
import asyncio
import threading
import sqlite3
from flask import Flask, jsonify, render_template
from flask_cors import CORS
from config import settings
from database import init_db
from bot import router

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

app = Flask(__name__)
CORS(app)

@app.route('/api/lessons.json')
def api_lessons_json():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT b.title as book_title, b.author, b.slug as book_slug, 
               l.id, l.part, l.description, l.audio_url 
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
            "id": l["id"], "part": l["part"], "description": l["description"], "audio_url": l["audio_url"]
        })
    return jsonify(data)

@app.route('/api/book/<slug>.json')
def api_book_json(slug):
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT id, title, author FROM books WHERE slug = ?", (slug,))
    book = c.fetchone()
    if not book:
        conn.close()
        return jsonify({"error": "Book not found"}), 404

    c.execute("SELECT id, part, description, audio_url FROM lessons WHERE book_id = ? ORDER BY part", (book["id"],))
    lessons = [dict(r) for r in c.fetchall()]
    conn.close()

    return jsonify({"title": book["title"], "author": book["author"], "slug": slug, "lessons": lessons})

@app.route('/')
@app.route('/book/<slug>')
def index(slug=None):
    return render_template('index.html')

async def start_bot():
    LOCAL_API_URL = "http://telegram-api-server:8081"
    session = AiohttpSession(api=TelegramAPIServer.from_base(LOCAL_API_URL))
    bot = Bot(token=settings.bot_token, session=session)
    dp = Dispatcher()
    dp.include_router(router)
    print("Бот запущен...")
    await dp.start_polling(bot, handle_signals=False)

def run_bot_thread():
    asyncio.run(start_bot())

if __name__ == '__main__':
    init_db()
    threading.Thread(target=run_bot_thread, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
