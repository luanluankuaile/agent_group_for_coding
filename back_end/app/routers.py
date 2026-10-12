from __future__ import annotations

import base64
import json
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agents.loader import apply_document, compose_document, split_frontmatter
from app.config import settings, upsert_env
from app.github import github_request, repository_path
from app.llm import DeepSeekClient, DeepSeekError
from app.schemas import (
    GitHubBranchIn,
    GitHubFileIn,
    GitHubIssueIn,
    GitHubIssueUpdateIn,
    GitHubPullRequestIn,
    GitHubSettingsIn,
    PromptIn,
    PromptAssistIn,
    ReplyIn,
    SettingsIn,
    SettingsOut,
    TaskRunIn,
)
from app.services import RUNNING, Runner, active_pipeline, open_session, public_message, public_session

router = APIRouter(prefix="/api")

ALLOWED_MODELS = {"deepseek-flash", "deepseek-v4-pro"}
SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def agents():
    return router_state["agents"]


def store():
    return router_state["store"]


def runner() -> Runner:
    return Runner(settings, store(), agents())


router_state: dict = {}


def require_key() -> None:
    if not settings.has_key():
        raise HTTPException(status_code=400, detail="DeepSeek API key is not configured")


@router.get("/bootstrap")
def bootstrap():
    return {
        "agents": [agent.public() for agent in agents().values()],
        "pipeline": active_pipeline(agents()),
        "settings": settings.public(),
        "github": settings.github_public(),
    }


@router.get("/agents")
def list_agents():
    return [agent.public() for agent in agents().values()]


def prompt_payload(agent_id: str) -> dict:
    agent = agents().get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Unknown agent")
    default_document = (settings.agents_dir / agent.source_file).read_text(encoding="utf-8")
    config, prompt = split_frontmatter(agent.document)
    config.pop("model", None)
    payload = agent.public()
    payload.update(
        {
            "source_file": agent.source_file,
            "document": agent.document,
            "config": config,
            "prompt": prompt,
            "customized": agent.document != default_document,
        }
    )
    return payload


@router.get("/agents/{agent_id}/prompt")
def get_prompt(agent_id: str):
    return prompt_payload(agent_id)


@router.post("/agents/{agent_id}/prompt/assist")
async def assist_prompt(agent_id: str, body: PromptAssistIn):
    agent = agents().get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Unknown agent")
    require_key()
    messages = [
        {
            "role": "system",
            "content": (
                "You are an expert system-prompt editor. Merge the user's requirements into the existing system prompt. "
                "Preserve its existing purpose, safety boundaries, role identity, and useful instructions unless the new "
                "requirements explicitly change them. Remove contradictions cleanly, avoid duplicating rules, and keep the "
                "result usable as a complete system prompt. Return only the complete merged prompt, with no explanation or fences."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "agent_role": agent.role,
                    "existing_system_prompt": body.current_prompt,
                    "user_requirements": body.requirements,
                },
                ensure_ascii=False,
            ),
        },
    ]
    try:
        draft = await DeepSeekClient(settings).complete(messages)
    except DeepSeekError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if len(draft) > 200000:
        raise HTTPException(status_code=502, detail="Generated prompt is too long")
    return {"prompt": draft}


@router.put("/agents/{agent_id}/prompt")
def update_prompt(agent_id: str, body: PromptIn):
    agent = agents().get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Unknown agent")
    try:
        if body.config is not None and body.prompt is not None:
            document = compose_document(agent.document, body.config, body.prompt)
        elif body.document is not None:
            document = body.document
        else:
            raise ValueError("Prompt configuration and body are required")
        apply_document(agent, document)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    default_document = (settings.agents_dir / agent.source_file).read_text(encoding="utf-8")
    if agent.document == default_document:
        store().delete_agent_document(agent_id)
    else:
        store().save_agent_document(agent_id, agent.document)
    return prompt_payload(agent_id)


