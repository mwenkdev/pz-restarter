"""Restart the game Deployment and report its pod status through the Kubernetes API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from kubernetes import client, config

RESTARTED_AT = "kubectl.kubernetes.io/restartedAt"


@dataclass(frozen=True)
class PodStatus:
    name: str
    phase: str
    ready: bool
    started_at: datetime | None


def load_kube_config() -> None:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()


class DeploymentRestarter:
    def __init__(self, namespace: str, deployment: str) -> None:
        self.namespace = namespace
        self.deployment = deployment
        self._apps = client.AppsV1Api()
        self._core = client.CoreV1Api()

    async def restart(self) -> None:
        """Same as `kubectl rollout restart`: the pod gets SIGTERM, so the world saves."""
        stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = {"spec": {"template": {"metadata": {"annotations": {RESTARTED_AT: stamp}}}}}
        await asyncio.to_thread(
            self._apps.patch_namespaced_deployment, self.deployment, self.namespace, body
        )

    async def pods(self) -> list[PodStatus]:
        return await asyncio.to_thread(self._pods)

    def _pods(self) -> list[PodStatus]:
        deployment = self._apps.read_namespaced_deployment(self.deployment, self.namespace)
        labels = deployment.spec.selector.match_labels or {}
        selector = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        pods = self._core.list_namespaced_pod(self.namespace, label_selector=selector)
        result = []
        for pod in pods.items:
            statuses = pod.status.container_statuses or []
            result.append(
                PodStatus(
                    name=pod.metadata.name,
                    phase=pod.status.phase or "Unknown",
                    ready=bool(statuses) and all(s.ready for s in statuses),
                    started_at=pod.status.start_time,
                )
            )
        return result
