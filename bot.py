import re
import io
import time
import datetime
import requests
from telethon import TelegramClient, events, Button
from config import settings
from database import get_or_create_book, save_lesson, update_audio_url, get_last_book, delete_lesson, get_all_lessons
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

def is_admin(e):
    return e.sender_id in settings.admin_ids

def is_fresh(event):
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - event.date).total_seconds() < 60

# ==========================================
# ГЛАВНОЕ МЕНЮ И КОМАНДЫ
# ==========================================
@client.on(events.NewMessage(pattern='/start', func=is_admin))
async def cmd_start(event):
    if not is_fresh(event): return
    user_states[event.sender_id] = {"step": None, "data": {}}
    
    buttons = [
        [Button.inline("1. Записать новую книгу", b"flow_new")],
        [Button.inline("2. Продолжить существующую", b"flow_existing")],
        [Button.inline("📚 Управление библиотекой", b"manage_all")]
    ]
    await event.respond("Выбери действие:", buttons=buttons)

@client.on(events.NewMessage(pattern='/full', func=is_admin))
async def cmd_full(event):
    if not is_fresh(event): return
    user_states[event.sender_id] = {"step": "waiting_audio", "data": {"is_new": True, "is_lazy": True}}
    await event.respond("🚀 **Режим отложенной загрузки.**\n\nОтправь мне аудиофайл.")

# ==========================================
# УПРАВЛЕНИЕ БИБЛИОТЕКОЙ (УДАЛЕНИЕ И РЕДАКТИРОВАНИЕ)
# ==========================================
@client.on(events.NewMessage(pattern='/delete', func=is_admin))
@client.on(events.CallbackQuery(data=b"manage_all", func=is_admin))
async def manage_all(event):
    if not is_fresh(event): return
    lessons = get_all_lessons()
    if not lessons:
        text = "В базе пока нет ни одного урока."
        if isinstance(event, events.CallbackQuery.Event): await event.edit(text)
        else: await event.respond(text)
        return
    
    buttons = []
    current_book = ""
    for l in lessons:
        if l['book_title'] != current_book:
            current_book = l['book_title']
            buttons.append([Button.inline(f"📕 {current_book}", b"ignore")])
        buttons.append([Button.inline(f"{l['lesson_title']} (Часть {l['part']})", f"manage_{l['id']}".encode('utf-8'))])
    buttons.append([Button.inline("⬅️ В главное меню", b"back_start")])
    
    text = "Выбери урок для управления:"
    if isinstance(event, events.CallbackQuery.Event): await event.edit(text, buttons=buttons)
    else: await event.respond(text, buttons=buttons)

@client.on(events.CallbackQuery(pattern=b"manage_(\\d+)", func=is_admin))
async def action_with_lesson(event):
    lesson_id = int(event.pattern_match.group(1).decode('utf-8'))
    buttons = [
        [Button.inline("✏️ Редактировать", f"edit_{lesson_id}".encode('utf-8'))],
        [Button.inline("🗑 Удалить", f"del_{lesson_id}".encode('utf-8'))],
        [Button.inline("⬅️ Назад", b"manage_all")]
    ]
    await event.edit("Что сделать с этим уроком?", buttons=buttons)

# --- РЕДАКТИРОВАНИЕ ---
@client.on(events.CallbackQuery(pattern=b"edit_(\\d+)", func=is_admin))
async def start_edit(event):
    lesson_id = int(event.pattern_match.group(1).decode('utf-8'))
    user_states[event.sender_id] = {"step": "edit_title", "data": {"lesson_id": lesson_id}}
    await event.edit("Введи **новое название книги и автора** через запятую.\nПример: Далиль ат-Талиб, Шейх Марʿи")

@client.on(events.NewMessage(func=lambda e: is_admin(e) and e.text and user_states.get(e.sender_id, {}).get("step") == "edit_title"))
async def process_edit_title(event):
    if not is_fresh(event): return
    text = event.text.strip()
    if "," not in text:
        await event.respond("Пожалуйста, укажи название и автора через запятую.")
        return
    title, author = [x.strip() for x in text.split(",", 1)]
    user_states[event.sender_id]["data"]["title"] = title
    user_states[event.sender_id]["data"]["author"] = author
    user_states[event.sender_id]["step"] = "edit_part"
    await event.respond("Введи **новый номер части** (только цифру):")

