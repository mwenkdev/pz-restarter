"""Restart state machine.

Invariant: the Deployment is only restarted when an RCON `players` query made
immediately beforehand reported nobody online. If RCON cannot be reached, nothing
is restarted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

log = logging.getLogger(__name__)


class Game(Protocol):
    async def players(self) -> list[str]: ...

    async def broadcast(self, message: str) -> None: ...


class Restarter(Protocol):
    async def restart(self) -> None: ...


@dataclass
class Pending:
    requested_by: str
    requested_at: datetime
    started: float
    last_reminder: float


@dataclass
class Snapshot:
    players: list[str] | None
    players_checked_at: datetime | None
    rcon_error: str | None
    pending: Pending | None
    events: list[tuple[datetime, str]] = field(default_factory=list)


def display_name(user: str) -> str:
    """Use the local part of an email so addresses are not broadcast in game."""
    return user.split("@", 1)[0] or user


class Controller:
    def __init__(
        self,
        game: Game,
        restarter: Restarter,
        *,
        reminder_interval_seconds: float,
        pending_timeout_seconds: float,
        players_cache_seconds: float = 10,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._game = game
        self._restarter = restarter
        self._reminder_interval = reminder_interval_seconds
        self._timeout = pending_timeout_seconds
        self._players_cache = players_cache_seconds
        self._clock = clock
        self._now = now
        self._lock = asyncio.Lock()
        self.pending: Pending | None = None
        self.players: list[str] | None = None
        self.players_checked_at: datetime | None = None
        self._players_checked_mono: float | None = None
        self.rcon_error: str | None = None
        self.events: list[tuple[datetime, str]] = []

    # -- helpers -----------------------------------------------------------

    def _event(self, message: str) -> None:
        log.info(message)
        self.events.insert(0, (self._now(), message))
        del self.events[20:]

    async def _query_players(self) -> list[str] | None:
        """Return the online players, or None if RCON failed."""
        try:
            players = await self._game.players()
        except Exception as exc:  # noqa: BLE001 - any failure means "unknown"
            self.rcon_error = str(exc) or type(exc).__name__
            self.players = None
            log.warning("players query failed: %s", self.rcon_error)
            return None
        self.rcon_error = None
        self.players = players
        self.players_checked_at = self._now()
        self._players_checked_mono = self._clock()
        return players

    async def _broadcast(self, message: str) -> None:
        try:
            await self._game.broadcast(message)
        except Exception as exc:  # noqa: BLE001
            log.warning("servermsg failed: %s", exc)

    async def _restart(self, user: str) -> bool:
        try:
            await self._restarter.restart()
        except Exception as exc:  # noqa: BLE001
            self._event(f"Restart requested by {user} failed: {exc}")
            return False
        self.pending = None
        self._event(f"Server restarted for {user} (nobody was online)")
        return True

    def snapshot(self) -> Snapshot:
        return Snapshot(
            players=self.players,
            players_checked_at=self.players_checked_at,
            rcon_error=self.rcon_error,
            pending=self.pending,
            events=list(self.events),
        )

    # -- actions -----------------------------------------------------------

    async def refresh(self) -> None:
        """Refresh the player list for display, at most every players_cache_seconds."""
        async with self._lock:
            last = self._players_checked_mono
            if last is None or self._clock() - last >= self._players_cache or self.rcon_error:
                await self._query_players()

    async def request_restart(self, user: str) -> str:
        async with self._lock:
            if self.pending is not None:
                return f"A restart is already pending (requested by {self.pending.requested_by})."
            players = await self._query_players()
            if players is None:
                self._event(f"Restart requested by {user} refused: server unreachable over RCON")
                return "Server unreachable over RCON, so it is not safe to restart. Try later."
            if not players:
                if await self._restart(user):
                    return "Nobody was online. The server is restarting."
                return "Restart failed; see the event log."
            now = self._clock()
            self.pending = Pending(user, self._now(), now, now)
            self._event(f"Restart requested by {user}; waiting for {len(players)} player(s)")
            await self._broadcast(
                f"{display_name(user)} wants to join - mods have changed. "
                "Server will restart when everyone logs off."
            )
            return f"{len(players)} player(s) online. They were notified; restart when empty."

    async def cancel(self, user: str) -> str:
        async with self._lock:
            if self.pending is None:
                return "No restart is pending."
            self.pending = None
            self._event(f"Pending restart cancelled by {user}")
            await self._broadcast(f"Restart request cancelled by {display_name(user)}.")
            return "Pending restart cancelled."

    async def tick(self) -> None:
        """Advance a pending request. Called every poll interval."""
        async with self._lock:
            pending = self.pending
            if pending is None:
                return
            players = await self._query_players()
            if players == []:
                # Someone may have joined since the last poll: check once more right before.
                players = await self._query_players()
                if players == []:
                    await self._restart(pending.requested_by)
                    return
            now = self._clock()
            if now - pending.started >= self._timeout:
                self.pending = None
                self._event(
                    f"Restart request by {pending.requested_by} expired; players still online"
                )
                await self._broadcast("Restart request expired; the server will keep running.")
                return
            if players and now - pending.last_reminder >= self._reminder_interval:
                pending.last_reminder = now
                await self._broadcast(
                    f"Reminder: {display_name(pending.requested_by)} is waiting to join "
                    "(mods changed). Server restarts once everyone logs off."
                )
