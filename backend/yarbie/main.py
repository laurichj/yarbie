"""The FastAPI app: the API under /api and the built frontend at /."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from . import discovery
from .api import router
from .config import Settings
from .imagery import GoogleImagery
from .service import MapService
from .sources.base import MapSource
from .sources.cloud import CloudSource
from .sources.robot import RobotSource
from .store import BackupStore

log = logging.getLogger("yarbie")

FRONTEND_DIST = Path(
    os.environ.get("YARBIE_FRONTEND_DIR")
    or Path(__file__).resolve().parents[2] / "frontend" / "dist"
)


async def make_source(settings: Settings) -> MapSource:
    if settings.source == "lan":
        host, serial = settings.host, settings.serial
        if host is None:
            host, serial = await discovery.locate(serial, settings.data_dir / "lan_robot.json")
            log.info("yarbie: found robot %s at %s", serial, host)
        return RobotSource.lan(host, serial)
    if settings.source == "cloud":
        assert settings.email is not None and settings.password is not None
        return CloudSource(
            settings.email,
            settings.password,
            settings.data_dir / "cloud_tokens.json",
            settings.serial,
        )
    return RobotSource.simulated()


class _Frontend(StaticFiles):
    """The built UI. The page itself is always re-checked so a rebuild shows on reload;
    its scripts have content hashes in their names and can be cached for good."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(settings: Settings | None = None, source: MapSource | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        chosen = source or await make_source(settings)
        await chosen.start()
        log.info("yarbie: map source %s (%s)", chosen.name, "read/write" if chosen.writable else "read-only")
        app.state.service = MapService(chosen, BackupStore(settings.data_dir))
        app.state.google = (
            GoogleImagery(settings.google_maps_key, settings.data_dir / "google_session.json")
            if settings.google_maps_key
            else None
        )
        try:
            yield
        finally:
            await chosen.close()

    app = FastAPI(title="yarbie", lifespan=lifespan)
    app.include_router(router)
    if FRONTEND_DIST.exists():
        app.mount("/", _Frontend(directory=FRONTEND_DIST, html=True), name="frontend")
    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    if sys.argv[1:2] == ["discover"]:
        raise SystemExit(_discover(sys.argv[2] if len(sys.argv) > 2 else None))
    # Loopback by default: anyone who can reach this server can change the robot's map.
    host = os.environ.get("YARBIE_BIND", "127.0.0.1")
    port = int(os.environ.get("YARBIE_PORT", "8765"))
    uvicorn.run(create_app(), host=host, port=port)


def _discover(spec: str | None) -> int:
    """``yarbie discover [CIDR]``: list the robots heard on the network."""
    try:
        found = asyncio.run(discovery.scan(spec))
    except discovery.DiscoveryError as err:
        print(err, file=sys.stderr)
        return 1
    if not found:
        print("No Yarbo robots found.", file=sys.stderr)
        return 1
    for hit in found:
        print(f"{hit.host}\t{', '.join(hit.serials)}")
    return 0
