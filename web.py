import io
import os
import asyncio
import threading
import requests
import sqlite3
import json
from flask import Flask, request, render_template_string
from config import settings
from database import init_db, update_audio_url
from bot import router

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

app = Flask(__name__)

# --- Логика GitHub ---
def github_upload_audio(file_bytes: io.BytesIO, filename: str) -> str:
    headers = {"Authorization": f"token {settings.github_token}", "Accept": "application/vnd.github.v3+json"}
    tag = filename.rsplit(".", 1)[0].replace(" ", "_")
    payload = {"tag_name": tag, "name": f"Файл {tag}", "body": "Загружено", "draft": False, "prerelease": False}
    resp = requests.post(f"https://api.github.com/repos/{settings.github_repo}/releases", json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    upload_url = resp.json()["upload_url"].split("{")[0]
    upload_headers = headers.copy()
    upload_headers["Content-Type"] = "application/octet-stream"
    file_bytes.seek(0)
    upload_resp = requests.post(f"{upload_url}?name={filename}", headers=upload_headers, data=file_bytes.getvalue(), timeout=120)
    upload_resp.raise_for_status()
    return upload_resp.json()["browser_download_url"]

def send_telegram_notification(chat_id, text):
    if not chat_id: return
    url = f"https://api.telegram.org/bot{settings.bot_token}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}, timeout=10)

# --- HTML: САЙТ ДЛЯ КЛИЕНТОВ ---
CLIENT_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Библиотека уроков</title>
    <style>
        body { font-family: system-ui, sans-serif; background: #12121a; color: #e8e8f0; margin: 0; padding: 20px; }
        h1 { text-align: center; color: #7c3aed; }
        .container { max-width: 800px; margin: 0 auto; }
        .book-title { font-size: 24px; color: #f59e0b; margin-top: 30px; border-bottom: 1px solid #2a2a3d; padding-bottom: 10px; }
        .lesson { background: #1a1a26; border-radius: 10px; padding: 15px; margin: 15px 0; }
        .lesson h3 { margin: 0 0 10px 0; color: #7c3aed; }
        .sections { font-size: 14px; color: #8a8aa0; margin-bottom: 15px; }
        .audio-wrap { display: flex; flex-direction: column; gap: 10px; }
        audio { width: 100%; border-radius: 5px; }
        .video-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; margin-top: 20px; }
        .video-card { background: #1a1a26; padding: 15px; border-radius: 10px; }
        .video-card iframe { width: 100%; aspect-ratio: 16/9; border: none; border-radius: 8px; }
        .video-card h4 { margin: 10px 0 0 0; font-size: 16px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📚 Библиотека</h1>

        {% if not lessons and not videos %}
            <p style="text-align:center; opacity:0.5;">Пока пусто. Добавь контент через Telegram-бота.</p>
        {% endif %}

        {% for book, items in lessons.items() %}
            <div class="book-title">📕 {{ book }} <span style="font-size:14px; color:#8a8aa0;">({{ items[0].author }})</span></div>
            {% for l in items %}
                <div class="lesson">
                    <h3>Урок {{ l.lesson_number }}: {{ l.lesson_title }}</h3>
                    <div class="sections">
                        {% for s in l.sections_list %}
                            • {{ s }}<br>
                        {% endfor %}
                    </div>
                    {% if l.audio_url %}
                        <audio controls src="{{ l.audio_url }}"></audio>
                    {% else %}
                        <div style="color: #f59e0b; font-size: 14px;">⏳ Аудио еще не загружено</div>
                    {% endif %}
                </div>
            {% endfor %}
        {% endfor %}

        {% if videos %}
            <div class="book-title">🎬 Видеоуроки</div>
            <div class="video-grid">
                {% for v in videos %}
                    <div class="video-card">
                        {% if 'youtube.com' in v.video_url or 'youtu.be' in v.video_url %}
                            <iframe src="{{ v.video_url.replace('watch?v=', 'embed/').replace('youtu.be/', 'youtube.com/embed/') }}" allowfullscreen></iframe>
                        {% else %}
                            <a href="{{ v.video_url }}" target="_blank" style="color: #7c3aed;">Открыть ссылку на видео</a>
                        {% endif %}
                        <h4>{{ v.title }}</h4>
                    </div>
                {% endfor %}
            </div>
        {% endif %}
    </div>
</body>
</html>
"""

# --- HTML: АДМИНКА (ЗАГРУЗКА) ---
ADMIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Загрузка аудио</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: system-ui, sans-serif; padding: 20px; background: #12121a; color: white; display: flex; justify-content: center; align-items: center; min-height: 80vh; }
        form { width: 100%; max-width: 400px; background: #1a1a26; padding: 30px; border-radius: 12px; box-shadow: 0 4px 15px rgba(0,0,0,0.5); }
        h2 { margin-top: 0; text-align: center; color: #7c3aed; }
        input { width: 100%; padding: 12px; margin: 10px 0 20px 0; background: #2a2a3d; border: 1px solid #3f3f5a; color: white; border-radius: 6px; box-sizing: border-box; }
        button { width: 100%; padding: 14px; background: #7c3aed; color: white; border: none; border-radius: 6px; font-size: 16px; cursor: pointer; }
        button:hover { background: #6d28d9; }
        .msg { padding: 12px; margin-top: 20px; border-radius: 6px; background: #23233a; color: #f59e0b; text-align: center; }
        label { font-size: 14px; color: #8a8aa0; }
    </style>
</head>
<body>
    <form method="POST" enctype="multipart/form-data">
        <h2>Загрузка аудио</h2>
        <label>ID Урока (из бота):</label>
        <input type="number" name="lesson_id" required placeholder="Например: 5">
        <label>Аудиофайл (MP3/OGG):</label>
        <input type="file" name="audio" accept="audio/*" required>
        <button type="submit">Загрузить на GitHub</button>
        {% if message %}<div class="msg">{{ message }}</div>{% endif %}
    </form>
</body>
</html>
"""

@app.route('/')
def index():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Получаем уроки и группируем по книгам
    c.execute("""
        SELECT b.title as book, b.author, l.lesson_number, l.lesson_title, l.sections, l.audio_url 
        FROM lessons l JOIN books b ON l.book_id = b.id ORDER BY b.title, l.lesson_number
    """)
    rows = c.fetchall()
    
    lessons_dict = {}
    for r in rows:
        book = r["book"]
        if book not in lessons_dict:
            lessons_dict[book] = []
        
        # Парсим JSON разделов
        try:
            sections = json.loads(r["sections"])
        except:
            sections = []
            
        lessons_dict[book].append({
            "author": r["author"],
            "lesson_number": r["lesson_number"],
            "lesson_title": r["lesson_title"],
            "sections_list": sections,
            "audio_url": r["audio_url"]
        })
        
    # Получаем видео
    c.execute("SELECT title, video_url FROM videos ORDER BY id DESC")
    videos = [dict(v) for v in c.fetchall()]
    conn.close()

    return render_template_string(CLIENT_HTML, lessons=lessons_dict, videos=videos)

@app.route('/admin', methods=['GET', 'POST'])
def admin():
    message = ""
    if request.method == 'POST':
        lesson_id = request.form.get('lesson_id')
        file = request.files.get('audio')
        if not lesson_id or not file:
            message = "Ошибка: выбери файл и укажи ID"
            return render_template_string(ADMIN_HTML, message=message)
        
        filename = f"lesson_{lesson_id}_{file.filename}"
        try:
            file_bytes = io.BytesIO(file.read())
            audio_url = github_upload_audio(file_bytes, filename)
        except Exception as e:
            message = f"Ошибка GitHub: {e}"
            return render_template_string(ADMIN_HTML, message=message)
            
        try:
            chat_id = update_audio_url(int(lesson_id), audio_url)
            message = f"✅ Аудио привязано к уроку {lesson_id}."
            send_telegram_notification(chat_id, f"✅ **Аудио загружено!**\nУрок ID: `{lesson_id}`\n[Ссылка]({audio_url})")
        except Exception as e:
            message = f"Ошибка БД: {e}"
            
    return render_template_string(ADMIN_HTML, message=message)

# --- Запуск Бота ---
async def start_bot():
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="new", description="Добавить контент"),
        BotCommand(command="all", description="Все уроки"),
        BotCommand(command="delete", description="Удалить урок")
    ])
    print("Бот запущен...")
    await dp.start_polling(bot, handle_signals=False)

def run_bot_thread():
    asyncio.run(start_bot())

if __name__ == '__main__':
    init_db()
    threading.Thread(target=run_bot_thread, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
