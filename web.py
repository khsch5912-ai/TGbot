import io
import os
import asyncio
import threading
import requests
from flask import Flask, request, render_template_string
from config import settings
from database import init_db, update_audio_url
from bot import router

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

app = Flask(__name__)

def github_upload_audio(file_bytes: io.BytesIO, filename: str) -> str:
    headers = {
        "Authorization": f"token {settings.github_token}",
        "Accept": "application/vnd.github.v3+json",
    }
    tag = filename.rsplit(".", 1)[0].replace(" ", "_")
    payload = {
        "tag_name": tag, "name": f"Файл {tag}",
        "body": "Загружено через сайт", "draft": False, "prerelease": False,
    }
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
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Ошибка уведомления: {e}")

HTML = """
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
        button { width: 100%; padding: 14px; background: #7c3aed; color: white; border: none; border-radius: 6px; font-size: 16px; cursor: pointer; transition: background 0.2s; }
        button:hover { background: #6d28d9; }
        .msg { padding: 12px; margin-top: 20px; border-radius: 6px; background: #23233a; color: #f59e0b; word-break: break-all; text-align: center; }
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

@app.route('/', methods=['GET', 'POST'])
def upload():
    message = ""
    if request.method == 'POST':
        lesson_id = request.form.get('lesson_id')
        file = request.files.get('audio')

        if not lesson_id or not file:
            message = "Ошибка: выбери файл и укажи ID"
            return render_template_string(HTML, message=message)

        filename = f"lesson_{lesson_id}_{file.filename}"
        try:
            file_bytes = io.BytesIO(file.read())
            audio_url = github_upload_audio(file_bytes, filename)
        except Exception as e:
            message = f"Ошибка GitHub: {e}"
            return render_template_string(HTML, message=message)

        try:
            chat_id = update_audio_url(int(lesson_id), audio_url)
            message = f"✅ Успех! Аудио привязано к уроку {lesson_id}."
            
            send_telegram_notification(
                chat_id, 
                f"✅ **Аудио успешно загружено!**\n\nУрок ID: `{lesson_id}`\n[Ссылка]({audio_url})"
            )
        except Exception as e:
            message = f"Ошибка БД: {e}"

    return render_template_string(HTML, message=message)

async def start_bot():
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command="start", description="Добавить урок"), BotCommand(command="delete", description="Удалить урок")])
    print("Бот запущен в фоне...")
    await dp.start_polling(bot, handle_signals=False)

def run_bot_thread():
    asyncio.run(start_bot())

if __name__ == '__main__':
    init_db()
    threading.Thread(target=run_bot_thread, daemon=True).start()
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
    # В функции start_bot() обнови установку команд:
async def start_bot():
    bot = Bot(token=settings.bot_token)
    dp = Dispatcher()
    dp.include_router(router)
    
    # Добавили команду /video
    await bot.set_my_commands([
        BotCommand(command="start", description="Добавить аудиоурок"),
        BotCommand(command="video", description="Добавить видеоурок"),
        BotCommand(command="delete", description="Удалить урок")
    ])
    
    print("Бот запущен в фоне...")
    await dp.start_polling(bot, handle_signals=False)
