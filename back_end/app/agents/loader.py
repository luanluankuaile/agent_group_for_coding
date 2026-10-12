from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

AGENT_FILES = {
    "coordinate": "coordinate-agent.agent.md",
    "architect": "architect-agent.agent.md",
    "implement": "implement-agent.agent.md",
    "review": "review-agent.agent.md",
}

AGENT_ORDER = ["coordinate", "architect", "implement", "review"]

AGENT_META = {
    "coordinate": {"role": "Coordinate", "duty": "Lead · intake, orchestration, and summary"},
    "architect": {"role": "Architect", "duty": "Designer · solution, metadata, and governance"},
    "implement": {"role": "Implement", "duty": "Developer · steps, mappings, and artifacts"},
    "review": {"role": "Review", "duty": "Reviewer · quality, framework, and risk"},
}


@dataclass
class AgentSpec:
    id: str
    name: str
    role: str
    duty: str
    description: str
    temperature: float
    welcome_message: str
    conversation_starters: list[str]
    system_prompt: str
    source_file: str
    document: str
    trigger_agents: list[str]
    github_enabled: bool

    def public(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "duty": self.duty,
            "description": self.description,
            "temperature": self.temperature,
            "welcome_message": self.welcome_message,
            "conversation_starters": self.conversation_starters,
            "trigger_agents": self.trigger_agents,
            "github_enabled": self.github_enabled,
        }


def split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        raise ValueError("Agent file is missing frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise ValueError("Agent frontmatter is not closed")
    raw = text[3:end]
    body = text[end + 4 :].lstrip("\n")
    data = yaml.safe_load(raw) or {}
    if not isinstance(data, dict):
        raise ValueError("Agent frontmatter must be a mapping")
    return data, body


def compose_document(source: str, config: dict, prompt: str) -> str:
    metadata, _ = split_frontmatter(source)
    metadata.update(config)
    metadata.pop("model", None)
    body = prompt.strip()
    if not body:
        raise ValueError("Prompt body cannot be empty")
    frontmatter = yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False).rstrip()
    return f"---\n{frontmatter}\n---\n\n{body}\n"


def configured_agent_ids(metadata: dict) -> list[str]:
    aliases = {}
    for agent_id, labels in AGENT_META.items():
        aliases[agent_id.casefold()] = agent_id
        aliases[labels["role"].casefold()] = agent_id
        aliases[f"{labels['role']} Agent".casefold()] = agent_id
    configured = metadata.get("nested_agents") or []
    if not isinstance(configured, list):
        return []
    result = []
    for item in configured:
        value = item if isinstance(item, str) else item.get("id") or item.get("name") if isinstance(item, dict) else ""
        agent_id = aliases.get(str(value).strip().casefold())
        if agent_id and agent_id not in result:
            result.append(agent_id)
    return result


def github_enabled(metadata: dict) -> bool:
    toolkits = metadata.get("toolkits") or []
    return isinstance(toolkits, list) and any(
        isinstance(toolkit, dict) and toolkit.get("type") == "github" for toolkit in toolkits
    )


def apply_document(agent: AgentSpec, document: str) -> None:
    if not document.strip():
        raise ValueError("Prompt cannot be empty")
    parse_text = document.lstrip("\ufeff")
    if parse_text.startswith("---"):
        meta, prompt = split_frontmatter(parse_text)
        body = prompt.strip()
        if not body:
            raise ValueError("Prompt body cannot be empty")
        starters = meta.get("conversation_starters") or []
        if not isinstance(starters, list):
            raise ValueError("conversation_starters must be a list")
        try:
            temperature = (
                float(meta["temperature"])
                if meta.get("temperature") is not None
                else agent.temperature
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("temperature must be a number") from exc
        agent.name = str(meta["name"]).strip() if meta.get("name") else agent.name
        if "description" in meta:
            agent.description = str(meta.get("description") or "").strip()
        agent.temperature = temperature
        if "welcome_message" in meta:
            agent.welcome_message = str(meta.get("welcome_message") or "").strip()
        agent.conversation_starters = [str(item) for item in starters]
        agent.system_prompt = body
        agent.trigger_agents = configured_agent_ids(meta)
        agent.github_enabled = github_enabled(meta)
    else:
        agent.system_prompt = document.strip()
    agent.document = document


def load_agents(agents_dir: Path) -> dict[str, AgentSpec]:
    loaded: dict[str, AgentSpec] = {}
    for agent_id in AGENT_ORDER:
        path = agents_dir / AGENT_FILES[agent_id]
        if not path.exists():
            raise FileNotFoundError(f"Agent definition not found: {path}")
        raw = path.read_text(encoding="utf-8")
        meta, prompt = split_frontmatter(raw)
        starters = meta.get("conversation_starters") or []
        if not isinstance(starters, list):
            starters = []
        labels = AGENT_META[agent_id]
        loaded[agent_id] = AgentSpec(
            id=agent_id,
            name=str(meta.get("name") or agent_id),
            role=labels["role"],
            duty=labels["duty"],
            description=str(meta.get("description") or "").strip(),
            temperature=float(meta.get("temperature") or 0.6),
            welcome_message=str(meta.get("welcome_message") or "").strip(),
            conversation_starters=[str(item) for item in starters],
            system_prompt=prompt.strip(),
            source_file=AGENT_FILES[agent_id],
            document=raw,
            trigger_agents=configured_agent_ids(meta),
            github_enabled=github_enabled(meta),
        )
    return loaded
