import re
import sqlite3
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import settings
from database import get_or_create_book, save_lesson, delete_lesson, save_video

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

# === СТАРТ ===
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить контент", callback_data="menu_new")],
        [InlineKeyboardButton(text="📚 Посмотреть всё", callback_data="menu_all")]
    ])
    text = (
        "👋 **Привет! Я бот управления библиотекой.**\n\n"
        "🔹 `/new` — добавить аудио или видео.\n"
        "🔹 `/all` — посмотреть всё, что есть.\n"
        "🔹 `/delete ID` — удалить урок.\n\n"
        "Выбери действие:"
    )
    await message.answer(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@router.callback_query(F.data == "menu_new")
async def menu_new(callback: CallbackQuery):
    await callback.message.edit_text(
        "Что добавим?",
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
        [InlineKeyboardButton(text="➕ Добавить контент", callback_data="menu_new")],
        [InlineKeyboardButton(text="📚 Посмотреть всё", callback_data="menu_all")]
    ])
    await callback.message.edit_text("Главное меню:", reply_markup=kb)

@router.message(Command("new"))
async def cmd_new(message: Message):
    await message.answer("Что добавим?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎧 Аудиоурок", callback_data="new_audio")],
        [InlineKeyboardButton(text="🎬 Видеоурок", callback_data="new_video")]
    ]))

# === ДОБАВЛЕНИЕ АУДИО ===
@router.callback_query(F.data == "new_audio")
async def start_new_audio(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🎧 **Шаг 1:** Введи название книги:")
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
    await message.answer("**Шаг 4:** Введи название темы:")
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
    data.update({"sections": sections, "uploaded_by": message.from_user.id, "uploaded_by_name": message.from_user.full_name})
    try:
        lesson_id = save_lesson(data)
    except Exception as e:
        await message.answer(f"❌ Ошибка БД: {e}")
        await state.clear()
        return
    await message.answer(f"✅ **Урок создан!**\nID: `{lesson_id}`\nЗайди на `/admin` сайта, чтобы загрузить аудио.", parse_mode=ParseMode.MARKDOWN)
    await state.clear()

# === ДОБАВЛЕНИЕ ВИДЕО ===
@router.callback_query(F.data == "new_video")
async def start_new_video(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("🎬 **Шаг 1:** Введи название видео:")
    await state.set_state(VideoForm.title)

@router.message(VideoForm.title)
async def process_video_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await message.answer("**Шаг 2:** Отправь ссылку на видео (YouTube):")
    await state.set_state(VideoForm.url)

@router.message(VideoForm.url)
async def process_video_url(message: Message, state: FSMContext):
    url = message.text.strip()
    if not url.startswith("http"):
        await message.answer("❌ Это не ссылка. Отправь ссылку (http...):")
        return
    data = await state.get_data()
    try:
        save_video(title=data["title"], url=url, user_id=message.from_user.id, user_name=message.from_user.full_name)
    except Exception as e:
        await message.answer(f"❌ Ошибка БД: {e}")
        await state.clear()
        return
    await message.answer(f"✅ **Видеоурок сохранен!**", parse_mode=ParseMode.MARKDOWN)
    await state.clear()

# === ВСЕ КОНТЕНТ ===
@router.message(Command("all"))
async def cmd_all(message: Message):
    conn = sqlite3.connect(settings.db_path)
    c = conn.cursor()
    c.execute("SELECT b.title, b.author, l.id, l.lesson_number, l.lesson_title, l.audio_url FROM lessons l JOIN books b ON l.book_id = b.id ORDER BY b.title, l.lesson_number")
    lessons = c.fetchall()
    c.execute("SELECT id, title, video_url FROM videos ORDER BY id DESC")
    videos = c.fetchall()
    conn.close()

    text = "📚 **Твоя библиотека:**\n\n🎧 **Аудиоуроки:**\n"
    if not lessons:
        text += "_Нет уроков._\n"
    else:
        current_book = ""
        for l in lessons:
            if l[0] != current_book:
                text += f"\n📕 **{l[0]}** ({l[1]})\n"
                current_book = l[0]
            status = "✅" if l[5] else "⏳"
            text += f"  • [ID: {l[2]}] Урок {l[3]}: {l[4]} {status}\n"

    text += "\n🎬 **Видеоуроки:**\n"
    if not videos:
        text += "_Нет видео._\n"
    else:
        for v in videos:
            text += f"  • [ID: {v[0]}] [{v[1]}]({v[2]})\n"
    await message.answer(text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)

# === УДАЛЕНИЕ ===
@router.message(Command("delete"))
async def cmd_delete(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Используй: `/delete ID`")
        return
    try:
        lesson_id = int(args[1])
        if delete_lesson(lesson_id):
            await message.answer(f"🗑 Урок {lesson_id} удален.")
        else:
            await message.answer(f"⚠️ Урок {lesson_id} не найден.")
    except ValueError:
        await message.answer("ID должен быть числом.")
