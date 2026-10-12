from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACK_END_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACK_END_DIR.parent
DEFAULT_PROMPT_DIR = PROJECT_ROOT / "default_promt"
ENV_PATH = BACK_END_DIR / ".env"

load_dotenv(ENV_PATH)


class Settings:
    def __init__(self) -> None:
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash"
        self.max_tokens = int(os.getenv("DEEPSEEK_MAX_TOKENS", "8192"))
        self.github_owner = os.getenv("GITHUB_OWNER", "").strip()
        self.github_repo = os.getenv("GITHUB_REPO", "").strip()
        self.github_token = os.getenv("GITHUB_TOKEN", "").strip()
        self.agents_dir = Path(os.getenv("AGENTS_DIR", str(DEFAULT_PROMPT_DIR)))
        self.db_path = Path(os.getenv("APP_DB", str(BACK_END_DIR / "data" / "app.db")))
        self.front_end_dir = PROJECT_ROOT / "front_end"

    def has_key(self) -> bool:
        return bool(self.api_key)

    def public(self) -> dict:
        hint = ""
        if self.api_key:
            hint = f"···{self.api_key[-4:]}" if len(self.api_key) >= 4 else "configured"
        models = ["deepseek-flash", "deepseek-v4-pro"]
        if self.model not in models:
            models.append(self.model)
        return {
            "has_api_key": self.has_key(),
            "key_hint": hint,
            "model": self.model,
            "base_url": self.base_url,
            "max_tokens": self.max_tokens,
            "models": models,
        }

    def github_public(self) -> dict:
        token_hint = f"···{self.github_token[-4:]}" if self.github_token else ""
        return {
            "owner": self.github_owner,
            "repo": self.github_repo,
            "has_token": bool(self.github_token),
            "token_hint": token_hint,
        }


settings = Settings()


def upsert_env(updates: dict[str, str]) -> None:
    lines: list[str] = []
    if ENV_PATH.exists():
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        key, _, _ = line.partition("=")
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
