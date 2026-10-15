import asyncio

import pytest

from pz_restarter.controller import Controller
from pz_restarter.rcon import RconError


class FakeGame:
    def __init__(self, players=None):
        # Each players() call pops from `script` if set, otherwise returns `players`.
        self.players_value = players if players is not None else []
        self.script: list = []
        self.messages: list[str] = []
        self.calls = 0

    async def players(self):
        self.calls += 1
        value = self.script.pop(0) if self.script else self.players_value
        if isinstance(value, Exception):
            raise value
        return list(value)

    async def broadcast(self, message):
        self.messages.append(message)


class FakeRestarter:
    def __init__(self):
        self.restarts = 0
        self.fail = False

    async def restart(self):
        if self.fail:
            raise RuntimeError("forbidden")
        self.restarts += 1


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env():
    game, restarter, clock = FakeGame(), FakeRestarter(), Clock()
    ctl = Controller(
        game,
        restarter,
        reminder_interval_seconds=600,
        pending_timeout_seconds=1800,
        clock=clock,
    )
    return game, restarter, clock, ctl


def run(coro):
    return asyncio.run(coro)


def test_restart_immediately_when_empty(env):
    game, restarter, _, ctl = env
    msg = run(ctl.request_restart("alice@example.com"))
    assert restarter.restarts == 1
    assert ctl.pending is None
    assert game.messages == []
    assert "restarting" in msg


def test_refuse_when_rcon_down(env):
    game, restarter, _, ctl = env
    game.script = [RconError("refused")]
    msg = run(ctl.request_restart("alice@example.com"))
    assert restarter.restarts == 0
    assert ctl.pending is None
    assert "unreachable" in msg


def test_wait_when_players_online(env):
    game, restarter, _, ctl = env
    game.players_value = ["bob"]
    run(ctl.request_restart("alice@example.com"))
    assert restarter.restarts == 0
    assert ctl.pending.requested_by == "alice@example.com"
    assert len(game.messages) == 1
    assert "alice wants to join" in game.messages[0]
    assert "example.com" not in game.messages[0]


def test_second_request_does_not_stack(env):
    game, _, _, ctl = env
    game.players_value = ["bob"]
    run(ctl.request_restart("alice@example.com"))
    msg = run(ctl.request_restart("carol@example.com"))
    assert "already pending" in msg
    assert ctl.pending.requested_by == "alice@example.com"
    assert len(game.messages) == 1


def test_tick_restarts_once_empty_with_recheck(env):
    game, restarter, clock, ctl = env
    game.players_value = ["bob"]
    run(ctl.request_restart("alice@example.com"))
    clock.t += 30
    game.players_value = []
    calls_before = game.calls
    run(ctl.tick())
    assert restarter.restarts == 1
    assert game.calls - calls_before == 2  # poll + recheck right before restart
    assert ctl.pending is None


def test_tick_join_race_prevents_restart(env):
    game, restarter, clock, ctl = env
    game.players_value = ["bob"]
    run(ctl.request_restart("alice@example.com"))
    clock.t += 30
    game.script = [[], ["dave"]]  # empty on poll, someone joined before restart
    run(ctl.tick())
    assert restarter.restarts == 0
    assert ctl.pending is not None


def test_tick_rcon_down_keeps_waiting(env):
    game, restarter, clock, ctl = env
    game.players_value = ["bob"]
    run(ctl.request_restart("alice@example.com"))
    clock.t += 30
    game.script = [RconError("down")]
    run(ctl.tick())
    assert restarter.restarts == 0
    assert ctl.pending is not None
    assert ctl.rcon_error == "down"


def test_recheck_failure_prevents_restart(env):
    game, restarter, clock, ctl = env
    game.players_value = ["bob"]
    run(ctl.request_restart("alice@example.com"))
    clock.t += 30
    game.script = [[], RconError("down")]
    run(ctl.tick())
    assert restarter.restarts == 0
    assert ctl.pending is not None


def test_reminders(env):
    game, _, clock, ctl = env
    game.players_value = ["bob"]
    run(ctl.request_restart("alice@example.com"))
    clock.t += 300
    run(ctl.tick())
    assert len(game.messages) == 1
    clock.t += 300
    run(ctl.tick())
    assert len(game.messages) == 2
    assert game.messages[1].startswith("Reminder: alice")
    clock.t += 30
    run(ctl.tick())
    assert len(game.messages) == 2


def test_timeout_expires_request(env):
    game, restarter, clock, ctl = env
    game.players_value = ["bob"]
    run(ctl.request_restart("alice@example.com"))
    clock.t += 1800
    run(ctl.tick())
    assert ctl.pending is None
    assert restarter.restarts == 0
    assert "expired" in game.messages[-1]
    assert "expired" in ctl.events[0][1]


def test_empty_at_timeout_still_restarts(env):
    game, restarter, clock, ctl = env
    game.players_value = ["bob"]
    run(ctl.request_restart("alice@example.com"))
    clock.t += 1800
    game.players_value = []
    run(ctl.tick())
    assert restarter.restarts == 1


def test_cancel(env):
    game, restarter, clock, ctl = env
    game.players_value = ["bob"]
    run(ctl.request_restart("alice@example.com"))
    run(ctl.cancel("carol@example.com"))
    assert ctl.pending is None
    assert "cancelled by carol" in game.messages[-1]
    game.players_value = []
    clock.t += 30
    run(ctl.tick())
    assert restarter.restarts == 0


def test_cancel_nothing_pending(env):
    _, _, _, ctl = env
    assert run(ctl.cancel("alice@example.com")) == "No restart is pending."


def test_restart_api_failure_keeps_pending(env):
    game, restarter, clock, ctl = env
    game.players_value = ["bob"]
    run(ctl.request_restart("alice@example.com"))
    restarter.fail = True
    game.players_value = []
    clock.t += 30
    run(ctl.tick())
    assert ctl.pending is not None
    assert "failed" in ctl.events[0][1]
