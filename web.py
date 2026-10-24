import io
import os
import asyncio
import threading
import requests
import sqlite3
import json
from flask import Flask, request, render_template_string, jsonify
from config import settings
from database import init_db, update_audio_url
from bot import router

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

app = Flask(__name__)

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

# --- API ДЛЯ ВСЕХ УРОКОВ ---
@app.route('/api/lessons.json')
def api_lessons_json():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT b.title as book_title, b.author, b.slug as book_slug, l.id, l.lesson_number, l.lesson_title, l.sections, l.audio_url 
        FROM lessons l JOIN books b ON l.book_id = b.id ORDER BY b.title, l.lesson_number
    """)
    lessons = [dict(r) for r in c.fetchall()]
    c.execute("SELECT id, title, video_url FROM videos ORDER BY id DESC")
    videos = [dict(r) for r in c.fetchall()]
    conn.close()

    data = {"books": {}, "videos": videos}
    for l in lessons:
        book = l["book_title"]
        if book not in data["books"]:
            data["books"][book] = {"author": l["author"], "slug": l["book_slug"], "lessons": []}
        try: sections = json.loads(l["sections"])
        except: sections = []
        data["books"][book]["lessons"].append({
            "id": l["id"], "number": l["lesson_number"], "title": l["lesson_title"],
            "sections": sections, "audio_url": l["audio_url"]
        })
    return jsonify(data)

# --- API ДЛЯ КОНКРЕТНОЙ КНИГИ ---
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

    c.execute("""
        SELECT id, lesson_number, lesson_title, sections, audio_url 
        FROM lessons WHERE book_id = ? ORDER BY lesson_number
    """, (book["id"],))
    lessons = [dict(r) for r in c.fetchall()]
    conn.close()

    for l in lessons:
        try: l["sections"] = json.loads(l["sections"])
        except: l["sections"] = []

    return jsonify({
        "title": book["title"],
        "author": book["author"],
        "slug": slug,
        "lessons": lessons
    })

CLIENT_HTML = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Библиотека уроков</title>
    <style>
        body { font-family: system-ui, sans-serif; background: #12121a; color: #e8e8f0; margin: 0; padding: 20px; }
        h1, h2 { text-align: center; color: #7c3aed; }
        .container { max-width: 800px; margin: 0 auto; }
        .book-title { font-size: 24px; color: #f59e0b; margin-top: 30px; border-bottom: 1px solid #2a2a3d; padding-bottom: 10px; }
        .lesson { background: #1a1a26; border-radius: 10px; padding: 15px; margin: 15px 0; }
        .lesson h3 { margin: 0 0 10px 0; color: #7c3aed; }
        .sections { font-size: 14px; color: #8a8aa0; margin-bottom: 15px; }
        audio { width: 100%; border-radius: 5px; margin-top: 10px; }
        .video-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; margin-top: 20px; }
        .video-card { background: #1a1a26; padding: 15px; border-radius: 10px; }
        .video-card iframe { width: 100%; aspect-ratio: 16/9; border: none; border-radius: 8px; }
        .video-card h4 { margin: 10px 0 0 0; font-size: 16px; }
        .loader { text-align: center; margin-top: 50px; font-size: 20px; color: #7c3aed; }
        a { color: #7c3aed; text-decoration: none; }
        a:hover { text-decoration: underline; }
    </style>
</head>
<body>
    <div class="container" id="app">
        <h1>📚 Библиотека</h1>
        <div id="content"><div class="loader">Загрузка...</div></div>
    </div>

    <script>
        const path = window.location.pathname;
        const parts = path.split('/').filter(p => p);

        async function loadData() {
            if (parts.length === 2 && parts[0] === 'book') {
                const slug = parts[1];
                const res = await fetch(`/api/book/${slug}.json`);
                if (!res.ok) {
                    document.getElementById('content').innerHTML = `<h2>Книга не найдена :(</h2>`;
                    return;
                }
                const data = await res.json();
                renderSingleBook(data);
            } else {
                const res = await fetch('/api/lessons.json');
                const data = await res.json();
                renderAll(data);
            }
        }

        function renderSingleBook(bookData) {
            // СОРТИРОВКА ПО НОМЕРУ УРОКА
            bookData.lessons.sort((a, b) => a.number - b.number);

            let html = `<a href="/">← Назад ко всем книгам</a>`;
            html += `<h2>📕 ${bookData.title} <span style="font-size:16px; color:#8a8aa0;">(${bookData.author})</span></h2>`;
            
            bookData.lessons.forEach(lesson => {
                html += `<div class="lesson">
                    <h3>Урок ${lesson.number}: ${lesson.title}</h3>
                    <div class="sections">${lesson.sections.map(s => `• ${s}<br>`).join('')}</div>`;
                if (lesson.audio_url) {
                    html += `<audio controls src="${lesson.audio_url}"></audio>`;
                } else {
                    html += `<div style="color: #f59e0b; font-size: 14px;">⏳ Аудио еще не загружено</div>`;
                }
                html += `</div>`;
            });
            document.getElementById('content').innerHTML = html;
        }

        function renderAll(data) {
            let html = '';

            for (const [bookTitle, bookData] of Object.entries(data.books)) {
                // СОРТИРОВКА ПО НОМЕРУ УРОКА
                bookData.lessons.sort((a, b) => a.number - b.number);

                html += `<div class="book-title">
                    📕 <a href="/book/${bookData.slug}">${bookTitle}</a> 
                    <span style="font-size:14px; color:#8a8aa0;">(${bookData.author})</span>
                </div>`;
                
                bookData.lessons.forEach(lesson => {
                    html += `<div class="lesson">
                        <h3>Урок ${lesson.number}: ${lesson.title}</h3>
                        <div class="sections">${lesson.sections.map(s => `• ${s}<br>`).join('')}</div>`;
                    if (lesson.audio_url) {
                        html += `<audio controls src="${lesson.audio_url}"></audio>`;
                    } else {
                        html += `<div style="color: #f59e0b; font-size: 14px;">⏳ Аудио еще не загружено</div>`;
                    }
                    html += `</div>`;
                });
            }

            if (data.videos && data.videos.length > 0) {
                html += `<div class="book-title">🎬 Видеоуроки</div><div class="video-grid">`;
                data.videos.forEach(video => {
                    let embedUrl = video.video_url;
                    if (video.video_url.includes('youtube.com/watch?v=')) embedUrl = video.video_url.replace('watch?v=', 'embed/');
                    else if (video.video_url.includes('youtu.be/')) embedUrl = video.video_url.replace('youtu.be/', 'youtube.com/embed/');
                    html += `<div class="video-card"><iframe src="${embedUrl}" allowfullscreen></iframe><h4>${video.title}</h4></div>`;
                });
                html += `</div>`;
            }

            document.getElementById('content').innerHTML = html || '<p>Пока пусто.</p>';
        }

        loadData();
    </script>
</body>
</html>
"""

@app.route('/')
@app.route('/book/<slug>')
def index(slug=None):
    return render_template_string(CLIENT_HTML)

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
