# pz-restarter

A small web page for restarting the Project Zomboid dedicated server when the
Workshop mods have changed and a client can no longer connect. A restart makes
the server pull the latest mods.

It never interrupts anyone who is playing:

- **Nobody online:** pressing **Restart server** restarts the Deployment right away.
- **Players online:** they get an in-game `servermsg` saying someone wants to join.
  The service polls `players` over RCON and restarts once the server is empty,
  checking again immediately before restarting. Reminders are broadcast
  periodically, and the request expires after a configurable timeout.
- **RCON unreachable:** nothing is restarted, because emptiness can't be confirmed.

Restarting patches `kubectl.kubernetes.io/restartedAt` on the Deployment (like
`kubectl rollout restart`), so the pod gets SIGTERM and the server saves the world.

Authentication is handled by Cloudflare Access in front of the service. Requests
without the `Cf-Access-Authenticated-User-Email` header get 403, and the email's
local part is used to say who asked. Cross-origin form posts are rejected.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `RCON_PASSWORD` | required | Server RCON password |
| `RCON_HOST` / `RCON_PORT` | `pz-server-rcon` / `27015` | RCON endpoint |
| `RCON_TIMEOUT_SECONDS` | `5` | Connect/read timeout |
| `TARGET_NAMESPACE` | pod's namespace | Namespace of the game Deployment |
| `TARGET_DEPLOYMENT` | `pz-server` | Deployment to restart |
| `POLL_INTERVAL_SECONDS` | `30` | How often a pending request checks players |
| `REMINDER_INTERVAL_MINUTES` | `10` | How often waiting players are reminded |
| `PENDING_TIMEOUT_MINUTES` | `30` | When a pending request expires |
| `USER_HEADER` | `cf-access-authenticated-user-email` | Header naming the user |
| `PORT` | `8080` | HTTP port |

The ServiceAccount needs `get`/`patch` on the Deployment and `list` on pods.
Pending state is in memory; restarting this service forgets a pending request.

## Development

```sh
uv venv && uv pip install -e '.[dev]'
.venv/bin/pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

Images are published to `ghcr.io/mwenkdev/pz-restarter:<tag>` when a GitHub
release is created.
