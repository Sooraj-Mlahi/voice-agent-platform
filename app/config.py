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

    # Latency optimisation knobs (overridable via env without code deploy)
    # max_tokens caps LLM output length → shorter TTS synthesis time
    agent_max_tokens: int = 200
    # responsiveness (0.0–1.0): how quickly agent fires after user stops speaking
    agent_responsiveness: float = 1.0
    # ElevenLabs voice model — eleven_turbo_v2_5 is ~40% lower latency than
    # eleven_multilingual_v2 with comparable quality for conversational use
    agent_voice_model: str = "eleven_turbo_v2_5"


settings = Settings()
