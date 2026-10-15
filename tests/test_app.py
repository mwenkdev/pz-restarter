import pytest
from fastapi.testclient import TestClient

from pz_restarter.app import create_app
from pz_restarter.config import Settings
from pz_restarter.controller import Controller
from tests.test_controller import FakeGame, FakeRestarter

USER = {"Cf-Access-Authenticated-User-Email": "alice@example.com"}


@pytest.fixture
def setup():
    game, restarter = FakeGame(), FakeRestarter()
    ctl = Controller(game, restarter, reminder_interval_seconds=600, pending_timeout_seconds=1800)
    app = create_app(
        ctl,
        user_header="cf-access-authenticated-user-email",
        poll_interval_seconds=30,
        run_poller=False,
    )
    with TestClient(app, base_url="https://pz.example.com") as client:
        yield client, game, restarter


def test_healthz_needs_no_auth(setup):
    client, *_ = setup
    assert client.get("/healthz").text == "ok"


@pytest.mark.parametrize("method,path", [("get", "/"), ("post", "/restart"), ("post", "/cancel")])
def test_requires_access_header(setup, method, path):
    client, _, restarter = setup
    assert getattr(client, method)(path).status_code == 403
    assert restarter.restarts == 0


def test_restart_flow(setup):
    client, _, restarter = setup
    resp = client.post("/restart", headers=USER)
    assert resp.status_code == 200  # followed the 303
    assert restarter.restarts == 1
    assert "Nobody was online" in resp.text


def test_cross_origin_post_rejected(setup):
    client, _, restarter = setup
    resp = client.post("/restart", headers={**USER, "Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert restarter.restarts == 0


def test_same_origin_post_allowed(setup):
    client, _, restarter = setup
    client.post("/restart", headers={**USER, "Origin": "https://pz.example.com"})
    assert restarter.restarts == 1


def test_page_shows_players_and_escapes(setup):
    client, game, _ = setup
    game.players_value = ["<script>bob</script>"]
    text = client.get("/", headers=USER).text
    assert "1 online" in text
    assert "&lt;script&gt;bob" in text
    assert "<script>bob" not in text


def test_settings_defaults_and_validation():
    s = Settings.from_env({"RCON_PASSWORD": "x", "TARGET_NAMESPACE": "pz"})
    assert (s.poll_interval_seconds, s.reminder_interval_minutes, s.pending_timeout_minutes) == (
        30,
        10,
        30,
    )
    s = Settings.from_env({"RCON_PASSWORD": "x", "PENDING_TIMEOUT_MINUTES": "45"})
    assert s.pending_timeout_minutes == 45
    with pytest.raises(ValueError):
        Settings.from_env({"RCON_PASSWORD": "x", "PENDING_TIMEOUT_MINUTES": "0"})
    with pytest.raises(ValueError):
        Settings.from_env({})
