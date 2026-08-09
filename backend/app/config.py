from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    deployment_mode: str = "local"
    ai_provider: str = "openai"
    ai_fallback_to_deepseek: bool = True
    openai_api_key: str | None = None
    openai_model: str = "gpt-5"
    deepseek_api_key: str | None = None
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    ai_request_timeout_seconds: float = 90.0
    target_english_variant: str = "Australian English"
    default_sc_word_target: int = 350
    database_url: str = "sqlite:///./data/job_assistant.db"
    frontend_origin: str = "http://localhost:3000"
    supabase_url: str | None = None
    supabase_jwt_issuer: str | None = None
    supabase_jwt_audience: str = "authenticated"
    daily_pack_limit_per_user: int = 3
    monthly_pack_limit_global: int = 50
    allow_public_signup: bool = False
    enable_tailored_resume: bool = True
    enable_cover_letter: bool = True
    enable_selection_criteria: bool = True
    enable_ats_analysis: bool = True
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
