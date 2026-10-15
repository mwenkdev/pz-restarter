from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_SA_NAMESPACE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")


def _int(env: dict[str, str], name: str, default: int, minimum: int = 1) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _default_namespace() -> str:
    try:
        return _SA_NAMESPACE.read_text().strip() or "pz"
    except OSError:
        return "pz"


@dataclass(frozen=True)
class Settings:
    rcon_host: str
    rcon_port: int
    rcon_password: str
    rcon_timeout_seconds: int
    namespace: str
    deployment: str
    poll_interval_seconds: int
    reminder_interval_minutes: int
    pending_timeout_minutes: int
    user_header: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ) if env is None else env
        password = env.get("RCON_PASSWORD", "")
        if not password:
            raise ValueError("RCON_PASSWORD is required")
        return cls(
            rcon_host=env.get("RCON_HOST", "pz-server-rcon"),
            rcon_port=_int(env, "RCON_PORT", 27015),
            rcon_password=password,
            rcon_timeout_seconds=_int(env, "RCON_TIMEOUT_SECONDS", 5),
            namespace=env.get("TARGET_NAMESPACE") or _default_namespace(),
            deployment=env.get("TARGET_DEPLOYMENT", "pz-server"),
            poll_interval_seconds=_int(env, "POLL_INTERVAL_SECONDS", 30),
            reminder_interval_minutes=_int(env, "REMINDER_INTERVAL_MINUTES", 10),
            pending_timeout_minutes=_int(env, "PENDING_TIMEOUT_MINUTES", 30),
            user_header=env.get("USER_HEADER", "cf-access-authenticated-user-email").lower(),
        )
