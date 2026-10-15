"""Project Zomboid commands over RCON."""

from __future__ import annotations

import asyncio
import re

from .rcon import RconClient, RconError

_HEADER = re.compile(r"Players connected \((\d+)\)")


def parse_players(output: str) -> list[str]:
    """Parse `players` output, e.g. "Players connected (2): \\n-alice\\n-bob\\n".

    Raises RconError if the output is not recognised, so callers never mistake an
    unexpected reply for an empty server.
    """
    match = _HEADER.search(output)
    if not match:
        raise RconError(f"unrecognised players output: {output[:200]!r}")
    count = int(match.group(1))
    names = [
        line.strip()[1:].strip()
        for line in output[match.end() :].splitlines()
        if line.strip().startswith("-")
    ]
    if len(names) != count:
        # Trust the count; pad with placeholders so emptiness is never misjudged.
        names = (names + ["(unknown)"] * count)[:count]
    return names


def sanitize_message(text: str) -> str:
    """Make text safe to place inside servermsg "..."."""
    cleaned = re.sub(r"[\"\\\r\n\t]", "", text)
    return cleaned[:200]


class GameServer:
    def __init__(self, client: RconClient) -> None:
        self._client = client

    async def players(self) -> list[str]:
        output = await asyncio.to_thread(self._client.command, "players")
        return parse_players(output)

    async def broadcast(self, message: str) -> None:
        await asyncio.to_thread(self._client.command, f'servermsg "{sanitize_message(message)}"')
