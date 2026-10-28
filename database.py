from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str = Field(..., alias="BOT_TOKEN")
    github_token: str = Field(..., alias="GITHUB_TOKEN")
    github_repo: str = Field(..., alias="GITHUB_REPO")
    db_path: str = Field("books.db", alias="DB_PATH")
    
    api_id: int = Field(..., alias="API_ID")
    api_hash: str = Field(..., alias="API_HASH")

settings = Settings()