@client.on(events.NewMessage(func=lambda e: is_admin(e) and e.text and user_states.get(e.sender_id, {}).get("step") == "edit_part"))
async def process_edit_part(event):
    if not is_fresh(event): return
    text = event.text.strip()
    if not text.isdigit():
        await event.respond("Пожалуйста, напиши номер части цифрами.")
        return
    user_states[event.sender_id]["data"]["part"] = int(text)
    user_states[event.sender_id]["step"] = "edit_lesson_title"
    await event.respond("Введи **новое название урока**:")

@client.on(events.NewMessage(func=lambda e: is_admin(e) and e.text and user_states.get(e.sender_id, {}).get("step") == "edit_lesson_title"))
async def process_edit_lesson_title(event):
    if not is_fresh(event): return
    lesson_title = event.text.strip()
    data = user_states[event.sender_id]["data"]
    
    # Обновляем в базе
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    slug = make_slug(data["title"])
    
    # Обновляем или создаем книгу
    c.execute("SELECT id FROM books WHERE slug = ?", (slug,))
    row = c.fetchone()
    if row:
        book_id = row[0]
        c.execute("UPDATE books SET title = ?, author = ? WHERE id = ?", (data["title"], data["author"], book_id))
    else:
        c.execute("INSERT INTO books (title, author, slug) VALUES (?, ?, ?)", (data["title"], data["author"], slug))
        book_id = c.lastrowid
        
    # Обновляем урок
    c.execute("UPDATE lessons SET book_id = ?, lesson_title = ?, part = ? WHERE id = ?", 
              (book_id, lesson_title, data["part"], data["lesson_id"]))
    conn.commit()
    conn.close()
    
    # Синхронизируем с GitHub (вызываем функцию из database.py)
    from database import sync_to_github
    sync_to_github()
    
    user_states[event.sender_id] = {"step": None, "data": {}}
    await event.respond("✅ Данные урока успешно обновлены!")

# --- УДАЛЕНИЕ ---
@client.on(events.CallbackQuery(pattern=b"del_(\\d+)", func=is_admin))
async def confirm_delete(event):
    lesson_id = int(event.pattern_match.group(1).decode('utf-8'))
    buttons = [
        [Button.inline("✅ Да, удалить", f"do_del_{lesson_id}".encode('utf-8'))],
        [Button.inline("❌ Отмена", b"manage_all")]
    ]
    await event.edit("Вы уверены, что хотите удалить этот урок?", buttons=buttons)

@client.on(events.CallbackQuery(pattern=b"do_del_(\\d+)", func=is_admin))
async def execute_delete(event):
    lesson_id = int(event.pattern_match.group(1).decode('utf-8'))
    if delete_lesson(lesson_id):
        await event.answer("Урок удален!", alert=True)
        await manage_all(event)
    else:
        await event.answer("Ошибка: урок не найден.", alert=True)

@client.on(events.CallbackQuery(data=b"back_start", func=is_admin))
async def back_start(event):
    user_states[event.sender_id] = {"step": None, "data": {}}
    buttons = [
        [Button.inline("1. Записать новую книгу", b"flow_new")],
        [Button.inline("2. Продолжить существующую", b"flow_existing")],
        [Button.inline("📚 Управление библиотекой", b"manage_all")]
    ]
    await event.edit("Выбери действие:", buttons=buttons)

# ==========================================
# ДОБАВЛЕНИЕ (ЛОГИКА ОСТАЕТСЯ ПРЕЖНЕЙ)
# ==========================================
@client.on(events.CallbackQuery(data=b"flow_new", func=is_admin))
async def flow_new(event):
    user_states[event.sender_id] = {"step": "waiting_audio", "data": {"is_new": True, "is_lazy": False}}
    await event.edit("Создание нового цикла.\n\nОтправь мне аудиофайл (любого размера).")

@client.on(events.CallbackQuery(data=b"flow_existing", func=is_admin))
async def flow_existing(event):
    last_book = get_last_book()
    if not last_book:
        await event.edit("В базе нет ни одной книги.")
        return
    user_states[event.sender_id] = {"step": "waiting_audio", "data": {"is_new": False, "is_lazy": False, "book_id": last_book["id"], "title": last_book["title"]}}
    await event.edit(f"Продолжаем книгу: {last_book['title']}\n\nОтправь мне аудиофайл.")

