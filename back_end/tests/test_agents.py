import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from app.agents.loader import compose_document, load_agents, split_frontmatter
from app.config import DEFAULT_PROMPT_DIR
from app.db import Store
from app.prompts import PIPELINE, instruction_for
from app.services import Runner, active_pipeline, build_messages, format_context


class AgentLoaderTest(unittest.TestCase):
    def test_loads_four_roles(self):
        agents = load_agents(DEFAULT_PROMPT_DIR)
        self.assertEqual(
            list(agents),
            ["coordinate", "architect", "implement", "review"],
        )
        coordinate = agents["coordinate"]
        self.assertEqual(coordinate.name, "Coordinate Agent")
        self.assertIn("Coordinate Agent", coordinate.system_prompt)
        self.assertGreaterEqual(len(coordinate.conversation_starters), 1)
        self.assertIn("Architect Agent", agents["architect"].system_prompt)
        self.assertIn("Implementation Agent", agents["implement"].system_prompt)
        self.assertIn("REQUEST CHANGES", agents["review"].system_prompt)
        self.assertEqual(coordinate.trigger_agents, ["architect", "implement", "review"])
        self.assertTrue(coordinate.github_enabled)

    def test_compose_document_preserves_nested_config_and_drops_model(self):
        source = (DEFAULT_PROMPT_DIR / "implement-agent.agent.md").read_text(encoding="utf-8")
        config, prompt = split_frontmatter(source)
        self.assertIn("toolkits", config)
        config["temperature"] = 0.3
        document = compose_document(source, config, prompt)
        saved_config, saved_prompt = split_frontmatter(document)
        self.assertNotIn("model", saved_config)
        self.assertEqual(saved_config["temperature"], 0.3)
        self.assertEqual(saved_config["toolkits"], config["toolkits"])
        self.assertEqual(saved_prompt.strip(), prompt.strip())

    def test_pipeline_uses_each_role(self):
        agents = load_agents(DEFAULT_PROMPT_DIR)
        self.assertEqual(
            [step["agent_id"] for step in PIPELINE],
            ["coordinate", "architect", "implement", "review", "coordinate"],
        )
        context = "## User\nOnboard customer.account"
        messages = build_messages(agents["architect"], context, "design", reply=False)
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("Architect Agent", messages[0]["content"])
        self.assertIn("Landing, Raw, Persistent", messages[1]["content"])
        self.assertIn("Onboard customer.account", messages[1]["content"])
        self.assertTrue(instruction_for("review", context))

    def test_configured_pipeline_filters_unselected_agents(self):
        agents = load_agents(DEFAULT_PROMPT_DIR)
        agents["coordinate"].trigger_agents = ["architect"]
        self.assertEqual(
            [step["id"] for step in active_pipeline(agents)],
            ["intake", "design", "summary"],
        )


class AgentDelegateTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_chat_triggers_configured_delegate(self):
        from app.config import settings

        class FakeLLM:
            async def stream(self, messages, temperature):
                yield "content", "Focused result"

        agents = load_agents(DEFAULT_PROMPT_DIR)
        agents["architect"].trigger_agents = ["implement"]
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "app.db")
            session = store.create_session("agent", "Delegation test", agent_id="architect")
            runner = Runner(settings, store, agents)
            runner.llm = FakeLLM()
            request = Mock()
            request.is_disconnected = AsyncMock(return_value=False)
            events = [
                event
                async for event in runner.run_reply(
                    request, session, agents["architect"], "Design this change"
                )
            ]
            assistant_messages = [
                message for message in store.list_messages(session["id"])
                if message["role"] == "assistant"
            ]
            self.assertIn('"step_id": "delegate-implement"', "".join(events))
            self.assertEqual([message["agent_id"] for message in assistant_messages], ["architect", "implement"])
            store.close()

    def test_session_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / "app.db")
            session = store.create_session("group", "Test task")
            store.add_message(session["id"], "user", "hello")
            store.add_message(
                session["id"],
                "assistant",
                "plan",
                agent_id="coordinate",
                step_id="intake",
                step_title="Intake and plan",
            )
            messages = store.list_messages(session["id"])
            self.assertEqual(len(messages), 2)
            text = format_context(messages, load_agents(DEFAULT_PROMPT_DIR))
            self.assertIn("Coordinate Agent", text)
            self.assertIn("hello", text)
            self.assertTrue(store.delete_session(session["id"]))
            self.assertIsNone(store.get_session(session["id"]))
            store.close()


