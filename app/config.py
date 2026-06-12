from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    gigachat_auth_key: str
    gigachat_scope: str
    gigachat_model: str
    gigachat_verify_ssl: bool
    supabase_url: str
    supabase_key: str


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    load_dotenv()

    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required in .env")

    return Settings(
        telegram_bot_token=telegram_bot_token,
        gigachat_auth_key=os.getenv("GIGACHAT_AUTH_KEY", "").strip(),
        gigachat_scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS").strip(),
        gigachat_model=os.getenv("GIGACHAT_MODEL", "GigaChat").strip(),
        gigachat_verify_ssl=_bool_env("GIGACHAT_VERIFY_SSL", False),
        supabase_url=os.getenv("SUPABASE_URL", "").strip(),
        supabase_key=os.getenv("SUPABASE_KEY", "").strip(),
    )
