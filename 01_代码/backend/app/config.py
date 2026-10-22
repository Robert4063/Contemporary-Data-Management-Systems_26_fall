from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    mongo_uri: str = 'mongodb://127.0.0.1:27017'
    mongo_db: str = 'questionnaire_stage1'
    app_secret: str = Field(default='local-development-change-this-secret-32chars', min_length=32)
    session_hours: int = Field(default=24, ge=1, le=720)