class ApiTest(unittest.TestCase):
    def test_bootstrap_page_and_missing_key(self):
        from fastapi.testclient import TestClient

        from app.config import settings
        from app.main import app

        previous_db = settings.db_path
        previous_key = settings.api_key
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.db_path = Path(tmp) / "app.db"
                settings.api_key = ""
                with TestClient(app) as client:
                    boot = client.get("/api/bootstrap")
                    self.assertEqual(boot.status_code, 200)
                    payload = boot.json()
                    self.assertEqual(
                        [item["id"] for item in payload["agents"]],
                        ["coordinate", "architect", "implement", "review"],
                    )
                    self.assertEqual(len(payload["pipeline"]), 5)
                    self.assertFalse(payload["settings"]["has_api_key"])
                    denied = client.post("/api/tasks/run", json={"message": "Onboard a table"})
                    self.assertEqual(denied.status_code, 400)
                    page = client.get("/")
                    self.assertEqual(page.status_code, 200)
                    self.assertIn("Dev Agent Group", page.text)
                    self.assertIn("/static/app.js", page.text)
                    script = client.get("/static/app.js")
                    self.assertEqual(script.status_code, 200)
                    prompt = client.get("/api/agents/architect/prompt")
                    self.assertEqual(prompt.status_code, 200)
                    original = prompt.json()
                    self.assertIn("architect-agent.agent.md", original["source_file"])
                    self.assertIn("Architect Agent", original["document"])
                    self.assertNotIn("model", original["config"])
                    self.assertTrue(original["prompt"].strip())
                    self.assertFalse(original["customized"])
                    config = original["config"]
                    config["temperature"] = 0.3
                    config["nested_agents"] = [{"name": "Implement Agent"}]
                    config["toolkits"] = [{"toolkit": "github", "type": "github", "meta": {"label": "GitHub"}, "settings": {}, "tools": []}]
                    structured = client.put(
                        "/api/agents/architect/prompt",
                        json={"config": config, "prompt": original["prompt"]},
                    )
                    self.assertEqual(structured.status_code, 200)
                    self.assertEqual(structured.json()["config"]["temperature"], 0.3)
                    self.assertEqual(structured.json()["trigger_agents"], ["implement"])
                    self.assertTrue(structured.json()["github_enabled"])
                    self.assertNotIn("model", structured.json()["document"])
                    saved = client.put(
                        "/api/agents/architect/prompt",
                        json={"document": original["document"] + "\nExtra instruction.\n"},
                    )
                    self.assertEqual(saved.status_code, 200)
                    self.assertTrue(saved.json()["customized"])
                    self.assertIn("Extra instruction.", saved.json()["document"])
                    reset = client.post("/api/agents/architect/prompt/reset")
                    self.assertEqual(reset.status_code, 200)
                    self.assertFalse(reset.json()["customized"])
                    self.assertNotIn("Extra instruction.", reset.json()["document"])
        finally:
            settings.db_path = previous_db
            settings.api_key = previous_key

    def test_prompt_assist_requires_key_and_returns_merged_draft(self):
        from fastapi.testclient import TestClient

        from app.config import settings
        from app.main import app

        previous_db, previous_key = settings.db_path, settings.api_key
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.db_path = Path(tmp) / "app.db"
                settings.api_key = ""
                with TestClient(app) as client:
                    payload = {"current_prompt": "Existing system prompt", "requirements": "Add concise examples"}
                    denied = client.post("/api/agents/architect/prompt/assist", json=payload)
                    self.assertEqual(denied.status_code, 400)
                    settings.api_key = "test-key"
                    with patch(
                        "app.routers.DeepSeekClient.complete",
                        new=AsyncMock(return_value="Merged system prompt"),
                    ) as complete:
                        response = client.post("/api/agents/architect/prompt/assist", json=payload)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["prompt"], "Merged system prompt")
                    self.assertIn("Existing system prompt", complete.await_args.args[0][1]["content"])
                    self.assertIn("Add concise examples", complete.await_args.args[0][1]["content"])
        finally:
            settings.db_path, settings.api_key = previous_db, previous_key


