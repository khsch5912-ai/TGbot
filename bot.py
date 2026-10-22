import re
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import settings
from database import get_or_create_book, get_last_book, save_lesson, delete_lesson

class LessonForm(StatesGroup):
    book_title = State()
    author = State()
    lesson_number = State()
    lesson_title = State()
    sections = State()
    video_url = State()

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if settings.admin_ids and message.from_user.id not in settings.admin_ids:
        await message.answer("⛔ У тебя нет прав.")
        return

    await state.clear()
    last_book = get_last_book()
    
    if last_book:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"▶️ Продолжить «{last_book['title']}»", callback_data="continue_book")],
            [InlineKeyboardButton(text="➕ Новая книга", callback_data="new_book")]
        ])
        await message.answer("Хотите продолжить прошлую книгу или начать новую?", reply_markup=kb)
    else:
        await message.answer("👋 Давай добавим первую книгу.\n\n**Шаг 1:** Введи название книги:")
        await state.set_state(LessonForm.book_title)

@router.callback_query(F.data == "new_book")
async def new_book_click(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("**Шаг 1:** Введи название книги:")
    await state.set_state(LessonForm.book_title)

@router.callback_query(F.data == "continue_book")
async def continue_book_click(callback: CallbackQuery, state: FSMContext):
    last_book = get_last_book()
    await state.update_data(book_id=last_book["id"], book_title=last_book["title"], author=last_book["author"])
    await callback.message.edit_text(f"Продолжаем книгу «{last_book['title']}».\n\n**Шаг 2:** Введи номер урока:")
    await state.set_state(LessonForm.lesson_number)

# --- Новая книга ---
@router.message(LessonForm.book_title)
async def process_book_title(message: Message, state: FSMContext):
    await state.update_data(book_title=message.text.strip())
    await message.answer("**Шаг 2:** Введи автора книги:")
    await state.set_state(LessonForm.author)

@router.message(LessonForm.author)
async def process_author(message: Message, state: FSMContext):
    author = message.text.strip()
    data = await state.get_data()
    
    # Создаем или достаем книгу
    book_id = get_or_create_book(data["book_title"], author)
    
    await state.update_data(book_id=book_id, author=author)
    await message.answer("**Шаг 3:** Введи номер урока (можно 2️⃣0️⃣):")
    await state.set_state(LessonForm.lesson_number)

# --- Общая ветка ---
@router.message(LessonForm.lesson_number)
async def process_lesson_number(message: Message, state: FSMContext):
    text = message.text.strip()
    # Убираем эмодзи-цифры
    text = re.sub(r'[^\d]', '', text) 
    
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
    await state.update_data(sections=sections)
    await message.answer("**Шаг 6:** Если есть видеоурок, отправь ссылку на YouTube (или напиши «Нет»):")
    await state.set_state(LessonForm.video_url)

@router.message(LessonForm.video_url)
async def process_video_url(message: Message, state: FSMContext):
    video_url = message.text.strip()
    if video_url.lower() in ["нет", "no", "-"]:
        video_url = ""
        
    data = await state.get_data()
    data.update({
        "video_url": video_url,
        "uploaded_by": message.from_user.id,
        "uploaded_by_name": message.from_user.full_name,
    })
    
    try:
        lesson_id = save_lesson(data)
    except Exception as e:
        await message.answer(f"❌ Ошибка БД: {e}")
        await state.clear()
        return

    report = (
        f"✅ **Урок сохранен!**\n\n"
        f"📕 Книга: {data['book_title']}\n"
        f"📌 Урок: {data['lesson_number']}\n"
        f"🆔 **ID: `{lesson_id}`**\n\n"
        f"👇 Перейди на сайт, чтобы загрузить аудио, или удали урок командой `/delete {lesson_id}`."
    )
    
    await message.answer(report, parse_mode=ParseMode.MARKDOWN)
    await state.clear()

# --- Удаление ---
@router.message(Command("delete"))
async def cmd_delete(message: Message):
    if settings.admin_ids and message.from_user.id not in settings.admin_ids:
        return
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Используй: `/delete ID_урока`")
        return
    
    try:
        lesson_id = int(args[1])
        if delete_lesson(lesson_id):
            await message.answer(f"🗑 Урок ID {lesson_id} удален из базы.")
        else:
            await message.answer(f"⚠️ Урок с ID {lesson_id} не найден.")
    except ValueError:
        await message.answer("ID должен быть числом.")
