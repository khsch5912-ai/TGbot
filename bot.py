# ... импорты те же
from config import settings

# ... код парсинга, БД, GitHub — тот же

# Где раньше было BOT_TOKEN, GITHUB_TOKEN и т.д. — теперь:
# settings.bot_token
# settings.github_token
# settings.github_repo
# settings.db_path
# settings.admin_ids

# Пример в github_upload_audio:
headers = {
    "Authorization": f"token {settings.github_token}",
    "Accept": "application/vnd.github.v3+json",
}
# ...
f"https://api.github.com/repos/{settings.github_repo}/releases"

# Пример в init_db:
sqlite3.connect(settings.db_path)

# Пример в handle_audio:
if settings.admin_ids and message.from_user.id not in settings.admin_ids:
    ...

# Пример в main():
bot = Bot(token=settings.bot_token)