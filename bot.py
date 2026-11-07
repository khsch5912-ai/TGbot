import re
import io
import time
import datetime
import requests
import asyncio
from telethon import TelegramClient, events, Button
from config import settings
from database import (
    get_or_create_book, save_lesson, update_audio_url, get_last_book, 
    delete_lesson, get_all_lessons, sync_to_github, get_all_books, 
    rename_book, move_lessons_to_book, delete_book
)
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
upload_queue = asyncio.Queue()
active_books = {} 

def is_admin(e):
    return e.sender_id in settings.admin_ids

def is_fresh(event):
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - event.date).total_seconds() < 60

# ==========================================
# ФОНОВЫЙ ВОРКЕР
# ==========================================
async def upload_worker():
    print("🚀 Фоновый воркер запущен...", flush=True)
    while True:
        task = await upload_queue.get()
        try:
            msg_id = task["msg_id"]
            lesson_id = task["lesson_id"]
            chat_id = task["chat_id"]
            part = task["part"]
            
            message_obj = await client.get_messages(chat_id, ids=msg_id)
            file_bytes = await message_obj.download_media(bytes)
            
            audio_url = github_upload_audio(io.BytesIO(file_bytes), f"lesson_{lesson_id}.mp3")
            update_audio_url(lesson_id, audio_url)
            
            try:
                await client.send_message(chat_id, f"✅ Фоновая загрузка: Часть {part} готова и появилась на сайте!")
            except: pass
            
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
        [Button.inline("📝 Управление уроками", b"manage_lessons")],
        [Button.inline("📚 Управление книгами", b"manage_books")]
    ]
    await event.respond("Выбери действие:", buttons=buttons)

# ==========================================
# УПРАВЛЕНИЕ КНИГАМИ
# ==========================================
@client.on(events.CallbackQuery(data=b"manage_books", func=is_admin))
async def manage_books(event):
    books = get_all_books()
    if not books:
        await event.edit("В базе нет ни одной книги.")
        return
    
    buttons = []
    for b in books:
        buttons.append([Button.inline(f"📕 {b['title']}", f"book_edit_{b['id']}".encode('utf-8'))])
    buttons.append([Button.inline("⬅️ В главное меню", b"back_start")])
    await event.edit("Выбери книгу для управления:", buttons=buttons)

@client.on(events.CallbackQuery(pattern=b"book_edit_(\\d+)", func=is_admin))
async def book_action(event):
    book_id = int(event.pattern_match.group(1).decode('utf-8'))
    buttons = [
        [Button.inline("✏️ Переименовать", f"book_ren_{book_id}".encode('utf-8'))],
        [Button.inline("📦 Перенести части", f"book_move_{book_id}".encode('utf-8'))],
        [Button.inline("🗑 Удалить книгу", f"book_del_{book_id}".encode('utf-8'))],
        [Button.inline("⬅️ Назад", b"manage_books")]
    ]
    await event.edit("Что сделать с книгой?", buttons=buttons)

@client.on(events.CallbackQuery(pattern=b"book_ren_(\\d+)", func=is_admin))
async def rename_book_start(event):
    book_id = int(event.pattern_match.group(1).decode('utf-8'))
    user_states[event.sender_id] = {"step": "rename_book", "data": {"book_id": book_id}}
    await event.edit("Введи **новое название книги и автора** через запятую.")

@client.on(events.NewMessage(func=lambda e: is_admin(e) and e.text and user_states.get(e.sender_id, {}).get("step") == "rename_book"))
async def rename_book_process(event):
    text = event.text.strip()
    if "," not in text:
        await event.respond("Укажи название и автора через запятую.")
        return
    
    title, author = [x.strip() for x in text.split(",", 1)]
    book_id = user_states[event.sender_id]["data"]["book_id"]
    
    slug = make_slug(title)
    rename_book(book_id, title, author, slug)
    
    user_states[event.sender_id] = {"step": None, "data": {}}
    await event.respond(f"✅ Книга успешно переименована в «{title}»!")

@client.on(events.CallbackQuery(pattern=b"book_move_(\\d+)", func=is_admin))
async def move_book_start(event):
    source_book_id = int(event.pattern_match.group(1).decode('utf-8'))
    books = get_all_books()
    
    buttons = []
    for b in books:
        if b["id"] != source_book_id:
            buttons.append([Button.inline(f"В «{b['title']}»", f"book_move_to_{b['id']}_{source_book_id}".encode('utf-8'))])
            
    if not buttons:
        await event.edit("Нет других книг для переноса.")
        return
        
    buttons.append([Button.inline("❌ Отмена", b"manage_books")])
    await event.edit("В какую книгу перенести все части?", buttons=buttons)

