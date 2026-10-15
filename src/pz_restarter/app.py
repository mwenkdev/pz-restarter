"""HTTP interface. Authentication is done by Cloudflare Access in front of this service."""

from __future__ import annotations

import asyncio
import contextlib
import html
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from .controller import Controller

log = logging.getLogger(__name__)

PodLister = Callable[[], Awaitable[list]]


def _fmt(ts: datetime | None) -> str:
    if ts is None:
        return "-"
    return ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def create_app(
    controller: Controller,
    *,
    user_header: str,
    poll_interval_seconds: float,
    pod_lister: PodLister | None = None,
    pending_timeout_minutes: int = 30,
    run_poller: bool = True,
) -> FastAPI:
    flash: dict[str, str] = {}

    async def poller() -> None:
        while True:
            await asyncio.sleep(poll_interval_seconds)
            try:
                await controller.tick()
            except Exception:  # noqa: BLE001
                log.exception("poll failed")

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(poller()) if run_poller else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def user_of(request: Request) -> str | None:
        value = request.headers.get(user_header, "").strip()
        return value or None

    def same_origin(request: Request) -> bool:
        """Reject cross-site form posts riding on the Access cookie."""
        origin = request.headers.get("origin")
        if origin is None or origin == "null":
            return origin is None
        hosts = {request.headers.get("host"), request.headers.get("x-forwarded-host")}
        return urlsplit(origin).netloc in hosts - {None}

    def guard(request: Request) -> tuple[str | None, PlainTextResponse | None]:
        user = user_of(request)
        if user is None:
            return None, PlainTextResponse("Forbidden: no authenticated user", status_code=403)
        if not same_origin(request):
            return None, PlainTextResponse("Forbidden: cross-origin request", status_code=403)
        return user, None

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    @app.post("/restart")
    async def restart(request: Request):
        user, denied = guard(request)
        if denied:
            return denied
        flash[user] = await controller.request_restart(user)
        return RedirectResponse("/", status_code=303)

    @app.post("/cancel")
    async def cancel(request: Request):
        user, denied = guard(request)
        if denied:
            return denied
        flash[user] = await controller.cancel(user)
        return RedirectResponse("/", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        user = user_of(request)
        if user is None:
            return PlainTextResponse("Forbidden: no authenticated user", status_code=403)
        await controller.refresh()
        pods = None
        pod_error = None
        if pod_lister is not None:
            try:
                pods = await pod_lister()
            except Exception as exc:  # noqa: BLE001
                pod_error = str(exc)
        return HTMLResponse(
            render(
                controller, user, flash.pop(user, None), pods, pod_error, pending_timeout_minutes
            )
        )

    return app


def render(controller, user, message, pods, pod_error, pending_timeout_minutes) -> str:
    e = html.escape
    snap = controller.snapshot()

    if snap.rcon_error:
        players_html = f'<p class="bad">Server unreachable over RCON: {e(snap.rcon_error)}</p>'
    elif snap.players is None:
        players_html = "<p>Unknown</p>"
    elif not snap.players:
        players_html = '<p class="ok">Nobody online</p>'
    else:
        items = "".join(f"<li>{e(p)}</li>" for p in snap.players)
        players_html = f"<p>{len(snap.players)} online:</p><ul>{items}</ul>"

    if snap.pending:
        p = snap.pending
        pending_html = (
            f'<p class="warn">Restart pending, requested by {e(p.requested_by)} at '
            f"{e(_fmt(p.requested_at))}. It happens when everyone has logged off and expires "
            f"after {pending_timeout_minutes} minutes.</p>"
            '<form method="post" action="/cancel"><button>Cancel request</button></form>'
        )
    else:
        pending_html = (
            "<p>No restart pending.</p>"
            '<form method="post" action="/restart"><button>Restart server</button></form>'
            '<p class="hint">Restarts immediately if nobody is online. Otherwise players are '
            "told someone wants to join, and the server restarts once they log off.</p>"
        )

    if pod_error:
        pods_html = f'<p class="bad">Pod status unavailable: {e(pod_error)}</p>'
    elif pods is None:
        pods_html = ""
    elif not pods:
        pods_html = "<p>No server pod.</p>"
    else:
        rows = "".join(
            f"<tr><td>{e(pod.name)}</td><td>{e(pod.phase)}</td>"
            f"<td>{'yes' if pod.ready else 'no'}</td><td>{e(_fmt(pod.started_at))}</td></tr>"
            for pod in pods
        )
        pods_html = (
            "<table><tr><th>Pod</th><th>Phase</th><th>Ready</th><th>Started</th></tr>"
            f"{rows}</table>"
        )

    flash_html = f'<p class="flash">{e(message)}</p>' if message else ""
    events = "".join(f"<li>{e(_fmt(ts))}: {e(text)}</li>" for ts, text in snap.events)
    events_html = f"<h2>Recent events</h2><ul>{events}</ul>" if events else ""

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="15">
<title>PZ server</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 40rem; margin: 2rem auto;
       padding: 0 1rem; background: #111; color: #ddd; }}
button {{ font-size: 1.1rem; padding: .5rem 1rem; cursor: pointer; }}
.ok {{ color: #6c6; }} .warn {{ color: #fc6; }} .bad {{ color: #f66; }}
.flash {{ background: #234; padding: .5rem; }} .hint {{ color: #888; font-size: .9rem; }}
table {{ border-collapse: collapse; }} td, th {{ padding: .2rem .6rem; text-align: left; }}
</style></head><body>
<h1>Project Zomboid server</h1>
<p class="hint">Signed in as {e(user)}. Players last checked {e(_fmt(snap.players_checked_at))}.</p>
{flash_html}
<h2>Players</h2>{players_html}
<h2>Restart</h2>{pending_html}
{pods_html}
{events_html}
</body></html>"""
