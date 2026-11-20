"""scripts/ci-seed-mint-apps.py against the app: app sets and later runs get their own App ids."""

import asyncio
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ci-seed-mint-apps.py"


def _load_script():
    spec = importlib.util.spec_from_file_location("ci_seed_mint_apps", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def seeder(client, admin_token, monkeypatch, tmp_path, capsys):
    """Run the script's seed() in a thread, its HTTP calls answered by the app."""
    module = _load_script()
    loop = asyncio.get_running_loop()

    def call(base, token, method, path, body=None, prefix="/api/v3"):
        async def request():
            resp = await client.request(method, f"{prefix}{path}", json=body,
                                        headers={"Authorization": f"token {token}"})
            return resp.status_code, (resp.json() if resp.content else None)
        return asyncio.run_coroutine_threadsafe(request(), loop).result()

    monkeypatch.setattr(module, "call", call)

    async def seed(roles, app_set="fullsend"):
        monkeypatch.setenv("APP_SET", app_set)
        await asyncio.to_thread(module.seed, "http://testserver", admin_token, "seed-org",
                                str(tmp_path / f"pem-{app_set}"), roles)
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    return seed


@pytest.mark.asyncio
async def test_two_app_sets_get_their_own_apps(seeder):
    first = await seeder(["triage", "coder"])
    second = await seeder(["triage", "coder"], app_set="other")
    assert set(first) == set(second) == {"triage", "coder"}
    assert not set(first.values()) & set(second.values())


@pytest.mark.asyncio
async def test_custom_roles_seeded_one_run_at_a_time(seeder):
    first = await seeder(["custom-a"])
    second = await seeder(["custom-b"])
    assert first["custom-a"] != second["custom-b"]


@pytest.mark.asyncio
async def test_seeding_again_reuses_the_apps(seeder, tmp_path):
    first = await seeder(["triage", "e2e"])
    again = await seeder(["triage", "e2e"])
    assert again == first
    # e2e is seeded with a key but is not a mint role.
    assert "e2e" not in first
    assert (tmp_path / "pem-fullsend" / "e2e.pem").read_text().startswith("-----BEGIN")
