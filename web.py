import os
import asyncio
import threading
import sqlite3
from flask import Flask, jsonify
from flask_cors import CORS
from config import settings
from database import init_db
from bot import router

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

app = Flask(__name__)
CORS(app) # Разрешаем запросы с GitHub Pages

# ==========================================
# API ДЛЯ ФРОНТЕНДА (на GitHub Pages)
# ==========================================
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

# ==========================================
# ЗАГЛУШКА ДЛЯ ГЛАВНОЙ СТРАНИЦЫ RENDER
# ==========================================
@app.route('/')
@app.route('/book/<slug>')
def index(slug=None):
    return """
    <html>
        <head>
            <title>API Бэкенд</title>
            <style>
                body { background: #12121a; color: #e8e8f0; font-family: sans-serif; padding: 40px; text-align: center; }
                h1 { color: #7c3aed; }
                a { color: #f59e0b; text-decoration: none; }
                a:hover { text-decoration: underline; }
                .box { background: #1a1a26; padding: 20px; border-radius: 10px; display: inline-block; margin-top: 20px; text-align: left; }
            </style>
        </head>
        <body>
            <h1>⚙️ API Бэкенд успешно работает</h1>
            <p>Этот сервис нужен только для отдачи JSON-данных и запуска Telegram-бота.</p>
            <div class="box">
                <p>📡 <b>Доступные эндпоинты:</b></p>
                <ul>
                    <li><a href="/api/lessons.json" target="_blank">/api/lessons.json</a> (Все данные)</li>
                    <li>/api/book/название-книги.json (Конкретная книга)</li>
                </ul>
            </div>
        </body>
    </html>
    """

# ==========================================
# ЗАПУСК БОТА
# ==========================================
async def start_bot():
    # Ссылка на твой локальный Bot API Server на Render
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
    # Запускаем бота в фоновом потоке
    threading.Thread(target=run_bot_thread, daemon=True).start()
    
    # Запускаем Flask-сервер
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
