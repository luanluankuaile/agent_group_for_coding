from __future__ import annotations

import httpx
from fastapi import HTTPException

from app.config import settings

GITHUB_API = "https://api.github.com"


def repository_path() -> str:
    if not settings.github_owner or not settings.github_repo:
        raise HTTPException(status_code=400, detail="Configure a GitHub owner and repository first")
    return f"repos/{settings.github_owner}/{settings.github_repo}"


def github_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
    allow_not_found: bool = False,
) -> dict | list | None:
    if not settings.github_token:
        raise HTTPException(status_code=400, detail="Configure a GitHub token first")
    try:
        response = httpx.request(
            method,
            f"{GITHUB_API}/{path.lstrip('/')}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {settings.github_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            params=params,
            json=payload,
            timeout=20,
        )
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail="Could not reach GitHub") from exc
    if allow_not_found and response.status_code == 404:
        return None
    try:
        data = response.json() if response.content else {}
    except ValueError:
        data = {}
    if not response.is_success:
        message = data.get("message") if isinstance(data, dict) else None
        raise HTTPException(
            status_code=response.status_code,
            detail=f"GitHub API: {message or 'Request failed'}",
        )
    return data