@client.on(events.CallbackQuery(pattern=b"book_move_to_(\\d+)_(\\d+)", func=is_admin))
async def move_book_process(event):
    target_id = int(event.pattern_match.group(1).decode('utf-8'))
    source_id = int(event.pattern_match.group(2).decode('utf-8'))
    
    move_lessons_to_book(source_id, target_id)
    await event.edit("✅ Все части перенесены! Исходная книга удалена.")

@client.on(events.CallbackQuery(pattern=b"book_del_(\\d+)", func=is_admin))
async def delete_book_confirm(event):
    book_id = int(event.pattern_match.group(1).decode('utf-8'))
    buttons = [
        [Button.inline("✅ Да, удалить всё", f"book_del_do_{book_id}".encode('utf-8'))],
        [Button.inline("❌ Отмена", b"manage_books")]
    ]
    await event.edit("⚠️ Вы уверены? Это удалит книгу и **все её части** навсегда!", buttons=buttons)

@client.on(events.CallbackQuery(pattern=b"book_del_do_(\\d+)", func=is_admin))
async def delete_book_execute(event):
    book_id = int(event.pattern_match.group(1).decode('utf-8'))
    delete_book(book_id)
    await event.edit("✅ Книга и все её части удалены.")

# ==========================================
# ПРОДОЛЖИТЬ СУЩЕСТВУЮЩУЮ
# ==========================================
@client.on(events.CallbackQuery(data=b"flow_existing", func=is_admin))
async def flow_existing(event):
    user_id = event.sender_id
    if user_id in active_books:
        last_book = next((b for b in get_all_books() if b["id"] == active_books[user_id]), None)
        if last_book:
            user_states[user_id] = {"step": "waiting_audio", "data": {"is_new": False, "book_id": last_book["id"]}}
            buttons = [[Button.inline("🔄 Сменить книгу", b"flow_existing_change")]]
            await event.edit(f"Продолжаем книгу: {last_book['title']}\n\nОтправь мне аудиофайл.", buttons=buttons)
            return
    await show_book_selection(event)

@client.on(events.CallbackQuery(data=b"flow_existing_change", func=is_admin))
async def change_book(event):
    await show_book_selection(event)

async def show_book_selection(event):
    books = get_all_books()
    if not books:
        await event.edit("В базе нет ни одной книги.")
        return
    
    buttons = []
    for b in books:
        buttons.append([Button.inline(f"📕 {b['title']}", f"select_active_{b['id']}".encode('utf-8'))])
    buttons.append([Button.inline("⬅️ В главное меню", b"back_start")])
    await event.edit("Выбери книгу, которую будешь продолжать:", buttons=buttons)

@client.on(events.CallbackQuery(pattern=b"select_active_(\\d+)", func=is_admin))
async def select_active_book(event):
    book_id = int(event.pattern_match.group(1).decode('utf-8'))
    active_books[event.sender_id] = book_id
    book = next((b for b in get_all_books() if b["id"] == book_id), None)
    user_states[event.sender_id] = {"step": "waiting_audio", "data": {"is_new": False, "book_id": book_id}}
    await event.edit(f"✅ Выбрана книга: {book['title']}\n\nОтправь мне аудиофайл.")

# ==========================================
# ФОНОВАЯ ЗАГРУЗКА
# ==========================================
@client.on(events.CallbackQuery(data=b"flow_queue", func=is_admin))
async def start_queue_mode(event):
    buttons = [
        [Button.inline("1. Новая книга", b"queue_new_book")],
        [Button.inline("2. Продолжить существующую", b"queue_existing_book")]
    ]
    await event.edit("⚡️ **Режим фоновой очереди.**\n\nКуда будем добавлять файлы?", buttons=buttons)

@client.on(events.CallbackQuery(data=b"queue_new_book", func=is_admin))
async def queue_new_book(event):
    user_states[event.sender_id] = {"step": "queue_audio", "data": {"is_new": True}}
    await event.edit("Отправь мне первый аудиофайл. Затем напиши название книги и автора.")

