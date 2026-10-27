import re
import io
import requests
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
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

# --- ИНИЦИАЛИЗАЦИЯ PYROGRAM ---
app = Client(
    "audio_book_bot",
    api_id=settings.api_id,
    api_hash=settings.api_hash,
    bot_token=settings.bot_token
)

user_states = {}

@app.on_message(filters.command("start"))
async def cmd_start(client, message: Message):
    print(f"=== ПОЛУЧЕНО /start ОТ {message.from_user.id} ===", flush=True)
    user_id = message.from_user.id
    user_states[user_id] = {"step": None, "data": {}}
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("1. Записать новую книгу", callback_data="flow_new")],
        [InlineKeyboardButton("2. Продолжить существующую", callback_data="flow_existing")]
    ])
    
    try:
        await message.reply_text("Выбери действие:", reply_markup=kb)
        print("=== ОТВЕТ УСПЕШНО ОТПРАВЛЕН ===", flush=True)
    except Exception as e:
        print(f"=== ОШИБКА ОТПРАВКИ: {e} ===", flush=True)

@app.on_callback_query()
async def handle_callback(client, callback: CallbackQuery):
    print(f"=== НАЖАТА КНОПКА: {callback.data} ===", flush=True)
    user_id = callback.from_user.id
    data = callback.data
    
    if data == "flow_new":
        user_states[user_id] = {"step": "waiting_audio", "data": {"is_new": True}}
        await callback.message.edit_text("Создание нового цикла.\n\nОтправь мне аудиофайл (любого размера).")
    elif data == "flow_existing":
        last_book = get_last_book()
        if not last_book:
            await callback.message.edit_text("В базе нет ни одной книги.")
            return
        user_states[user_id] = {"step": "waiting_audio", "data": {"is_new": False, "book_id": last_book["id"], "title": last_book["title"]}}
        await callback.message.edit_text(f"Продолжаем книгу: {last_book['title']}\n\nОтправь мне аудиофайл.")

@app.on_message(filters.audio | filters.voice | filters.document)
async def handle_audio(client, message: Message):
    print(f"=== ПОЛУЧЕН ФАЙЛ ОТ {message.from_user.id} ===", flush=True)
    user_id = message.from_user.id
    state = user_states.get(user_id)
    
    if not state or state.get("step") != "waiting_audio":
        print("=== ФАЙЛ ПРОИГНОРИРОВАН (неверное состояние) ===", flush=True)
        return 
    
    status_msg = await message.reply_text("⏳ Скачиваю файл (без лимитов MTProto)...")
    print("=== НАЧАЛО СКАЧИВАНИЯ ===", flush=True)
    
    try:
        file_bytes = await client.download_media(message, in_memory=True)
        print("=== ФАЙЛ УСПЕШНО СКАЧАН ===", flush=True)
        
        user_states[user_id]["data"]["audio_data"] = file_bytes.getvalue()
        user_states[user_id]["step"] = "waiting_title" if state["data"]["is_new"] else "waiting_lesson_title"
        
        if state["data"]["is_new"]:
            await status_msg.edit_text("Аудио готово!\nНапиши название книги и автора через запятую.\nПример: Далиль ат-Талиб, Шейх Марʿи")
        else:
            await status_msg.edit_text("Аудио готово!\nНапиши название этого урока (например: Урок 20. Книга намаза):")
            
    except Exception as e:
        print(f"=== ОШИБКА СКАЧИВАНИЯ: {e} ===", flush=True)
        await status_msg.edit_text(f"❌ Ошибка: {e}")
        user_states[user_id] = None
