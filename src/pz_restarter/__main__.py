from __future__ import annotations

import logging
import os

import uvicorn

from .app import create_app
from .config import Settings
from .controller import Controller
from .game import GameServer
from .k8s import DeploymentRestarter, load_kube_config
from .rcon import RconClient


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    settings = Settings.from_env()
    load_kube_config()
    restarter = DeploymentRestarter(settings.namespace, settings.deployment)
    game = GameServer(
        RconClient(
            settings.rcon_host,
            settings.rcon_port,
            settings.rcon_password,
            settings.rcon_timeout_seconds,
        )
    )
    controller = Controller(
        game,
        restarter,
        reminder_interval_seconds=settings.reminder_interval_minutes * 60,
        pending_timeout_seconds=settings.pending_timeout_minutes * 60,
    )
    app = create_app(
        controller,
        user_header=settings.user_header,
        poll_interval_seconds=settings.poll_interval_seconds,
        pod_lister=restarter.pods,
        pending_timeout_minutes=settings.pending_timeout_minutes,
    )
    logging.getLogger(__name__).info(
        "managing %s/%s via RCON %s:%s (poll %ss, reminder %smin, timeout %smin)",
        settings.namespace,
        settings.deployment,
        settings.rcon_host,
        settings.rcon_port,
        settings.poll_interval_seconds,
        settings.reminder_interval_minutes,
        settings.pending_timeout_minutes,
    )
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
        proxy_headers=True,
        forwarded_allow_ips="*",
        access_log=False,
    )


if __name__ == "__main__":
    main()
