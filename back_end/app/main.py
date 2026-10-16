from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agents.loader import apply_document, load_agents
from app.config import settings
from app.db import Store
from app.routers import router, router_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("agent_group")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Load agents ───────────────────────────────────────────────────────
    try:
        loaded = load_agents(settings.agents_dir)
    except FileNotFoundError as exc:
        logger.error("STARTUP FAILED — agent file not found: %s", exc)
        logger.error("Make sure the 'default_promt/' directory exists and contains all 4 agent .md files.")
        raise
    except Exception as exc:
        logger.error("STARTUP FAILED — could not load agents: %s", exc)
        logger.error(
            "Common causes: invalid YAML frontmatter in an agent .md file, "
            "missing 'name' or 'temperature' field, or a syntax error."
        )
        raise

    # ── Apply any saved prompt overrides from DB ───────────────────────────
    try:
        db = Store(settings.db_path)
    except Exception as exc:
        logger.error("STARTUP FAILED — could not open database at %s: %s", settings.db_path, exc)
        raise

    for agent_id, document in db.agent_documents().items():
        agent = loaded.get(agent_id)
        if agent is None:
            continue
        try:
            apply_document(agent, document)
        except ValueError as exc:
            logger.warning("ignored saved prompt for %s: %s", agent_id, exc)
            db.delete_agent_document(agent_id)

    router_state["agents"] = loaded
    router_state["store"] = db
    logger.info("✓ Loaded agents: %s", ", ".join(loaded))
    logger.info("✓ Agents dir:    %s", settings.agents_dir)
    logger.info("✓ DB path:       %s", settings.db_path)
    logger.info("✓ Front-end dir: %s", settings.front_end_dir)
    logger.info("✓ Server ready — open http://127.0.0.1:8000 in your browser")
    yield
    store = router_state.get("store")
    if store is not None:
        store.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Dev Agent Group", version="1.0.0", lifespan=lifespan)

    # ── CORS ──────────────────────────────────────────────────────────────
    # Allows the browser to reach /api/* regardless of whether the page was
    # opened via localhost, 127.0.0.1, or a local LAN address.  Credentials
    # are not used so wildcard origins are safe here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router)
    front = settings.front_end_dir
    app.mount("/static", StaticFiles(directory=front), name="static")

    @app.get("/")
    def index():
        return FileResponse(Path(front) / "index.html")

    return app


app = create_app()
