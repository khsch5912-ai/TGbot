import asyncio
import threading
from flask import Flask
from pyrogram import Client, idle

# ==================== Конфигурация ====================
# Замените на ваши реальные API_ID, API_HASH и BOT_TOKEN
API_ID = 123456  # Ваш api_id (целое число)
API_HASH = "your_api_hash_here"
BOT_TOKEN = "your_bot_token_here"

# ==================== Часть Flask ====================
flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "Bot is alive!"

def run_flask():
    """Запуск Flask в отдельном потоке, чтобы не блокировать главный поток"""
    # На Render обычно используется порт 10000, вы также можете использовать переменную окружения PORT
    flask_app.run(host='0.0.0.0', port=10000, use_reloader=False)

# ==================== Часть Pyrogram ====================
# Создание экземпляра Client
app = Client(
    "my_bot",  # Имя сессии, можно указать произвольно
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Здесь вы можете добавить ваши обработчики (handlers)
# Например:
# @app.on_message(filters.command("start"))
# async def start_handler(client, message):
#     await message.reply("Hello!")

# ==================== Главный запуск ====================
async def main():
    # 1. Запуск Flask в фоновом потоке (daemon=True означает автоматическое завершение при выходе из главного потока)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    print("Flask сервер запущен в фоновом потоке.")

    # 2. Запуск клиента Pyrogram
    await app.start()
    print("Бот успешно подключен к Telegram!")

    # 3. Ключевой момент: вызов правильной функции idle(), а не app.idle()
    # Она будет блокировать главный поток, позволяя боту продолжать получать сообщения
    await idle()

    # 4. Корректная остановка клиента после получения сигнала (например, Ctrl+C)
    await app.stop()
    print("Бот остановлен.")

if __name__ == "__main__":
    # Запуск асинхронной главной функции
    asyncio.run(main())
