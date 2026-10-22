import re
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from config import settings
from database import get_or_create_book, get_last_book, save_lesson, delete_lesson, save_video

# Добавляем новые состояния для видео
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

# ... код для /start (аудио) без изменений ...
# ... код для /delete без изменений ...

# --- НОВАЯ КОМАНДА: Добавление видеоурока ---
@router.message(Command("video"))
async def cmd_video(message: Message, state: FSMContext):
    if settings.admin_ids and message.from_user.id not in settings.admin_ids:
        await message.answer("⛔ У тебя нет прав.")
        return

    await state.clear()
    await message.answer("🎬 **Добавление видеоурока.**\n\n**Шаг 1:** Введи название видеоурока (например: *Введение в Фикх*):")
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
        f"✅ **Видеоурок успешно сохранен!**\n\n"
        f"🎬 Название: {data['title']}\n"
        f"🆔 **ID видео: `{video_id}`**",
        parse_mode=ParseMode.MARKDOWN
    )
    await state.clear()