class GitHubApiTest(unittest.TestCase):
    def test_github_settings_hide_token_and_connection_uses_configured_repo(self):
        from fastapi.testclient import TestClient

        from app.config import settings
        from app.main import app

        previous = (settings.db_path, settings.github_owner, settings.github_repo, settings.github_token)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.db_path = Path(tmp) / "app.db"
                with patch("app.config.ENV_PATH", Path(tmp) / ".env"), TestClient(app) as client:
                    saved = client.post(
                        "/api/github/settings",
                        json={"owner": "octo-org", "repo": "agent-repo", "token": "ghp_example_secret"},
                    )
                    self.assertEqual(saved.status_code, 200)
                    self.assertTrue(saved.json()["has_token"])
                    self.assertNotIn("ghp_example_secret", saved.text)
                    self.assertIn("GITHUB_TOKEN=ghp_example_secret", (Path(tmp) / ".env").read_text())
                    with patch(
                        "app.routers.github_request",
                        side_effect=[
                            {"login": "octo-user"},
                            {
                                "full_name": "octo-org/agent-repo",
                                "default_branch": "main",
                                "private": True,
                                "permissions": {"push": True},
                            },
                        ],
                    ) as request:
                        connected = client.post("/api/github/test")
                    self.assertEqual(connected.status_code, 200)
                    self.assertEqual(connected.json()["full_name"], "octo-org/agent-repo")
                    self.assertEqual(request.call_args_list[1].args[1], "repos/octo-org/agent-repo")
        finally:
            settings.db_path, settings.github_owner, settings.github_repo, settings.github_token = previous

    def test_github_request_sends_token_as_bearer(self):
        from app.config import settings
        from app.github import github_request

        previous_token = settings.github_token
        settings.github_token = "ghp_test_token"
        response = Mock(status_code=200, is_success=True, content=b'{"ok": true}')
        response.json.return_value = {"ok": True}
        try:
            with patch("app.github.httpx.request", return_value=response) as request:
                self.assertEqual(github_request("GET", "user"), {"ok": True})
            self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer ghp_test_token")
        finally:
            settings.github_token = previous_token

    def test_branch_creation_and_tree_browsing(self):
        from fastapi.testclient import TestClient

        from app.config import settings
        from app.main import app

        previous = (settings.db_path, settings.github_owner, settings.github_repo, settings.github_token)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.db_path = Path(tmp) / "app.db"
                settings.github_owner, settings.github_repo, settings.github_token = "octo-org", "agent-repo", "test-token"
                with TestClient(app) as client:
                    with patch(
                        "app.routers.github_request",
                        side_effect=[
                            {"object": {"sha": "base-sha"}},
                            {"ref": "refs/heads/feature/ui"},
                        ],
                    ) as request:
                        created = client.post(
                            "/api/github/branches",
                            json={"name": "feature/ui", "base": "main"},
                        )
                    self.assertEqual(created.status_code, 200)
                    self.assertEqual(request.call_args_list[1].kwargs["payload"]["sha"], "base-sha")
                    with patch(
                        "app.routers.github_request",
                        side_effect=[
                            {"commit": {"sha": "head-sha"}},
                            {"tree": [{"path": "README.md", "type": "blob"}], "truncated": False},
                        ],
                    ) as request:
                        tree = client.get("/api/github/tree?ref=feature/ui")
                    self.assertEqual(tree.status_code, 200)
                    self.assertEqual(tree.json()["tree"][0]["path"], "README.md")
                    self.assertEqual(request.call_args_list[1].args[1], "repos/octo-org/agent-repo/git/trees/head-sha")
        finally:
            settings.db_path, settings.github_owner, settings.github_repo, settings.github_token = previous

    def test_file_commit_updates_existing_blob(self):
        from fastapi.testclient import TestClient

        from app.config import settings
        from app.main import app

        previous = (settings.db_path, settings.github_owner, settings.github_repo, settings.github_token)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                settings.db_path = Path(tmp) / "app.db"
                settings.github_owner, settings.github_repo, settings.github_token = "octo-org", "agent-repo", "test-token"
                with TestClient(app) as client:
                    with patch(
                        "app.routers.github_request",
                        side_effect=[{"sha": "old-sha"}, {"commit": {"sha": "new-sha"}}],
                    ) as request:
                        result = client.put(
                            "/api/github/file",
                            json={"path": "README.md", "content": "hello", "message": "Update docs", "branch": "main"},
                        )
                    self.assertEqual(result.status_code, 200)
                    payload = request.call_args_list[1].kwargs["payload"]
                    self.assertEqual(payload["sha"], "old-sha")
                    self.assertEqual(payload["content"], "aGVsbG8=")
        finally:
            settings.db_path, settings.github_owner, settings.github_repo, settings.github_token = previous


if __name__ == "__main__":
    unittest.main()