@client.on(events.CallbackQuery(data=b"queue_existing_book", func=is_admin))
async def queue_existing_book(event):
    user_id = event.sender_id
    if user_id in active_books:
        book = next((b for b in get_all_books() if b["id"] == active_books[user_id]), None)
        if book:
            user_states[user_id] = {"step": "queue_audio", "data": {"is_new": False, "book_id": book["id"]}}
            await event.edit(f"Продолжаем книгу: {book['title']} (в очереди).\n\nОтправь мне аудиофайл.")
            return
    await event.edit("Сначала выбери книгу в меню «Продолжить существующую».")

# ==========================================
# УПРАВЛЕНИЕ УРОКАМИ
# ==========================================
@client.on(events.NewMessage(pattern='/delete', func=is_admin))
@client.on(events.CallbackQuery(data=b"manage_lessons", func=is_admin))
async def manage_lessons(event):
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
        [Button.inline("⬅️ Назад", b"manage_lessons")]
    ]
    await event.edit("Что сделать с этим уроком?", buttons=buttons)

@client.on(events.CallbackQuery(pattern=b"del_(\\d+)", func=is_admin))
async def confirm_delete(event):
    lesson_id = int(event.pattern_match.group(1).decode('utf-8'))
    buttons = [
        [Button.inline("✅ Да, удалить", f"do_del_{lesson_id}".encode('utf-8'))],
        [Button.inline("❌ Отмена", b"manage_lessons")]
    ]
    await event.edit("Вы уверены, что хотите удалить этот урок?", buttons=buttons)

@client.on(events.CallbackQuery(pattern=b"do_del_(\\d+)", func=is_admin))
async def execute_delete(event):
    lesson_id = int(event.pattern_match.group(1).decode('utf-8'))
    if delete_lesson(lesson_id):
        await event.answer("Урок удален!", alert=True)
        await manage_lessons(event)
    else:
        await event.answer("Ошибка: урок не найден.", alert=True)

@client.on(events.CallbackQuery(pattern=b"edit_(\\d+)", func=is_admin))
async def start_edit(event):
    lesson_id = int(event.pattern_match.group(1).decode('utf-8'))
    user_states[event.sender_id] = {"step": "edit_title", "data": {"lesson_id": lesson_id}}
    await event.edit("Введи **новое название книги и автора** через запятую.\nПример: Далиль ат-Талиб, Шейх Марʿи")

@client.on(events.CallbackQuery(data=b"back_start", func=is_admin))
async def back_start(event):
    user_states[event.sender_id] = {"step": None, "data": {}}
    buttons = [
        [Button.inline("1. Записать новую книгу", b"flow_new")],
        [Button.inline("2. Продолжить существующую", b"flow_existing")],
        [Button.inline("🚀 Фоновая загрузка (Очередь)", b"flow_queue")],
        [Button.inline("📝 Управление уроками", b"manage_lessons")],
        [Button.inline("📚 Управление книгами", b"manage_books")]
    ]
    await event.edit("Выбери действие:", buttons=buttons)

# ==========================================
# ОБЫЧНОЕ ДОБАВЛЕНИЕ
# ==========================================
@client.on(events.CallbackQuery(data=b"flow_new", func=is_admin))
async def flow_new(event):
    user_states[event.sender_id] = {"step": "waiting_audio", "data": {"is_new": True}}
    await event.edit("Создание нового цикла.\n\nОтправь мне аудиофайл.")

