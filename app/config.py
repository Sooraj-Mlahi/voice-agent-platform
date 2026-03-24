from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    retell_api_key: str
    openrouter_api_key: str
    supabase_url: str
    supabase_service_key: str
    supabase_jwt_secret: str = ""
    webhook_base_url: str = ""
    dev_mode: bool = False

    # Redis — for multi-instance ConversationState
    redis_url: str = "redis://localhost:6379/0"

    # Barge-in / VAD tuning (overridable without code deploy)
    interruption_sensitivity: float = 0.9
    backchannel_frequency: float = 0.45

    # Silence handling defaults
    silence_timeout_seconds: int = 10
    max_silence_prompts: int = 2


settings = Settings()
