import re
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import settings
from database import get_or_create_book, get_last_book, save_lesson, delete_lesson, save_video
import sqlite3
import json

# Состояния
class LessonForm(StatesGroup):
    book_title = State()
    author = State()
    lesson_number = State()
    lesson_title = State()
    sections = State()
    
class VideoForm(StatesGroup):
    title = State()
    url = State()

router = Router()

# ============================================================
# 1. СТАРТ И ИНСТРУКЦИЯ
# ============================================================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if settings.admin_ids and message.from_user.id not in settings.admin_ids:
        await message.answer("⛔ У тебя нет прав.")
        return

    await state.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить новый контент", callback_data="menu_new")],
        [InlineKeyboardButton(text="📚 Посмотреть всё что есть", callback_data="menu_all")]
    ])
    
    text = (
        "👋 **Привет! Я бот для управления библиотекой.**\n\n"
        "Вот что я умею:\n"
        "🔹 `/new` — добавить новый аудиоурок или видеоурок.\n"
        "🔹 `/all` — посмотреть списки всех книг и видео.\n"
        "🔹 `/delete ID` — удалить урок по его ID.\n\n"
        "Выбери действие на кнопках ниже:"
    )
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@router.callback_query(F.data == "menu_new")
async def menu_new(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "Что именно ты хочешь добавить?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎧 Аудиоурок", callback_data="new_audio")],
            [InlineKeyboardButton(text="🎬 Видеоурок", callback_data="new_video")],
            [InlineKeyboardButton(text="🔙 Назад", callback_data="menu_back")]
        ])
    )

@router.callback_query(F.data == "menu_back")
async def menu_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить новый контент", callback_data="menu_new")],
        [InlineKeyboardButton(text="📚 Посмотреть всё что есть", callback_data="menu_all")]
    ])
    await callback.message.edit_text("Главное меню:", reply_markup=kb)

