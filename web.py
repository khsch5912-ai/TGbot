import re
import io
import requests
from telethon import TelegramClient, events, Button
from config import settings
from database import get_or_create_book, save_lesson, update_audio_url, get_last_book, delete_lesson, get_all_lessons

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

client = TelegramClient('audio_book_session', settings.api_id, settings.api_hash)
user_states = {}

# ==========================================
# ГЛАВНОЕ МЕНЮ
# ==========================================
@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    user_id = event.sender_id
    user_states[user_id] = {"step": None, "data": {}}
    
    buttons = [
        [Button.inline("1. Записать новую книгу", b"flow_new")],
        [Button.inline("2. Продолжить существующую", b"flow_existing")],
        [Button.inline("📚 Управление библиотекой", b"manage_all")]
    ]
    await event.respond("Выбери действие:", buttons=buttons)

# ==========================================
# УПРАВЛЕНИЕ БИБЛИОТЕКОЙ (УДАЛЕНИЕ)
# ==========================================
@client.on(events.NewMessage(pattern='/delete'))
@client.on(events.CallbackQuery(data=b"manage_all"))
async def manage_all(event):
    lessons = get_all_lessons()
    if not lessons:
        text = "В базе пока нет ни одного урока."
        if isinstance(event, events.CallbackQuery.Event):
            await event.edit(text)
        else:
            await event.respond(text)
        return
    
    buttons = []
    current_book = ""
    
    for l in lessons:
        if l['book_title'] != current_book:
            current_book = l['book_title']
            buttons.append([Button.inline(f"📕 {current_book}", b"ignore")])
        
        buttons.append([
            Button.inline(f"🗑 {l['lesson_title']} (Часть {l['part']})", f"select_del_{l['id']}".encode('utf-8'))
        ])
    
    buttons.append([Button.inline("⬅️ В главное меню", b"back_start")])
    
    text = "Выбери урок, который хочешь удалить:"
    if isinstance(event, events.CallbackQuery.Event):
        await event.edit(text, buttons=buttons)
    else:
        await event.respond(text, buttons=buttons)

@client.on(events.CallbackQuery(pattern=b"select_del_(\\d+)"))
async def confirm_delete(event):
    lesson_id = int(event.pattern_match.group(1).decode('utf-8'))
    
    buttons = [
        [Button.inline("✅ Да, удалить", f"do_del_{lesson_id}".encode('utf-8'))],
        [Button.inline("❌ Отмена", b"manage_all")]
    ]
    await event.edit("Вы уверены, что хотите удалить этот урок из базы?", buttons=buttons)

@client.on(events.CallbackQuery(pattern=b"do_del_(\\d+)"))
async def execute_delete(event):
    lesson_id = int(event.pattern_match.group(1).decode('utf-8'))
    
    if delete_lesson(lesson_id):
        await event.answer("Урок удален!", alert=True)
        await manage_all(event)
    else:
        await event.answer("Ошибка: урок не найден.", alert=True)

@client.on(events.CallbackQuery(data=b"back_start"))
async def back_start(event):
    user_id = event.sender_id
    user_states[user_id] = {"step": None, "data": {}}
    buttons = [
        [Button.inline("1. Записать новую книгу", b"flow_new")],
        [Button.inline("2. Продолжить существующую", b"flow_existing")],
        [Button.inline("📚 Управление библиотекой", b"manage_all")]
    ]
    await event.edit("Выбери действие:", buttons=buttons)

# ==========================================
# ДОБАВЛЕНИЕ
# ==========================================
@client.on(events.CallbackQuery(data=b"flow_new"))
async def flow_new(event):
    user_states[event.sender_id] = {"step": "waiting_audio", "data": {"is_new": True}}
    await event.edit("Создание нового цикла.\n\nОтправь мне аудиофайл (любого размера).")

@client.on(events.CallbackQuery(data=b"flow_existing"))
async def flow_existing(event):
    last_book = get_last_book()
    if not last_book:
        await event.edit("В базе нет ни одной книги.")
        return
    user_states[event.sender_id] = {"step": "waiting_audio", "data": {"is_new": False, "book_id": last_book["id"], "title": last_book["title"]}}
    await event.edit(f"Продолжаем книгу: {last_book['title']}\n\nОтправь мне аудиофайл.")

@client.on(events.NewMessage(func=lambda e: e.audio or e.voice or e.document))
async def handle_audio(event):
    user_id = event.sender_id
    state = user_states.get(user_id)
    
    if not state or state.get("step") != "waiting_audio":
        return 
    
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
        user_states[user_id]["step"] = "waiting_part"
        await event.respond("Теперь введи номер части (например: 1, 2 или 20):")
        
    elif state["step"] == "waiting_part":
        part_num = re.sub(r'\D', '', text)
        if not part_num:
            await event.respond("Пожалуйста, напиши номер части цифрами (например: 1):")
            return
        user_states[user_id]["data"]["part"] = int(part_num)
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
