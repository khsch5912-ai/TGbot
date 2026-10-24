from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = Field(..., alias="BOT_TOKEN")
    github_token: str = Field(..., alias="GITHUB_TOKEN")
    github_repo: str = Field(..., alias="GITHUB_REPO")
    db_path: str = Field("books.db", alias="DB_PATH")
    admin_ids_raw: str = Field("", alias="ADMIN_IDS")
    admin_site_url: str = Field("", alias="ADMIN_SITE_URL")

    @property
    def admin_ids(self) -> list[int]:
        if not self.admin_ids_raw: return []
        return [int(x.strip()) for x in self.admin_ids_raw.split(",") if x.strip()]

settings = Settings()
