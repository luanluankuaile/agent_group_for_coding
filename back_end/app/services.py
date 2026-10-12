from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import Request

from app.agents.loader import AgentSpec
from app.config import Settings
from app.db import Store, title_from_message
from app.llm import DeepSeekClient, DeepSeekError
from app.prompts import PIPELINE, RUNTIME_NOTE, instruction_for, reply_instruction

logger = logging.getLogger("agent_group")
RUNNING: set[str] = set()
CONTEXT_LIMIT = 48000


def sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def public_message(row: dict) -> dict:
    return {
        "id": row["id"],
        "role": row["role"],
        "agent_id": row["agent_id"],
        "step_id": row["step_id"],
        "step_title": row["step_title"],
        "content": row["content"],
        "created_at": row["created_at"],
    }


def public_session(row: dict) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "agent_id": row["agent_id"],
        "title": row["title"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def system_prompt(agent: AgentSpec) -> str:
    delegates = ", ".join(agent.trigger_agents) or "none"
    github_access = "enabled" if agent.github_enabled else "disabled"
    capability_note = (
        f"\n\n## Configured capabilities\nAllowed agent delegates: {delegates}. "
        f"GitHub workspace access: {github_access}. GitHub API operations are performed only from the GitHub page."
    )
    return f"{agent.system_prompt}\n\n{RUNTIME_NOTE}{capability_note}"


def active_pipeline(agents: dict[str, AgentSpec]) -> list[dict]:
    coordinator = agents.get("coordinate")
    if coordinator is None:
        return PIPELINE
    allowed = set(coordinator.trigger_agents)
    return [step for step in PIPELINE if step["agent_id"] == "coordinate" or step["agent_id"] in allowed]


def format_context(messages: list[dict], agents: dict[str, AgentSpec]) -> str:
    blocks: list[str] = []
    for message in messages:
        if message["role"] == "user":
            heading = "User"
        else:
            agent = agents.get(message.get("agent_id") or "")
            name = agent.name if agent else (message.get("agent_id") or "Agent")
            step = message.get("step_title") or "Chat"
            heading = f"{name} · {step}"
        blocks.append(f"## {heading}\n{(message.get('content') or '').strip()}")
    text = "\n\n".join(blocks)
    if len(text) <= CONTEXT_LIMIT:
        return text
    return "…(earlier context truncated)…\n" + text[-CONTEXT_LIMIT:]


def build_messages(agent: AgentSpec, context: str, step_id: str | None, reply: bool) -> list[dict]:
    instruction = reply_instruction(context) if reply else instruction_for(step_id or "reply", context)
    return [
        {"role": "system", "content": system_prompt(agent)},
        {"role": "user", "content": instruction},
    ]


def direct_messages(agent: AgentSpec, history: list[dict], user_message: str) -> list[dict]:
    messages = [{"role": "system", "content": system_prompt(agent)}]
    for item in history[-24:]:
        role = "assistant" if item["role"] == "assistant" else "user"
        messages.append({"role": role, "content": item["content"]})
    messages.append({"role": "user", "content": user_message})
    return messages


class Runner:
    def __init__(self, settings: Settings, store: Store, agents: dict[str, AgentSpec]) -> None:
        self.settings = settings
        self.store = store
        self.agents = agents
        self.llm = DeepSeekClient(settings)

    async def _stream_agent(
        self,
        request: Request,
        messages: list[dict],
        temperature: float,
    ) -> AsyncIterator[tuple[str, str] | tuple[str, None]]:
        async for channel, text in self.llm.stream(messages, temperature):
            if await request.is_disconnected():
                yield "disconnected", None
                return
            yield channel, text

    async def run_pipeline(
        self,
        request: Request,
        session: dict,
        user_message: str,
    ) -> AsyncIterator[str]:
        session_id = session["id"]
        pipeline = active_pipeline(self.agents)
        RUNNING.add(session_id)
        try:
            yield sse({"type": "session", "session": public_session(self.store.get_session(session_id))})
            user_row = self.store.add_message(session_id, "user", user_message)
            yield sse({"type": "message", "message": public_message(user_row)})
            for index, step in enumerate(pipeline, start=1):
                if await request.is_disconnected():
                    break
                agent = self.agents[step["agent_id"]]
                yield sse(
                    {
                        "type": "step_start",
                        "step_id": step["id"],
                        "step_title": step["title"],
                        "agent_id": agent.id,
                        "index": index,
                        "total": len(pipeline),
                    }
                )
                history = self.store.list_messages(session_id)
                messages = build_messages(
                    agent, format_context(history, self.agents), step["id"], reply=False
                )
                logger.info("pipeline step %s via %s", step["id"], agent.id)
                content_parts: list[str] = []
                reasoning_parts: list[str] = []
                try:
                    async for channel, text in self._stream_agent(request, messages, agent.temperature):
                        if channel == "disconnected":
                            self._save_partial(session_id, agent.id, step, content_parts, reasoning_parts)
                            return
                        if channel == "reasoning":
                            reasoning_parts.append(text or "")
                            yield sse({"type": "delta", "channel": "reasoning", "content": text})
                        else:
                            content_parts.append(text or "")
                            yield sse({"type": "delta", "channel": "content", "content": text})
                except asyncio.CancelledError:
                    self._save_partial(session_id, agent.id, step, content_parts, reasoning_parts)
                    raise
                except DeepSeekError as exc:
                    logger.warning("deepseek error on %s: %s", step["id"], exc)
                    self._save_partial(session_id, agent.id, step, content_parts, reasoning_parts)
                    yield sse({"type": "error", "detail": str(exc), "step_id": step["id"]})
                    return
                content = "".join(content_parts).strip() or "".join(reasoning_parts).strip()
                if not content:
                    content = "(This step returned no content)"
                saved = self.store.add_message(
                    session_id,
                    "assistant",
                    content,
                    agent_id=agent.id,
                    step_id=step["id"],
                    step_title=step["title"],
                )
                yield sse({"type": "step_end", "message": public_message(saved)})
            yield sse({"type": "done"})
        finally:
            RUNNING.discard(session_id)

    async def run_reply(
        self,
        request: Request,
        session: dict,
        agent: AgentSpec,
        user_message: str,
    ) -> AsyncIterator[str]:
        session_id = session["id"]
        RUNNING.add(session_id)
        step = {"id": "reply", "title": "Chat"}
        delegates = [
            self.agents[agent_id]
            for agent_id in agent.trigger_agents
            if session["kind"] == "agent" and agent_id in self.agents and agent_id != agent.id
        ]
        total = 1 + len(delegates)
        try:
            yield sse({"type": "session", "session": public_session(self.store.get_session(session_id))})
            user_row = self.store.add_message(session_id, "user", user_message)
            yield sse({"type": "message", "message": public_message(user_row)})
            yield sse(
                {
                    "type": "step_start",
                    "step_id": "reply",
                    "step_title": "Chat",
                    "agent_id": agent.id,
                    "index": 1,
                    "total": total,
                }
            )
            if session["kind"] == "group":
                history = self.store.list_messages(session_id)
                messages = build_messages(
                    agent, format_context(history, self.agents), None, reply=True
                )
            else:
                prior = self.store.list_messages(session_id)
                prior = [item for item in prior if item["id"] != user_row["id"]]
                messages = direct_messages(agent, prior, user_message)
            logger.info("reply via %s scope %s", agent.id, session["kind"])
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            try:
                async for channel, text in self._stream_agent(request, messages, agent.temperature):
                    if channel == "disconnected":
                        self._save_partial(session_id, agent.id, step, content_parts, reasoning_parts)
                        return
                    if channel == "reasoning":
                        reasoning_parts.append(text or "")
                        yield sse({"type": "delta", "channel": "reasoning", "content": text})
                    else:
                        content_parts.append(text or "")
                        yield sse({"type": "delta", "channel": "content", "content": text})
            except asyncio.CancelledError:
                self._save_partial(session_id, agent.id, step, content_parts, reasoning_parts)
                raise
            except DeepSeekError as exc:
                logger.warning("deepseek error on reply: %s", exc)
                self._save_partial(session_id, agent.id, step, content_parts, reasoning_parts)
                yield sse({"type": "error", "detail": str(exc), "step_id": "reply"})
                return
            content = "".join(content_parts).strip() or "".join(reasoning_parts).strip()
            if not content:
                content = "(No content returned)"
            saved = self.store.add_message(
                session_id,
                "assistant",
                content,
                agent_id=agent.id,
                step_id="reply",
                step_title="Chat",
            )
            yield sse({"type": "step_end", "message": public_message(saved)})
            for index, delegate in enumerate(delegates, start=2):
                delegate_step = {
                    "id": f"delegate-{delegate.id}",
                    "title": f"Triggered by {agent.role}",
                }
                yield sse(
                    {
                        "type": "step_start",
                        "step_id": delegate_step["id"],
                        "step_title": delegate_step["title"],
                        "agent_id": delegate.id,
                        "index": index,
                        "total": total,
                    }
                )
                history = self.store.list_messages(session_id)
                delegation_request = (
                    f"{agent.role} delegated this request to you.\n\n"
                    f"Original user request:\n{user_message}\n\n"
                    f"Primary agent response:\n{content}\n\n"
                    "Provide your focused contribution. Do not repeat the primary response."
                )
                delegate_messages = direct_messages(delegate, history, delegation_request)
                delegate_content: list[str] = []
                delegate_reasoning: list[str] = []
                try:
                    async for channel, text in self._stream_agent(
                        request, delegate_messages, delegate.temperature
                    ):
                        if channel == "disconnected":
                            self._save_partial(
                                session_id, delegate.id, delegate_step, delegate_content, delegate_reasoning
                            )
                            return
                        if channel == "reasoning":
                            delegate_reasoning.append(text or "")
                            yield sse({"type": "delta", "channel": "reasoning", "content": text})
                        else:
                            delegate_content.append(text or "")
                            yield sse({"type": "delta", "channel": "content", "content": text})
                except asyncio.CancelledError:
                    self._save_partial(
                        session_id, delegate.id, delegate_step, delegate_content, delegate_reasoning
                    )
                    raise
                except DeepSeekError as exc:
                    logger.warning("delegation to %s failed: %s", delegate.id, exc)
                    yield sse({"type": "error", "detail": str(exc), "step_id": delegate_step["id"]})
                    return
                delegate_reply = "".join(delegate_content).strip() or "".join(delegate_reasoning).strip()
                if not delegate_reply:
                    delegate_reply = "(No content returned)"
                saved = self.store.add_message(
                    session_id,
                    "assistant",
                    delegate_reply,
                    agent_id=delegate.id,
                    step_id=delegate_step["id"],
                    step_title=delegate_step["title"],
                )
                yield sse({"type": "step_end", "message": public_message(saved)})
            yield sse({"type": "done"})
        finally:
            RUNNING.discard(session_id)

    def _save_partial(
        self,
        session_id: str,
        agent_id: str,
        step: dict,
        content_parts: list[str],
        reasoning_parts: list[str],
    ) -> None:
        content = "".join(content_parts).strip() or "".join(reasoning_parts).strip()
        if not content:
            return
        self.store.add_message(
            session_id,
            "assistant",
            content + "\n\n(Generation stopped early)",
            agent_id=agent_id,
            step_id=step["id"],
            step_title=step["title"],
        )


def open_session(
    store: Store,
    kind: str,
    message: str,
    session_id: str | None,
    agent_id: str | None = None,
) -> dict:
    if session_id:
        session = store.get_session(session_id)
        if session is None:
            raise KeyError("Session not found")
        if session["kind"] != kind:
            raise ValueError("This session cannot be used on this page")
        if kind == "agent" and session["agent_id"] != agent_id:
            raise ValueError("This session does not belong to this agent")
        return session
    return store.create_session(kind, title_from_message(message), agent_id=agent_id)
