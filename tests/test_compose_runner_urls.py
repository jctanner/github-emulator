"""The Compose runner services reach the emulator at an address that answers.

Inside the Compose network the emulator answers plain HTTP on port 8000
(uvicorn), and HTTPS on 443 only for the names Caddy has a site for. A runner
URL of ``https://github-emulator`` fails the TLS handshake because Caddy has no
certificate for that name (issue #81).
"""

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

REPO_ROOT = Path(__file__).parents[1]
COMPOSE = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
SERVICES = COMPOSE["services"]
EMULATOR_SERVICE = "github-emulator"
UVICORN_PORT = 8000


def _environment(service):
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {key: str(value) for key, value in env.items()}
    return dict(item.split("=", 1) for item in env if "=" in item)


def _emulator_names():
    """Names other services can use to reach the emulator container."""
    names = {EMULATOR_SERVICE}
    networks = SERVICES[EMULATOR_SERVICE].get("networks") or {}
    if isinstance(networks, dict):
        for network in networks.values():
            names.update((network or {}).get("aliases") or [])
    return names


def _caddy_site_names():
    """Host names the Caddyfile serves (and so has certificates for)."""
    env = _environment(SERVICES[EMULATOR_SERVICE])
    names = set()
    for line in (REPO_ROOT / "Caddyfile").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.endswith("{") or line.startswith("#"):
            continue
        address = line[:-1].strip()
        # Caddy's {$VAR:default} placeholder, filled from the container env.
        address = re.sub(
            r"\{\$(\w+)(?::([^}]*))?\}",
            lambda m: env.get(m.group(1), m.group(2) or ""),
            address,
        )
        for site in address.replace(",", " ").split():
            names.add(urlsplit(site if "://" in site else f"//{site}").hostname)
    return names


RUNNER_URLS = {
    name: _environment(service)["GITHUB_EMULATOR_URL"]
    for name, service in SERVICES.items()
    if "GITHUB_EMULATOR_URL" in _environment(service)
}


def test_compose_has_runner_services():
    assert {"actions-runner", "actions-real-runner"} <= set(RUNNER_URLS)


@pytest.mark.parametrize("service", sorted(RUNNER_URLS))
def test_runner_url_reaches_the_emulator(service):
    url = urlsplit(RUNNER_URLS[service])
    assert url.hostname in _emulator_names(), f"{service}: {url.hostname} is not a name for the emulator"
    if url.scheme == "https":
        assert url.hostname in _caddy_site_names(), (
            f"{service}: Caddy has no site or certificate for {url.hostname}"
        )
        assert url.port in (None, 443)
    else:
        assert url.scheme == "http"
        assert url.port == UVICORN_PORT, f"{service}: plain HTTP is served on port {UVICORN_PORT}"