@client.on(events.NewMessage(func=lambda e: is_admin(e) and (e.audio or e.voice or e.document)))
async def handle_audio(event):
    user_id = event.sender_id
    state = user_states.get(user_id)
    if not state: return

    if state.get("step") == "queue_audio":
        book_id = state["data"].get("book_id")
        if not book_id:
            await event.respond("Сначала напиши название книги и автора через запятую:")
            state["step"] = "queue_book_title"
            state["data"]["temp_msg_id"] = event.id
            return
        state["data"]["temp_msg_id"] = event.id
        state["step"] = "queue_file_data"
        await event.respond("Введи **номер части и название урока** через `|`.\nПример: `5 | Книга намаза`")

    elif state.get("step") == "waiting_audio":
        status_msg = await event.respond("⏳ Скачиваю файл...")
        try:
            file_bytes = await event.download_media(bytes)
            
            # Никакой обработки текста. Сразу переходим к вопросам.
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
# ОБРАБОТЧИК ТЕКСТА (СТРОГО БЕЗ ФАЙЛОВ)
# ==========================================
# ЖЕСТКИЙ ФИЛЬТР: not (e.audio or e.voice or e.document)
@client.on(events.NewMessage(func=lambda e: is_admin(e) and e.text and not e.text.startswith('/') and not (e.audio or e.voice or e.document)))
async def handle_text(event):
    user_id = event.sender_id
    state = user_states.get(user_id)
    if not state: return
    text = event.text.strip()

    if state["step"] == "queue_book_title":
        if "," not in text:
            await event.respond("Пожалуйста, укажи название и автора через запятую.")
            return
        title, author = [x.strip() for x in text.split(",", 1)]
        slug = make_slug(title)
        book_id = get_or_create_book(title, author, slug)
        state["data"]["book_id"] = book_id
        state["step"] = "queue_file_data"
        await event.respond("Книга выбрана! Теперь введи **номер части и название урока** для файла.\nПример: `1 | Введение`")
        return

    if state["step"] == "queue_file_data":
        if "|" not in text:
            await event.respond("Ошибка формата. Пиши: `Номер | Название`")
            return
        part_str, lesson_title = [x.strip() for x in text.split("|", 1)]
        if not part_str.isdigit():
            await event.respond("Номер части должен быть цифрой.")
            return
            
        part = int(part_str)
        book_id = state["data"]["book_id"]
        msg_id = state["data"]["temp_msg_id"]

        lesson_id = save_lesson({"book_id": book_id, "lesson_title": lesson_title, "part": part})
        await upload_queue.put({
            "msg_id": msg_id,
            "lesson_id": lesson_id,
            "chat_id": event.chat_id,
            "part": part
        })
        
        state["step"] = "queue_audio"
        await event.respond(f"✅ Файл (Часть {part}) добавлен в очередь (всего: {upload_queue.qsize()}).\n\nМожешь кидать следующий файл!")

    elif state["step"] == "edit_title":
        if "," not in text:
            await event.respond("Укажи название и автора через запятую.")
            return
        title, author = [x.strip() for x in text.split(",", 1)]
        state["data"]["title"] = title
        state["data"]["author"] = author
        state["step"] = "edit_part"
        await event.respond("Введи **новый номер части**:")
        
    elif state["step"] == "edit_part":
        if not text.isdigit():
            await event.respond("Номер части должен быть цифрой.")
            return
        state["data"]["part"] = int(text)
        state["step"] = "edit_lesson_title"
        await event.respond("Введи **новое название урока**:")
        
    elif state["step"] == "edit_lesson_title":
        lesson_title = text
        data = state["data"]
        
        conn = sqlite3.connect(settings.db_path)
        c = conn.cursor()
        slug = make_slug(data["title"])
        
        c.execute("SELECT id FROM books WHERE slug = ?", (slug,))
        row = c.fetchone()
        if row:
            book_id = row[0]
            c.execute("UPDATE books SET title = ?, author = ? WHERE id = ?", (data["title"], data["author"], book_id))
        else:
            c.execute("INSERT INTO books (title, author, slug) VALUES (?, ?, ?)", (data["title"], data["author"], slug))
            book_id = c.lastrowid
            
        c.execute("UPDATE lessons SET book_id = ?, lesson_title = ?, part = ? WHERE id = ?", 
                  (book_id, lesson_title, data["part"], data["lesson_id"]))
        conn.commit()
        conn.close()
        
        sync_to_github()
        user_states[user_id] = {"step": None, "data": {}}
        await event.respond("✅ Данные урока успешно обновлены!")

    elif state["step"] == "waiting_title":
        if "," not in text:
            await event.respond("Укажи название и автора через запятую.")
            return
        title, author = [x.strip() for x in text.split(",", 1)]
        state["data"]["title"] = title
        state["data"]["author"] = author
        state["step"] = "waiting_part"
        await event.respond("Теперь введи номер части (например: 1, 2 или 20):")
        
    elif state["step"] == "waiting_part":
        if not text.isdigit():
            await event.respond("Напиши номер части цифрами.")
            return 
        state["data"]["part"] = int(text)
        state["step"] = "waiting_lesson_title"
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
                [Button.inline("📝 Управление уроками", b"manage_lessons")],
                [Button.inline("📚 Управление книгами", b"manage_books")]
            ]
            await status_msg.edit(f"✅ Готово! Часть {data['part']} загружена.", buttons=buttons)
        except Exception as e:
            await status_msg.edit(f"❌ Ошибка: {e}")
            user_states[user_id] = None
