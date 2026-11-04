import re
import io
import time
import datetime
import requests
import asyncio
from telethon import TelegramClient, events, Button
from config import settings
from database import get_or_create_book, save_lesson, update_audio_url, get_last_book, delete_lesson, get_all_lessons, sync_to_github
import sqlite3

def make_slug(title: str) -> str:
    eng_words = re.findall(r'[a-zA-Z0-9]+', title)
    if eng_words: return "-".join(eng_words).lower()
    return f"book-{abs(hash(title)) % 10000}"

def github_upload_audio(file_bytes: io.BytesIO, filename: str) -> str:
    headers = {"Authorization": f"token {settings.github_token}", "Accept": "application/vnd.github.v3+json"}
    base_tag = filename.rsplit(".", 1)[0].replace(" ", "_")
    unique_tag = f"{base_tag}_{int(time.time())}"
    
    payload = {"tag_name": unique_tag, "name": f"Файл {base_tag}", "body": "Загружено", "draft": False, "prerelease": False}
    resp = requests.post(f"https://api.github.com/repos/{settings.github_repo}/releases", json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    upload_url = resp.json()["upload_url"].split("{")[0]
    
    upload_headers = headers.copy()
    upload_headers["Content-Type"] = "application/octet-stream"
    file_bytes.seek(0)
    upload_resp = requests.post(f"{upload_url}?name={filename}", headers=upload_headers, data=file_bytes.getvalue(), timeout=300)
    upload_resp.raise_for_status()
    return upload_resp.json()["browser_download_url"]

client = TelegramClient('audio_book_session', settings.api_id, settings.api_hash)
user_states = {}

# Очередь для фоновой загрузки
upload_queue = asyncio.Queue()

def is_admin(e):
    return e.sender_id in settings.admin_ids

def is_fresh(event):
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - event.date).total_seconds() < 60

# ==========================================
# ФОНОВЫЙ ВОРКЕР (РАБОТАЕТ ВТИХУЮ)
# ==========================================
async def upload_worker():
    print("🚀 Фоновый воркер запущен и ждет файлы...", flush=True)
    while True:
        task = await upload_queue.get()
        try:
            msg_id = task["msg_id"]
            lesson_id = task["lesson_id"]
            chat_id = task["chat_id"]
            part = task["part"]
            
            # 1. Скачиваем файл
            message_obj = await client.get_messages(chat_id, ids=msg_id)
            file_bytes = await message_obj.download_media(bytes)
            
            # 2. Загружаем на GitHub
            audio_url = github_upload_audio(io.BytesIO(file_bytes), f"lesson_{lesson_id}.mp3")
            
            # 3. Обновляем ссылку в БД
            update_audio_url(lesson_id, audio_url)
            
            print(f"✅ Файл части {part} успешно загружен.", flush=True)
            
            # Уведомляем админа об успехе
            try:
                await client.send_message(chat_id, f"✅ Фоновая загрузка: Часть {part} готова и появилась на сайте!")
            except: pass
            
            # Пауза между загрузками, чтобы не забанили
            await asyncio.sleep(3) 
            
        except Exception as e:
            print(f"❌ Ошибка в фоновом воркере: {e}", flush=True)
            try:
                await client.send_message(task["chat_id"], f"❌ Ошибка при загрузке части {task.get('part')}: {e}")
            except: pass
        
        upload_queue.task_done()

# ==========================================
# ГЛАВНОЕ МЕНЮ
# ==========================================
@client.on(events.NewMessage(pattern='/start', func=is_admin))
async def cmd_start(event):
    if not is_fresh(event): return
    user_states[event.sender_id] = {"step": None, "data": {}}
    
    buttons = [
        [Button.inline("1. Записать новую книгу", b"flow_new")],
        [Button.inline("2. Продолжить существующую", b"flow_existing")],
        [Button.inline("🚀 Фоновая загрузка (Очередь)", b"flow_queue")],
        [Button.inline("📚 Управление библиотекой", b"manage_all")]
    ]
    await event.respond("Выбери действие:", buttons=buttons)

@client.on(events.CallbackQuery(data=b"flow_queue", func=is_admin))
async def start_queue_mode(event):
    user_states[event.sender_id] = {"step": "queue_audio", "data": {}}
    await event.edit("⚡️ **Режим фоновой очереди.**\n\nСкидывай мне аудиофайлы один за другим. Я буду спрашивать данные и сразу отправлять их в очередь на загрузку. Ты можешь продолжать кидать следующие файлы, не дожидаясь окончания.")

