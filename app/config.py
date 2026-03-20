from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    retell_api_key: str
    openrouter_api_key: str
    supabase_url: str
    supabase_service_key: str
    supabase_jwt_secret: str = ""


settings = Settings()
