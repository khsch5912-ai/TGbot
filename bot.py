import re
import io
import requests
import asyncio
import aiohttp
from aiogram import F, Router, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
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

# ==========================================
# ПРАВИЛЬНЫЙ АЛГОРИТМ СКАЧИВАНИЯ (БЕЗ ЛИМИТА)
# ==========================================
async def download_file_with_progress(bot: Bot, file_id: str, file_size: int, message: Message) -> io.BytesIO:
    file_info = await bot.get_file(file_id)
    file_path = file_info.file_path
    url = f"https://api.telegram.org/file/bot{settings.bot_token}/{file_path}"

    buffer = io.BytesIO()
    downloaded = 0
    status_message = await message.answer("⏳ Скачиваю файл: 0%")

    async with aiohttp.ClientSession() as session:
        # Качаем кусками по 10 МБ, чтобы не превышать лимиты и показывать прогресс
        chunk_size = 10 * 1024 * 1024 
        while downloaded < file_size:
            headers = {"Range": f"bytes={downloaded}-{downloaded + chunk_size - 1}"}
            async with session.get(url, headers=headers) as resp:
                if resp.status not in (200, 206):
                    raise Exception(f"Ошибка сервера Telegram: {resp.status}")
                
                chunk = await resp.read()
                buffer.write(chunk)
                downloaded += len(chunk)
                
                # Обновляем индикатор каждые 20%
                percent = int((downloaded / file_size) * 100)
                if percent % 20 == 0:
                    try:
                        await status_message.edit_text(f"⏳ Скачиваю файл: {percent}%")
                    except:
                        pass # Игнорируем ошибки редактирования, если сообщение слишком старое

    await status_message.edit_text("✅ Файл скачан! Загружаю на GitHub...")
    buffer.seek(0)
    return buffer

# --- СОСТОЯНИЯ ---
class NewBookForm(StatesGroup):
    audio_file = State()
    title_author = State()
    lesson_title = State()

class ExistingBookForm(StatesGroup):
    audio_file = State()
    lesson_title = State()

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
    await callback.message.edit_text("Создание нового цикла.\n\nОтправь мне аудиофайл (можно больше 20 МБ, я сам разберусь).")
    await state.set_state(NewBookForm.audio_file)

@router.message(NewBookForm.audio_file, F.voice | F.audio | F.document)
async def new_book_audio(message: Message, state: FSMContext, bot: Bot):
    if message.voice: 
        file_id, file_size = message.voice.file_id, message.voice.file_size
    elif message.audio: 
        file_id, file_size = message.audio.file_id, message.audio.file_size
    else: 
        file_id, file_size = message.document.file_id, message.document.file_size
    
    try:
        # Скачиваем файл с индикатором прогресса
        file_data = await download_file_with_progress(bot, file_id, file_size, message)
        await state.update_data(audio_data=file_data.getvalue())
        
        await message.answer("Аудио готово!\nНапиши название книги и автора через запятую.\nПример: Далиль ат-Талиб, Шейх Марʿи")
        await state.set_state(NewBookForm.title_author)
    except Exception as e:
        await message.answer(f"❌ Ошибка при скачивании: {e}")
        await state.clear()

@router.message(NewBookForm.title_author)
async def new_book_title(message: Message, state: FSMContext):
    text = message.text.strip()
    if "," not in text:
        await message.answer("Пожалуйста, укажи название и автора через запятую.")
        return
    title, author = [x.strip() for x in text.split(",", 1)]
    await state.update_data(title=title, author=author)
    await message.answer("Теперь напиши название этого урока (например: Урок 20. Книга намаза):")
    await state.set_state(NewBookForm.lesson_title)

@router.message(NewBookForm.lesson_title)
async def new_book_lesson_title(message: Message, state: FSMContext):
    lesson_title = message.text.strip()
    data = await state.get_data()
    
    slug = make_slug(data["title"])
    book_id = get_or_create_book(data["title"], data["author"], slug)
    lesson_id = save_lesson({"book_id": book_id, "lesson_title": lesson_title})
    
    status_msg = await message.answer("⏳ Загружаю на GitHub...")
    try:
        audio_url = github_upload_audio(io.BytesIO(data["audio_data"]), f"lesson_{lesson_id}.mp3")
        update_audio_url(lesson_id, audio_url)
        await status_msg.edit_text("✅ Готово! Аудио загружено на сайт.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")
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
    await callback.message.edit_text(f"Продолжаем книгу: {last_book['title']}\n\nОтправь мне аудиофайл.")
    await state.set_state(ExistingBookForm.audio_file)

@router.message(ExistingBookForm.audio_file, F.voice | F.audio | F.document)
async def existing_book_audio(message: Message, state: FSMContext, bot: Bot):
    if message.voice: 
        file_id, file_size = message.voice.file_id, message.voice.file_size
    elif message.audio: 
        file_id, file_size = message.audio.file_id, message.audio.file_size
    else: 
        file_id, file_size = message.document.file_id, message.document.file_size
        
    try:
        file_data = await download_file_with_progress(bot, file_id, file_size, message)
        await state.update_data(audio_data=file_data.getvalue())
        
        await message.answer("Аудио готово!\nНапиши название этого урока (например: Урок 20. Книга намаза):")
        await state.set_state(ExistingBookForm.lesson_title)
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
        await state.clear()

@router.message(ExistingBookForm.lesson_title)
async def existing_book_lesson_title(message: Message, state: FSMContext):
    lesson_title = message.text.strip()
    data = await state.get_data()
    
    lesson_id = save_lesson({"book_id": data["book_id"], "lesson_title": lesson_title})
    
    status_msg = await message.answer("⏳ Загружаю на GitHub...")
    try:
        audio_url = github_upload_audio(io.BytesIO(data["audio_data"]), f"lesson_{lesson_id}.mp3")
        update_audio_url(lesson_id, audio_url)
        await status_msg.edit_text("✅ Готово! Аудио загружено на сайт.")
    except Exception as e:
        await status_msg.edit_text(f"❌ Ошибка: {e}")
    await state.clear()