# ==========================================
# ОБРАБОТКА ФАЙЛОВ И ОЧЕРЕДИ
# ==========================================
@client.on(events.NewMessage(func=lambda e: is_admin(e) and (e.audio or e.voice or e.document)))
async def handle_audio(event):
    if not is_fresh(event): return
    user_id = event.sender_id
    state = user_states.get(user_id)
    
    if not state: return

    # Режим очереди
    if state.get("step") == "queue_audio":
        book_id = state["data"].get("book_id")
        
        if not book_id:
            await event.respond("Сначала напиши название книги и автора через запятую:")
            state["step"] = "queue_book_title"
            state["data"]["temp_msg_id"] = event.id
            return
            
        state["data"]["temp_msg_id"] = event.id
        state["step"] = "queue_file_data"
        await event.respond("Введи **номер части и название урока** через вертикальную черту `|`.\nПример: `5 | Книга намаза`")

    # Обычный режим (как было)
    elif state.get("step") == "waiting_audio":
        status_msg = await event.respond("⏳ Скачиваю файл...")
        try:
            file_bytes = await event.download_media(bytes)
            user_states[user_id]["data"]["audio_data"] = file_bytes
            user_states[user_id]["step"] = "waiting_title" if state["data"]["is_new"] else "waiting_part"
            if state["data"]["is_new"]:
                await status_msg.edit("Аудио готово!\nНапиши название книги и автора через запятую.")
            else:
                await status_msg.edit("Аудио готово!\nВведи номер части (например: 1, 2 или 20):")
        except Exception as e:
            await status_msg.edit(f"❌ Ошибка: {e}")
            user_states[user_id] = None

# ==========================================
# ОБРАБОТКА ТЕКСТА (ДЛЯ ОЧЕРЕДИ И ОБЫЧНОГО РЕЖИМА)
# ==========================================
@client.on(events.NewMessage(func=lambda e: is_admin(e) and e.text and not e.text.startswith('/')))
async def handle_text(event):
    if not is_fresh(event): return
    user_id = event.sender_id
    state = user_states.get(user_id)
    if not state: return
    text = event.text.strip()

    # --- ЛОГИКА ОЧЕРЕДИ ---
    if state["step"] == "queue_book_title":
        if "," not in text:
            await event.respond("Пожалуйста, укажи название и автора через запятую.")
            return
        title, author = [x.strip() for x in text.split(",", 1)]
        slug = make_slug(title)
        book_id = get_or_create_book(title, author, slug)
        
        state["data"]["book_id"] = book_id
        state["step"] = "queue_file_data"
        await event.respond("Книга выбрана! Теперь введи **номер части и название урока** для отправленного файла.\nПример: `1 | Введение`")
        return

    if state["step"] == "queue_file_data":
        if "|" not in text:
            await event.respond("Ошибка формата. Пиши: `Номер | Название`")
            return
        
        part_str, lesson_title = [x.strip() for x in text.split("|", 1)]
        if not part_str.isdigit():
            await event.respond("Номер части должен быть цифрой. Пример: `1 | Введение`")
            return
            
        part = int(part_str)
        book_id = state["data"]["book_id"]
        msg_id = state["data"]["temp_msg_id"]

        # 1. Сохраняем урок в БД (пока без аудио)
        lesson_id = save_lesson({"book_id": book_id, "lesson_title": lesson_title, "part": part})
        
        # 2. Отправляем задачу в фоновую очередь
        await upload_queue.put({
            "msg_id": msg_id,
            "lesson_id": lesson_id,
            "chat_id": event.chat_id,
            "part": part
        })
        
        # 3. Возвращаемся в режим ожидания следующего файла
        state["step"] = "queue_audio"
        
        queue_size = upload_queue.qsize()
        await event.respond(f"✅ Файл (Часть {part}) добавлен в очередь (всего в очереди: {queue_size}).\n\nМожешь кидать следующий файл!")

    # --- ОБЫЧНЫЙ РЕЖИМ ---
    elif state["step"] == "waiting_title":
        if "," not in text:
            await event.respond("Пожалуйста, укажи название и автора через запятую.")
            return
        title, author = [x.strip() for x in text.split(",", 1)]
        user_states[user_id]["data"]["title"] = title
        user_states[user_id]["data"]["author"] = author
        user_states[user_id]["step"] = "waiting_part"
        await event.respond("Теперь введи номер части (например: 1, 2 или 20):")
        
    elif state["step"] == "waiting_part":
        if not text.isdigit():
            await event.respond("Пожалуйста, напиши номер части цифрами (например: 1):")
            return 
        user_states[user_id]["data"]["part"] = int(text)
        user_states[user_id]["step"] = "waiting_lesson_title"
        await event.respond("Теперь напиши название этого урока (например: Книга намаза):")
        
    elif state["step"] == "waiting_lesson_title":
        lesson_title = text
        data = state["data"]
        
        if data["is_new"]:
            slug = make_slug(data["title"])
            book_id = get_or_create_book(data["title"], data["author"], slug)
        else:
            book_id = data["book_id"]
            
        lesson_id = save_lesson({"book_id": book_id, "lesson_title": lesson_title, "part": data["part"]})
        status_msg = await event.respond("⏳ Загружаю на GitHub...")
        
        try:
            audio_url = github_upload_audio(io.BytesIO(data["audio_data"]), f"lesson_{lesson_id}.mp3")
            update_audio_url(lesson_id, audio_url)
            user_states[user_id] = {"step": None, "data": {}}
            buttons = [
                [Button.inline("1. Записать новую книгу", b"flow_new")],
                [Button.inline("2. Продолжить существующую", b"flow_existing")],
                [Button.inline("🚀 Фоновая загрузка (Очередь)", b"flow_queue")],
                [Button.inline("📚 Управление библиотекой", b"manage_all")]
            ]
            await status_msg.edit(f"✅ Готово! Часть {data['part']} загружена.", buttons=buttons)
        except Exception as e:
            await status_msg.edit(f"❌ Ошибка: {e}")
            user_states[user_id] = None
