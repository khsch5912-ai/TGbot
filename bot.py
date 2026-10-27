import re
import io
import requests
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

# --- СОСТОЯНИЯ ---
class NewBookForm(StatesGroup):
    audio_parts = State()
    title_author = State()
    lesson_title = State()

class ExistingBookForm(StatesGroup):
    audio_parts = State()
    lesson_title = State()

router = Router()
# Временное хранилище для частей (в оперативной памяти)
audio_chunks = {}

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
    await callback.message.edit_text("Создание нового цикла.\n\nОтправь мне аудиофайл (или его части по очереди, до 20 МБ каждая).")
    await state.set_state(NewBookForm.audio_parts)
    audio_chunks[callback.from_user.id] = []

@router.message(NewBookForm.audio_parts, F.voice | F.audio | F.document)
async def new_book_audio(message: Message, state: FSMContext, bot: Bot):
    if message.voice: file_id = message.voice.file_id
    elif message.audio: file_id = message.audio.file_id
    else: file_id = message.document.file_id
    
    file_obj = await bot.download(file_id)
    audio_chunks[message.from_user.id].append(file_obj.getvalue())
    
    await message.answer(f"Часть {len(audio_chunks[message.from_user.id])} получена! Можешь отправить следующую, или нажми кнопку ниже, если это была последняя часть.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить загрузку", callback_data="finish_upload")]
    ])
    await message.answer("Когда закончишь, нажми кнопку:", reply_markup=kb)

@router.callback_query(F.data == "finish_upload")
async def finish_upload(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in audio_chunks or not audio_chunks[user_id]:
        await callback.message.edit_text("Вы не отправили ни одного файла.")
        return

    final_file = io.BytesIO()
    for chunk in audio_chunks[user_id]:
        final_file.write(chunk)
    final_file.seek(0)
    
    await state.update_data(full_audio=final_file.getvalue())
    audio_chunks[user_id] = []
    
    await callback.message.edit_text("Аудио собрано!\nНапиши название книги и автора через запятую.\nПример: Далиль ат-Талиб, Шейх Марʿи")
    await state.set_state(NewBookForm.title_author)

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
async def new_book_lesson_title(message: Message, state: FSMContext, bot: Bot):
    lesson_title = message.text.strip()
    data = await state.get_data()
    
    slug = make_slug(data["title"])
    book_id = get_or_create_book(data["title"], data["author"], slug)
    lesson_id = save_lesson({"book_id": book_id, "lesson_title": lesson_title})
    
    await message.answer("⏳ Загружаю склеенное аудио на хостинг...")
    try:
        audio_url = github_upload_audio(io.BytesIO(data["full_audio"]), f"lesson_{lesson_id}.mp3")
        update_audio_url(lesson_id, audio_url)
        await message.answer("✅ Готово! Аудио загружено на сайт.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
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
    await callback.message.edit_text(f"Продолжаем книгу: {last_book['title']}\n\nОтправь мне аудиофайл (или его части).")
    await state.set_state(ExistingBookForm.audio_parts)
    audio_chunks[callback.from_user.id] = []

@router.message(ExistingBookForm.audio_parts, F.voice | F.audio | F.document)
async def existing_book_audio(message: Message, state: FSMContext, bot: Bot):
    if message.voice: file_id = message.voice.file_id
    elif message.audio: file_id = message.audio.file_id
    else: file_id = message.document.file_id
    
    file_obj = await bot.download(file_id)
    audio_chunks[message.from_user.id].append(file_obj.getvalue())
    
    await message.answer(f"Часть {len(audio_chunks[message.from_user.id])} получена! Отправь следующую или заверши загрузку.")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Завершить загрузку", callback_data="finish_upload_existing")]
    ])
    await message.answer("Когда закончишь:", reply_markup=kb)

@router.callback_query(F.data == "finish_upload_existing")
async def finish_upload_existing(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in audio_chunks or not audio_chunks[user_id]:
        await callback.message.edit_text("Вы не отправили ни одного файла.")
        return

    final_file = io.BytesIO()
    for chunk in audio_chunks[user_id]:
        final_file.write(chunk)
    final_file.seek(0)
    
    await state.update_data(full_audio=final_file.getvalue())
    audio_chunks[user_id] = []
    
    await callback.message.edit_text("Аудио собрано!\nНапиши название этого урока (например: Урок 20. Книга намаза):")
    await state.set_state(ExistingBookForm.lesson_title)

@router.message(ExistingBookForm.lesson_title)
async def existing_book_lesson_title(message: Message, state: FSMContext, bot: Bot):
    lesson_title = message.text.strip()
    data = await state.get_data()
    
    lesson_id = save_lesson({"book_id": data["book_id"], "lesson_title": lesson_title})
    
    await message.answer("⏳ Загружаю склеенное аудио на хостинг...")
    try:
        audio_url = github_upload_audio(io.BytesIO(data["full_audio"]), f"lesson_{lesson_id}.mp3")
        update_audio_url(lesson_id, audio_url)
        await message.answer("✅ Готово! Аудио загружено на сайт.")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {e}")
    await state.clear()
