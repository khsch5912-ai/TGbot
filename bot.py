import re
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import settings
from database import save_lesson

EMOJI_DIGIT_MAP = {
    '\u0030\ufe0f\u20e3': '0', '\u0031\ufe0f\u20e3': '1',
    '\u0032\ufe0f\u20e3': '2', '\u0033\ufe0f\u20e3': '3',
    '\u0034\ufe0f\u20e3': '4', '\u0035\ufe0f\u20e3': '5',
    '\u0036\ufe0f\u20e3': '6', '\u0037\ufe0f\u20e3': '7',
    '\u0038\ufe0f\u20e3': '8', '\u0039\ufe0f\u20e3': '9',
}

def emoji_digits_to_int(text: str) -> str:
    for emoji, digit in EMOJI_DIGIT_MAP.items():
        text = text.replace(emoji, digit)
    return text

class LessonForm(StatesGroup):
    book_title = State()
    author = State()
    lesson_number = State()
    lesson_title = State()
    sections = State()

router = Router()

@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    if settings.admin_ids and message.from_user.id not in settings.admin_ids:
        await message.answer("⛔ У тебя нет прав.")
        return

    await state.clear()
    await message.answer("👋 Давай добавим новый урок.\n\n**Шаг 1 из 5:** Введи название книги:")
    await state.set_state(LessonForm.book_title)

@router.message(LessonForm.book_title)
async def process_book_title(message: Message, state: FSMContext):
    await state.update_data(book_title=message.text.strip())
    await message.answer("**Шаг 2 из 5:** Введи автора книги:")
    await state.set_state(LessonForm.author)

@router.message(LessonForm.author)
async def process_author(message: Message, state: FSMContext):
    await state.update_data(author=message.text.strip())
    await message.answer("**Шаг 3 из 5:** Введи номер урока (можно эмодзи 2️⃣0️⃣):")
    await state.set_state(LessonForm.lesson_number)

@router.message(LessonForm.lesson_number)
async def process_lesson_number(message: Message, state: FSMContext):
    text = emoji_digits_to_int(message.text.strip())
    digits = re.sub(r"\D", "", text)
    
    if not digits:
        await message.answer("❌ Не понял номер. Введи число (например: 20):")
        return
        
    await state.update_data(lesson_number=int(digits))
    await message.answer("**Шаг 4 из 5:** Введи название раздела / темы (например: *Книга намаза*):")
    await state.set_state(LessonForm.lesson_title)

@router.message(LessonForm.lesson_title)
async def process_lesson_title(message: Message, state: FSMContext):
    await state.update_data(lesson_title=message.text.strip())
    await message.answer("**Шаг 5 из 5:** Перечисли подразделы (каждый с новой строки):")
    await state.set_state(LessonForm.sections)

@router.message(LessonForm.sections)
async def process_sections(message: Message, state: FSMContext):
    sections_raw = message.text.strip()
    sections = [s.strip() for s in sections_raw.split('\n') if s.strip()]
    
    data = await state.get_data()
    data.update({
        "sections": sections,
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
        f"✅ **Урок создан в БД!**\n\n"
        f"📕 Книга: {data['book_title']}\n"
        f"📘 Автор: {data['author']}\n"
        f"📌 Урок: {data['lesson_number']}\n"
        f"📖 Тема: {data['lesson_title']}\n"
        f"📝 Разделы:\n" + "\n".join([f"  - {s}" for s in sections]) + "\n\n"
        f"🆔 **ID урока: `{lesson_id}`**\n\n"
        f"👇 Теперь перейди на сайт-загрузчик и загрузи аудио для этого ID."
    )
    
    await message.answer(report, parse_mode=ParseMode.MARKDOWN)
    await state.clear()
