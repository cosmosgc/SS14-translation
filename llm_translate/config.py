from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    source_locale_dir: Path
    target_locale_dir: Path
    source_lang: str = "en"
    target_lang: str = "pt-BR"
    cache_dir: Path = Path("./.cache")
    llm_api_base: str = "http://127.0.0.1:1234/v1"
    llm_api_key: str = "not-needed"
    llm_model: str = "local-model"
    llm_temperature: float = 0.1
    llm_max_tokens: int = 8192
    llm_timeout: int = 300


def load_config() -> Config:
    script_dir = Path(__file__).resolve().parent
    parent_dir = script_dir.parent

    local_dotenv = script_dir / ".env"
    if local_dotenv.exists():
        load_dotenv(local_dotenv)

    parent_dotenv = parent_dir / ".env"
    if parent_dotenv.exists():
        load_dotenv(parent_dotenv)

    load_dotenv()

    project_root = Path(os.getenv("PROJECT_ROOT", str(parent_dir.parent))).resolve()
    source_locale = os.getenv("SOURCE_LOCALE", "en-US")
    target_locale = os.getenv("TARGET_LOCALE", "pt-BR")

    source_locale_dir = Path(
        os.getenv("SOURCE_LOCALE_DIR", str(project_root / "Resources" / "Locale" / source_locale))
    ).resolve()
    target_locale_dir = Path(
        os.getenv("TARGET_LOCALE_DIR", str(project_root / "Resources" / "Locale" / target_locale))
    ).resolve()

    cache_dir = Path(os.getenv("CACHE_DIR", str(script_dir / ".cache"))).resolve()

    return Config(
        source_locale_dir=source_locale_dir,
        target_locale_dir=target_locale_dir,
        source_lang=os.getenv("SOURCE_LANG", "en"),
        target_lang=os.getenv("TARGET_LANG", "pt-BR"),
        cache_dir=cache_dir,
        llm_api_base=os.getenv("LLM_API_BASE", "http://127.0.0.1:1234/v1").rstrip("/"),
        llm_api_key=os.getenv("LLM_API_KEY", "not-needed"),
        llm_model=os.getenv("LLM_MODEL", "local-model"),
        llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.1")),
        llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "8192")),
        llm_timeout=int(os.getenv("LLM_TIMEOUT", "300")),
    )