# ============================================================
# 2. ДОБАВЛЕНИЕ АУДИО (/new -> Аудиоурок)
# ============================================================
@router.callback_query(F.data == "new_audio")
async def start_new_audio(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🎧 **Добавление аудиоурока**\n\n**Шаг 1:** Введи название книги:")
    await state.set_state(LessonForm.book_title)

@router.message(LessonForm.book_title)
async def process_book_title(message: Message, state: FSMContext):
    await state.update_data(book_title=message.text.strip())
    await message.answer("**Шаг 2:** Введи автора книги:")
    await state.set_state(LessonForm.author)

@router.message(LessonForm.author)
async def process_author(message: Message, state: FSMContext):
    author = message.text.strip()
    data = await state.get_data()
    book_id = get_or_create_book(data["book_title"], author)
    
    await state.update_data(book_id=book_id, author=author)
    await message.answer("**Шаг 3:** Введи номер урока (можно 2️⃣0️⃣):")
    await state.set_state(LessonForm.lesson_number)

@router.message(LessonForm.lesson_number)
async def process_lesson_number(message: Message, state: FSMContext):
    text = re.sub(r'[^\d]', '', message.text.strip())
    if not text:
        await message.answer("❌ Не понял номер. Введи число:")
        return
    await state.update_data(lesson_number=int(text))
    await message.answer("**Шаг 4:** Введи название темы (например: *Книга намаза*):")
    await state.set_state(LessonForm.lesson_title)

@router.message(LessonForm.lesson_title)
async def process_lesson_title(message: Message, state: FSMContext):
    await state.update_data(lesson_title=message.text.strip())
    await message.answer("**Шаг 5:** Перечисли подразделы (каждый с новой строки):")
    await state.set_state(LessonForm.sections)

@router.message(LessonForm.sections)
async def process_sections(message: Message, state: FSMContext):
    sections = [s.strip() for s in message.text.strip().split('\n') if s.strip()]
    data = await state.get_data()
    data.update({
        "sections": sections,
        "uploaded_by": message.from_user.id,
        "uploaded_by_name": message.from_user.full_name,
        "audio_url": "" # пока пусто
    })
    
    try:
        lesson_id = save_lesson(data)
    except Exception as e:
        await message.answer(f"❌ Ошибка БД: {e}")
        await state.clear()
        return

    report = (
        f"✅ **Аудиоурок создан!**\n\n"
        f"📕 Книга: {data['book_title']}\n"
        f"📌 Урок: {data['lesson_number']}\n"
        f"🆔 **ID: `{lesson_id}`**\n\n"
        f"👇 Перейди на сайт, чтобы загрузить аудиофайл:"
    )
    
    await message.answer(report, parse_mode=ParseMode.MARKDOWN)
    await state.clear()

# ============================================================
# 3. ДОБАВЛЕНИЕ ВИДЕО (/new -> Видеоурок)
# ============================================================
@router.callback_query(F.data == "new_video")
async def start_new_video(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🎬 **Добавление видеоурока**\n\n**Шаг 1:** Введи название видео:")
    await state.set_state(VideoForm.title)

@router.message(VideoForm.title)
async def process_video_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("**Шаг 2:** Отправь ссылку на видео (YouTube / VK / Rutube):")
    await state.set_state(VideoForm.url)

@router.message(VideoForm.url)
async def process_video_url(message: Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("❌ Это не похоже на ссылку. Отправь ссылку (начиная с http):")
        return

    data = await state.get_data()
    try:
        video_id = save_video(
            title=data["title"], 
            url=url, 
            user_id=message.from_user.id, 
            user_name=message.from_user.full_name
        )
    except Exception as e:
        await message.answer(f"❌ Ошибка БД: {e}")
        await state.clear()
        return

    await message.answer(
        f"✅ **Видеоурок сохранен!**\n\n"
        f"🎬 Название: {data['title']}\n"
        f"🆔 **ID видео: `{video_id}`**",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.clear()

# ============================================================
# 4. ПРОСМОТР ВСЕГО КОНТЕНТА (/all)
# ============================================================
@router.message(Command("all"))
async def cmd_all(message: Message):
    if settings.admin_ids and message.from_user.id not in settings.admin_ids:
        return

    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()

    # 1. Собираем аудио
    c.execute("""
        SELECT b.title, b.author, l.id, l.lesson_number, l.lesson_title, l.audio_url 
        FROM lessons l
        JOIN books b ON l.book_id = b.id
        ORDER BY b.title, l.lesson_number
    """)
    lessons = c.fetchall()

    # 2. Собираем видео
    c.execute("SELECT id, title, video_url FROM videos ORDER BY id DESC")
    videos = c.fetchall()
    conn.close()

    text = "📚 **Твоя библиотека:**\n\n"

    # Выводим аудио
    text += "🎧 **Аудиоуроки:**\n"
    if not lessons:
        text += "_Пока нет ни одного аудиоурока._\n"
    else:
        current_book = ""
        for l in lessons:
            book_title, author, l_id, l_num, l_title, audio_url = l
            if book_title != current_book:
                text += f"\n📕 **{book_title}** ({author})\n"
                current_book = book_title
            
            status = "✅" if audio_url else "⏳ (нет аудио)"
            text += f"  • [ID: {l_id}] Урок {l_num}: {l_title} {status}\n"

    # Выводим видео
    text += "\n🎬 **Видеоуроки:**\n"
    if not videos:
        text += "_Пока нет ни одного видеоурока._\n"
    else:
        for v in videos:
            v_id, v_title, v_url = v
            text += f"  • [ID: {v_id}] [{v_title}]({v_url})\n"

    await message.answer(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)


# ============================================================
# 5. УДАЛЕНИЕ (/delete ID)
# ============================================================
@router.message(Command("delete"))
async def cmd_delete(message: Message):
    if settings.admin_ids and message.from_user.id not in settings.admin_ids:
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Используй: `/delete ID` (ID можно посмотреть в `/all`)")
        return
    
    try:
        lesson_id = int(args[1])
        if delete_lesson(lesson_id):
            await message.answer(f"🗑 Урок/видео с ID {lesson_id} удален из базы.")
        else:
            await message.answer(f"⚠️ Запись с ID {lesson_id} не найдена (или это видео, удаление видео пока не поддерживается).")
    except ValueError:
        await message.answer("ID должен быть числом.")
