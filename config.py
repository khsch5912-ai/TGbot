from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Основные настройки
    bot_token: str = Field(..., alias="BOT_TOKEN")
    github_token: str = Field(..., alias="GITHUB_TOKEN")
    github_repo: str = Field(..., alias="GITHUB_REPO")
    db_path: str = Field("books.db", alias="DB_PATH")
    admin_ids_raw: str = Field("", alias="ADMIN_IDS")

    # Настройки для Pyrogram (MTProto) - снимают лимит 20 МБ
    api_id: int = Field(..., alias="API_ID")
    api_hash: str = Field(..., alias="API_HASH")

    @property
    def admin_ids(self) -> list[int]:
        if not self.admin_ids_raw: return []
        return [int(x.strip()) for x in self.admin_ids_raw.split(",") if x.strip()]

settings = Settings()
