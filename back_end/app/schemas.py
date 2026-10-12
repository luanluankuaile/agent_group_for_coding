from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class TaskRunIn(BaseModel):
    message: str
    session_id: str | None = None

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Enter a task first")
        if len(text) > 20000:
            raise ValueError("Task is too long")
        return text


class ReplyIn(BaseModel):
    message: str
    session_id: str | None = None
    agent_id: str
    scope: str = "agent"

    @field_validator("scope")
    @classmethod
    def clean_scope(cls, value: str) -> str:
        if value not in {"agent", "group"}:
            raise ValueError("Unknown chat scope")
        return value

    @field_validator("message")
    @classmethod
    def clean_message(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Enter a message first")
        if len(text) > 20000:
            raise ValueError("Message is too long")
        return text


class PromptIn(BaseModel):
    document: str | None = None
    config: dict[str, Any] | None = None
    prompt: str | None = None

    @field_validator("document")
    @classmethod
    def clean_document(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Prompt cannot be empty")
        if value is not None and len(value) > 200000:
            raise ValueError("Prompt is too long")
        return value

    @field_validator("prompt")
    @classmethod
    def clean_prompt(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Prompt body cannot be empty")
        if value is not None and len(value) > 200000:
            raise ValueError("Prompt is too long")
        return value


class PromptAssistIn(BaseModel):
    current_prompt: str
    requirements: str

    @field_validator("current_prompt")
    @classmethod
    def clean_current_prompt(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Current system prompt is required")
        if len(text) > 100000:
            raise ValueError("Current system prompt is too long")
        return text

    @field_validator("requirements")
    @classmethod
    def clean_requirements(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("Enter prompt requirements")
        if len(text) > 10000:
            raise ValueError("Prompt requirements are too long")
        return text


class SettingsIn(BaseModel):
    api_key: str | None = None
    model: str | None = None

    @field_validator("api_key")
    @classmethod
    def clean_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if any(char.isspace() for char in text):
            raise ValueError("Invalid API key")
        return text or None


class SettingsOut(BaseModel):
    has_api_key: bool
    key_hint: str
    model: str
    base_url: str
    max_tokens: int
    models: list[str] = Field(default_factory=list)


class GitHubSettingsIn(BaseModel):
    owner: str
    repo: str
    token: str | None = None
    clear_token: bool = False

    @field_validator("owner", "repo")
    @classmethod
    def clean_repository_part(cls, value: str) -> str:
        text = value.strip()
        if not text or len(text) > 100 or any(char in text for char in "/\\?#"):
            raise ValueError("Enter a valid GitHub owner and repository")
        return text

    @field_validator("token")
    @classmethod
    def clean_token(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if any(char.isspace() for char in text):
            raise ValueError("GitHub token cannot contain whitespace")
        return text or None


class GitHubBranchIn(BaseModel):
    name: str
    base: str | None = None


class GitHubFileIn(BaseModel):
    path: str
    content: str
    message: str
    branch: str


class GitHubPullRequestIn(BaseModel):
    title: str
    body: str = ""
    head: str
    base: str


class GitHubIssueIn(BaseModel):
    title: str
    body: str = ""
    labels: list[str] = Field(default_factory=list)


class GitHubIssueUpdateIn(BaseModel):
    state: str

    @field_validator("state")
    @classmethod
    def clean_state(cls, value: str) -> str:
        if value not in {"open", "closed"}:
            raise ValueError("Issue state must be open or closed")
        return value