@router.post("/agents/{agent_id}/prompt/reset")
def reset_prompt(agent_id: str):
    agent = agents().get(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Unknown agent")
    default_document = (settings.agents_dir / agent.source_file).read_text(encoding="utf-8")
    try:
        apply_document(agent, default_document)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    store().delete_agent_document(agent_id)
    return prompt_payload(agent_id)


@router.get("/settings", response_model=SettingsOut)
def get_settings():
    return settings.public()


@router.post("/settings", response_model=SettingsOut)
def update_settings(body: SettingsIn):
    updates: dict[str, str] = {}
    if body.api_key:
        settings.api_key = body.api_key
        updates["DEEPSEEK_API_KEY"] = body.api_key
    if body.model:
        if body.model not in ALLOWED_MODELS:
            raise HTTPException(status_code=400, detail="Unsupported model")
        settings.model = body.model
        updates["DEEPSEEK_MODEL"] = body.model
    if updates:
        upsert_env(updates)
    return settings.public()


@router.get("/github/settings")
def get_github_settings():
    return settings.github_public()


@router.post("/github/settings")
def update_github_settings(body: GitHubSettingsIn):
    settings.github_owner = body.owner
    settings.github_repo = body.repo
    updates = {"GITHUB_OWNER": body.owner, "GITHUB_REPO": body.repo}
    if body.clear_token:
        settings.github_token = ""
        updates["GITHUB_TOKEN"] = ""
    elif body.token:
        settings.github_token = body.token
        updates["GITHUB_TOKEN"] = body.token
    upsert_env(updates)
    return settings.github_public()


@router.post("/github/test")
def test_github_connection():
    user = github_request("GET", "user")
    repo = github_request("GET", repository_path())
    return {
        "user": user.get("login", "") if isinstance(user, dict) else "",
        "full_name": repo.get("full_name", "") if isinstance(repo, dict) else "",
        "default_branch": repo.get("default_branch", "") if isinstance(repo, dict) else "",
        "private": repo.get("private", False) if isinstance(repo, dict) else False,
        "permissions": repo.get("permissions", {}) if isinstance(repo, dict) else {},
    }


@router.get("/github/branches")
def github_branches():
    data = github_request("GET", f"{repository_path()}/branches", params={"per_page": 100})
    return data if isinstance(data, list) else []


@router.post("/github/branches")
def create_github_branch(body: GitHubBranchIn):
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Branch name is required")
    repo_path = repository_path()
    base = body.base or ""
    if not base:
        repo = github_request("GET", repo_path)
        base = repo.get("default_branch", "main") if isinstance(repo, dict) else "main"
    base_ref = github_request("GET", f"{repo_path}/git/ref/heads/{quote(base, safe='')}")
    if not isinstance(base_ref, dict):
        raise HTTPException(status_code=404, detail="Base branch was not found")
    result = github_request(
        "POST",
        f"{repo_path}/git/refs",
        payload={"ref": f"refs/heads/{body.name.strip()}", "sha": base_ref["object"]["sha"]},
    )
    return result


@router.get("/github/tree")
def github_tree(ref: str | None = None):
    repo_path = repository_path()
    if not ref:
        repo = github_request("GET", repo_path)
        ref = repo.get("default_branch", "main") if isinstance(repo, dict) else "main"
    branch = github_request("GET", f"{repo_path}/branches/{quote(ref, safe='')}")
    if not isinstance(branch, dict):
        raise HTTPException(status_code=404, detail="Branch was not found")
    tree = github_request(
        "GET",
        f"{repo_path}/git/trees/{branch['commit']['sha']}",
        params={"recursive": 1},
    )
    if not isinstance(tree, dict):
        return {"tree": [], "truncated": False}
    return {"tree": tree.get("tree", []), "truncated": tree.get("truncated", False), "ref": ref}


@router.get("/github/file")
def read_github_file(path: str, ref: str | None = None):
    repo_path = repository_path()
    if not path.strip() or path.startswith("/") or ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="Enter a repository-relative file path")
    if not ref:
        repo = github_request("GET", repo_path)
        ref = repo.get("default_branch", "main") if isinstance(repo, dict) else "main"
    file_data = github_request(
        "GET",
        f"{repo_path}/contents/{quote(path, safe='/')}",
        params={"ref": ref},
    )
    if not isinstance(file_data, dict) or file_data.get("type") != "file":
        raise HTTPException(status_code=404, detail="File was not found")
    if file_data.get("encoding") != "base64" or not file_data.get("content"):
        raise HTTPException(status_code=413, detail="This file cannot be edited through the GitHub API")
    try:
        content = base64.b64decode(file_data["content"]).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=415, detail="Only UTF-8 text files can be edited") from exc
    return {"path": path, "ref": ref, "sha": file_data["sha"], "content": content}


