import re
import io
import requests
from telethon import TelegramClient, events, Button
from config import settings
from database import get_or_create_book, save_lesson, update_audio_url, get_last_book

def make_slug(title: str) -> str:
    eng_words = re.findall(r'[a-zA-Z0-9]+', title)
    if eng_words: return "-".join(eng_words).lower()
    return f"book-{abs(hash(title)) % 10000}"

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
    upload_resp = requests.post(f"{upload_url}?name={filename}", headers=upload_headers, data=file_bytes.getvalue(), timeout=300)
    upload_resp.raise_for_status()
    return upload_resp.json()["browser_download_url"]

# --- ИНИЦИАЛИЗАЦИЯ TELETHON ---
client = TelegramClient('audio_book_session', settings.api_id, settings.api_hash)

# Словарь для хранения состояний пользователей
user_states = {}

# ==========================================
# ОБРАБОТЧИК КОМАНДЫ /start
# ==========================================
@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    user_id = event.sender_id
    user_states[user_id] = {"step": None, "data": {}}
    
    buttons = [
        [Button.inline("1. Записать новую книгу", b"flow_new")],
        [Button.inline("2. Продолжить существующую", b"flow_existing")]
    ]
    await event.respond("Выбери действие:", buttons=buttons)

# ==========================================
# ОБРАБОТЧИК КНОПОК
# ==========================================
@client.on(events.CallbackQuery)
async def handle_callback(event):
    user_id = event.sender_id
    data = event.data.decode('utf-8')
    
    if data == "flow_new":
        user_states[user_id] = {"step": "waiting_audio", "data": {"is_new": True}}
        await event.edit("Создание нового цикла.\n\nОтправь мне аудиофайл (любого размера).")
    elif data == "flow_existing":
        last_book = get_last_book()
        if not last_book:
            await event.edit("В базе нет ни одной книги.")
            return
        user_states[user_id] = {"step": "waiting_audio", "data": {"is_new": False, "book_id": last_book["id"], "title": last_book["title"]}}
        await event.edit(f"Продолжаем книгу: {last_book['title']}\n\nОтправь мне аудиофайл.")

# ==========================================
# ОБРАБОТЧИК ФАЙЛОВ (АУДИО/ГОЛОС/ДОКУМЕНТ)
# ==========================================
@client.on(events.NewMessage(func=lambda e: e.audio or e.voice or e.document))
async def handle_audio(event):
    user_id = event.sender_id
    state = user_states.get(user_id)
    
    if not state or state.get("step") != "waiting_audio":
        return 
    
    status_msg = await event.respond("⏳ Скачиваю файл (без лимитов MTProto)...")
    
    try:
        # Скачиваем в память (Telethon отлично это умеет)
        file_bytes = await event.download_media(bytes)
        
        user_states[user_id]["data"]["audio_data"] = file_bytes
        user_states[user_id]["step"] = "waiting_title" if state["data"]["is_new"] else "waiting_lesson_title"
        
        if state["data"]["is_new"]:
            await status_msg.edit("Аудио готово!\nНапиши название книги и автора через запятую.\nПример: Далиль ат-Талиб, Шейх Марʿи")
        else:
            await status_msg.edit("Аудио готово!\nНапиши название этого урока (например: Урок 20. Книга намаза):")
            
    except Exception as e:
        await status_msg.edit(f"❌ Ошибка: {e}")
        user_states[user_id] = None

# ==========================================
# ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ (ДИАЛОГ)
# ==========================================
@client.on(events.NewMessage(func=lambda e: e.text and not e.text.startswith('/')))
async def handle_text(event):
    user_id = event.sender_id
    state = user_states.get(user_id)
    
    if not state: return
    
    text = event.text.strip()
    
    if state["step"] == "waiting_title":
        if "," not in text:
            await event.respond("Пожалуйста, укажи название и автора через запятую.")
            return
        title, author = [x.strip() for x in text.split(",", 1)]
        user_states[user_id]["data"]["title"] = title
        user_states[user_id]["data"]["author"] = author
        user_states[user_id]["step"] = "waiting_lesson_title"
        await event.respond("Теперь напиши название урока:")
        
    elif state["step"] == "waiting_lesson_title":
        lesson_title = text
        data = state["data"]
        
        if data["is_new"]:
            slug = make_slug(data["title"])
            book_id = get_or_create_book(data["title"], data["author"], slug)
        else:
            book_id = data["book_id"]
            
        lesson_id = save_lesson({"book_id": book_id, "lesson_title": lesson_title})
        
        status_msg = await event.respond("⏳ Загружаю на GitHub...")
        try:
            audio_url = github_upload_audio(io.BytesIO(data["audio_data"]), f"lesson_{lesson_id}.mp3")
            update_audio_url(lesson_id, audio_url)
            await status_msg.edit("✅ Готово! Аудио загружено на сайт.")
        except Exception as e:
            await status_msg.edit(f"❌ Ошибка: {e}")
            
        user_states[user_id] = None

# ==========================================
# ЗАПУСК (ВЫЗЫВАЕТСЯ ИЗ WEB.PY)
# ==========================================
async def start_bot():
    await client.start(bot_token=settings.bot_token)
    print("Бот успешно подключен к Telegram (Telethon)!", flush=True)
    # run_until_disconnected блокирует поток, поэтому его вызывает web.py
    await client.run_until_disconnected()