@client.on(events.NewMessage(func=lambda e: is_admin(e) and (e.audio or e.voice or e.document)))
async def handle_audio(event):
    if not is_fresh(event): return
    user_id = event.sender_id
    state = user_states.get(user_id)
    if not state or state.get("step") != "waiting_audio": return 
    
    is_lazy = state["data"].get("is_lazy", False)

    if is_lazy:
        user_states[user_id]["data"]["audio_msg_id"] = event.id 
        user_states[user_id]["step"] = "waiting_title" if state["data"]["is_new"] else "waiting_part"
        if state["data"]["is_new"]:
            await event.respond("Файл отложен!\nНапиши название книги и автора через запятую.")
        else:
            await event.respond("Файл отложен!\nВведи номер части (например: 1, 2 или 20):")
    else:
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

@client.on(events.NewMessage(func=lambda e: is_admin(e) and e.text and not e.text.startswith('/') and user_states.get(e.sender_id, {}).get("step") in ["waiting_title", "waiting_part", "waiting_lesson_title"]))
async def handle_text(event):
    if not is_fresh(event): return
    user_id = event.sender_id
    state = user_states.get(user_id)
    text = event.text.strip()
    
    if state["step"] == "waiting_title":
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
            
        lesson_id = save_lesson({
            "book_id": book_id, 
            "lesson_title": lesson_title,
            "part": data["part"]
        })
        
        if data.get("is_lazy"):
            user_states[user_id]["data"]["lesson_id"] = lesson_id
            buttons = [[Button.inline("📤 Выгрузить аудио на хостинг", b"lazy_upload")]]
            await event.respond(f"✅ Данные сохранены! Часть {data['part']} добавлена в базу.\n\nНажми кнопку ниже, чтобы загрузить само аудио.", buttons=buttons)
        else:
            status_msg = await event.respond("⏳ Загружаю на GitHub...")
            try:
                audio_url = github_upload_audio(io.BytesIO(data["audio_data"]), f"lesson_{lesson_id}.mp3")
                update_audio_url(lesson_id, audio_url)
                user_states[user_id] = {"step": None, "data": {}}
                buttons = [
                    [Button.inline("1. Записать новую книгу", b"flow_new")],
                    [Button.inline("2. Продолжить существующую", b"flow_existing")],
                    [Button.inline("📚 Управление библиотекой", b"manage_all")]
                ]
                await status_msg.edit(f"✅ Готово! Часть {data['part']} загружена.", buttons=buttons)
            except Exception as e:
                await status_msg.edit(f"❌ Ошибка: {e}")
                user_states[user_id] = None

@client.on(events.CallbackQuery(data=b"lazy_upload", func=is_admin))
async def lazy_upload(event):
    user_id = event.sender_id
    state = user_states.get(user_id)
    if not state or "audio_msg_id" not in state["data"]:
        await event.answer("Ошибка: данные файла утеряны.", alert=True)
        return

    msg_id = state["data"]["audio_msg_id"]
    lesson_id = state["data"]["lesson_id"]
    part = state["data"]["part"]

    await event.edit("⏳ Скачиваю файл из Telegram...")
    try:
        message_obj = await client.get_messages(event.chat_id, ids=msg_id)
        file_bytes = await message_obj.download_media(bytes)
        
        await event.edit("⏳ Загружаю файл на GitHub...")
        audio_url = github_upload_audio(io.BytesIO(file_bytes), f"lesson_{lesson_id}.mp3")
        update_audio_url(lesson_id, audio_url)
        
        user_states[user_id] = {"step": None, "data": {}}
        buttons = [
            [Button.inline("1. Записать новую книгу", b"flow_new")],
            [Button.inline("2. Продолжить существующую", b"flow_existing")],
            [Button.inline("📚 Управление библиотекой", b"manage_all")]
        ]
        await event.edit(f"✅ Успех! Часть {part} загружена на сайт.", buttons=buttons)
    except Exception as e:
        await event.edit(f"❌ Ошибка при загрузке: {e}")
