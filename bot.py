import re
import io
import os
import requests
from aiogram import F, Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import settings
from database import get_or_create_book, save_lesson, update_audio_url, get_last_book

# Импорты для скачивания с YouTube
import yt_dlp
from contextlib import redirect_stdout

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

# --- СОСТОЯНИЯ ---
class NewBookForm(StatesGroup):
    youtube_url = State()
    title_author = State()
    details = State()

class ExistingBookForm(StatesGroup):
    youtube_url = State()
    details = State()

# ВОТ ЭТА СТРОКА БЫЛА ПОТЕРЯНА. ОНА СОЗДАЕТ РОУТЕР.
router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1. Записать новую книгу", callback_data="flow_new")],
        [InlineKeyboardButton(text="2. Продолжить существующую", callback_data="flow_existing")]
    ])
    await message.answer("Выбери действие:\n1. Записать новую книгу\n2. Продолжить существующую", reply_markup=kb)

# ==========================================
# ВЕТКА 1: НОВАЯ КНИГА
# ==========================================
@router.callback_query(F.data == "flow_new")
async def new_book_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("Создание нового цикла записей.\n\nЭтап 1. Отправь мне ссылку на видео с YouTube.")
    await state.set_state(NewBookForm.youtube_url)

@router.message(NewBookForm.youtube_url)
async def new_book_url(message: Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("Это не похоже на ссылку. Отправь ссылку с YouTube:")
        return
    
    await state.update_data(youtube_url=url)
    await message.answer("Этап 2. Ссылка получена!\nНапиши название книги и автора через запятую.\nПример: Далиль ат-Талиб, Шейх Марʿи")
    await state.set_state(NewBookForm.title_author)

@router.message(NewBookForm.title_author)
async def new_book_title(message: Message, state: FSMContext):
    text = message.text.strip()
    if "," not in text:
        await message.answer("Пожалуйста, укажи название и автора через запятую.")
        return
    title, author = [x.strip() for x in text.split(",", 1)]
    await state.update_data(title=title, author=author)
    await message.answer("Этап 3. Отлично!\nТеперь напиши:\n1) Часть\n2) О чем запись\n(каждый пункт с новой строки)")
    await state.set_state(NewBookForm.details)

@router.message(NewBookForm.details)
async def new_book_details(message: Message, state: FSMContext, bot: Bot):
    lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    part = int(re.sub(r'\D', '', lines[0])) if lines and re.sub(r'\D', '', lines[0]) else 1
    description = lines[1] if len(lines) > 1 else "Без описания"
    data = await state.get_data()
    
    slug = make_slug(data["title"])
    book_id = get_or_create_book(data["title"], data["author"], slug)
    lesson_data = {"book_id": book_id, "lesson_number": part, "part": part, "description": description}
    lesson_id = save_lesson(lesson_data)
    
    await message.answer("⏳ Скачиваю аудио с YouTube и загружаю на хостинг...")
    await process_youtube_to_github(message, data["youtube_url"], lesson_id)
    await state.clear()

# ==========================================
# ВЕТКА 2: ПРОДОЛЖИТЬ СУЩЕСТВУЮЩУЮ
# ==========================================
@router.callback_query(F.data == "flow_existing")
async def existing_book_start(callback: CallbackQuery, state: FSMContext):
    last_book = get_last_book()
    if not last_book:
        await callback.message.edit_text("В базе нет ни одной книги. Сначала создай новую.")
        return
    await state.update_data(book_id=last_book["id"], title=last_book["title"])
    await callback.message.edit_text(f"Продолжаем книгу: {last_book['title']}\n\nОтправь мне ссылку на видео с YouTube.")
    await state.set_state(ExistingBookForm.youtube_url)

@router.message(ExistingBookForm.youtube_url)
async def existing_book_url(message: Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("Это не похоже на ссылку. Отправь ссылку с YouTube:")
        return
    
    await state.update_data(youtube_url=url)
    await message.answer("Ссылка получена! Теперь напиши:\n1) Часть\n2) О чем запись\n(каждый пункт с новой строки)")
    await state.set_state(ExistingBookForm.details)

@router.message(ExistingBookForm.details)
async def existing_book_details(message: Message, state: FSMContext, bot: Bot):
    lines = [l.strip() for l in message.text.split('\n') if l.strip()]
    part = int(re.sub(r'\D', '', lines[0])) if lines and re.sub(r'\D', '', lines[0]) else 1
    description = lines[1] if len(lines) > 1 else "Без описания"
    data = await state.get_data()
    
    lesson_data = {"book_id": data["book_id"], "lesson_number": part, "part": part, "description": description}
    lesson_id = save_lesson(lesson_data)
    
    await message.answer("⏳ Скачиваю аудио с YouTube и загружаю на хостинг...")
    await process_youtube_to_github(message, data["youtube_url"], lesson_id)
    await state.clear()

# ==========================================
# ФУНКЦИЯ СКАЧИВАНИЯ И ЗАГРУЗКИ
# ==========================================
async def process_youtube_to_github(message: Message, url: str, lesson_id: int):
    try:
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': '-', 
            'quiet': True,
            'no_warnings': True,
            'logtostderr': True,
        }

        buffer = io.BytesIO()
        
        # Запускаем скачивание
        with redirect_stdout(buffer), yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        
        audio_bytes = buffer.getvalue()
        
        if len(audio_bytes) < 1000:
            raise Exception("Не удалось скачать аудио (файл слишком мал). Возможно, видео недоступно.")

        # Сохраняем как webm (браузеры его играют)
        filename = f"lesson_{lesson_id}.webm" 
        
        audio_url = github_upload_audio(io.BytesIO(audio_bytes), filename)
        update_audio_url(lesson_id, audio_url)
        
        await message.answer("✅ Готово! Аудио скачано с YouTube и загружено на сайт.")
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при скачивании/загрузке: {e}")
