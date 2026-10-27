import os
import asyncio
import threading
import sqlite3
from flask import Flask, jsonify
from flask_cors import CORS
from config import settings
from database import init_db
from bot import app as pyro_app

app = Flask(__name__)
CORS(app)

@app.route('/api/lessons.json')
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

@app.route('/')
def index():
    return "API is running successfully!"

def run_bot_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        print("Запускаю бота в фоне...", flush=True)
        
        # Запускаем клиента (безопасно для потоков, без перехвата сигналов)
        loop.run_until_complete(pyro_app.start())
        print("Бот успешно подключен к Telegram и слушает сообщения!", flush=True)
        
        # Держим цикл активным, чтобы бот не отключился
        loop.run_forever()
        
    except Exception as e:
        print(f"КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ БОТА: {e}", flush=True)

if __name__ == '__main__':
    init_db()
    
    # Запускаем бота в отдельном потоке
    threading.Thread(target=run_bot_thread, daemon=True).start()
    
    # Запускаем Flask
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