@router.put("/github/file")
def commit_github_file(body: GitHubFileIn):
    path = body.path.strip()
    if not path or path.startswith("/") or ".." in path.split("/"):
        raise HTTPException(status_code=400, detail="Enter a repository-relative file path")
    if len(body.content.encode("utf-8")) > 900_000:
        raise HTTPException(status_code=413, detail="File content is too large for this editor")
    repo_path = repository_path()
    file_path = f"{repo_path}/contents/{quote(path, safe='/')}"
    existing = github_request(
        "GET", file_path, params={"ref": body.branch}, allow_not_found=True
    )
    payload = {
        "message": body.message.strip(),
        "content": base64.b64encode(body.content.encode("utf-8")).decode("ascii"),
        "branch": body.branch,
    }
    if not payload["message"]:
        raise HTTPException(status_code=400, detail="Commit message is required")
    if isinstance(existing, dict):
        payload["sha"] = existing["sha"]
    result = github_request("PUT", file_path, payload=payload)
    return result


@router.get("/github/commits")
def github_commits(ref: str | None = None):
    params = {"per_page": 30}
    if ref:
        params["sha"] = ref
    data = github_request("GET", f"{repository_path()}/commits", params=params)
    return data if isinstance(data, list) else []


@router.get("/github/pulls")
def github_pulls(state: str = "open"):
    if state not in {"open", "closed", "all"}:
        raise HTTPException(status_code=400, detail="Unknown pull request state")
    data = github_request(
        "GET", f"{repository_path()}/pulls", params={"state": state, "per_page": 100}
    )
    return data if isinstance(data, list) else []


@router.post("/github/pulls")
def create_github_pull(body: GitHubPullRequestIn):
    return github_request(
        "POST",
        f"{repository_path()}/pulls",
        payload={"title": body.title, "body": body.body, "head": body.head, "base": body.base},
    )


@router.put("/github/pulls/{number}/merge")
def merge_github_pull(number: int, method: str = "merge"):
    if method not in {"merge", "squash", "rebase"}:
        raise HTTPException(status_code=400, detail="Unsupported merge method")
    return github_request(
        "PUT", f"{repository_path()}/pulls/{number}/merge", payload={"merge_method": method}
    )


@router.get("/github/issues")
def github_issues(state: str = "open"):
    if state not in {"open", "closed", "all"}:
        raise HTTPException(status_code=400, detail="Unknown issue state")
    data = github_request(
        "GET", f"{repository_path()}/issues", params={"state": state, "per_page": 100}
    )
    return [issue for issue in data if "pull_request" not in issue] if isinstance(data, list) else []


@router.post("/github/issues")
def create_github_issue(body: GitHubIssueIn):
    return github_request(
        "POST",
        f"{repository_path()}/issues",
        payload={"title": body.title, "body": body.body, "labels": body.labels},
    )


@router.patch("/github/issues/{number}")
def update_github_issue(number: int, body: GitHubIssueUpdateIn):
    return github_request(
        "PATCH", f"{repository_path()}/issues/{number}", payload={"state": body.state}
    )


@router.get("/sessions")
def list_sessions(kind: str | None = None, agent_id: str | None = None):
    if kind and kind not in {"group", "agent"}:
        raise HTTPException(status_code=400, detail="Unknown session type")
    return store().list_sessions(kind=kind, agent_id=agent_id)


@router.get("/sessions/{session_id}")
def get_session(session_id: str):
    session = store().get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    payload = public_session(session)
    payload["messages"] = [public_message(item) for item in store().list_messages(session_id)]
    return payload


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str):
    if session_id in RUNNING:
        raise HTTPException(status_code=409, detail="This task is running and cannot be deleted yet")
    if not store().delete_session(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    return {"ok": True}


@router.post("/tasks/run")
async def run_task(body: TaskRunIn, request: Request):
    require_key()
    try:
        session = open_session(store(), "group", body.message, body.session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if session["id"] in RUNNING:
        raise HTTPException(status_code=409, detail="This task is already running")
    stream = runner().run_pipeline(request, session, body.message)
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.post("/chat")
async def chat(body: ReplyIn, request: Request):
    require_key()
    agent = agents().get(body.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Unknown agent")
    if body.session_id:
        existing = store().get_session(body.session_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if existing["kind"] != body.scope:
            raise HTTPException(status_code=400, detail="This session cannot be used on this page")
    try:
        session = open_session(
            store(),
            body.scope,
            body.message,
            body.session_id,
            agent_id=body.agent_id if body.scope == "agent" else None,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if session["id"] in RUNNING:
        raise HTTPException(status_code=409, detail="This session is generating")
    stream = runner().run_reply(request, session, agent, body.message)
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)
