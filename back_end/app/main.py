from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
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
    db = Store(settings.db_path)
    loaded = load_agents(settings.agents_dir)
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
    logger.info("loaded agents: %s", ", ".join(loaded))
    yield
    store = router_state.get("store")
    if store is not None:
        store.close()


def create_app() -> FastAPI:
    app = FastAPI(title="Dev Agent Group", version="1.0.0", lifespan=lifespan)
    app.include_router(router)
    front = settings.front_end_dir
    app.mount("/static", StaticFiles(directory=front), name="static")

    @app.get("/")
    def index():
        return FileResponse(Path(front) / "index.html")

    return app


app = create_app()
