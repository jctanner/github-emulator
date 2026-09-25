"""A runner's reported OS is reduced to the token GitHub's API returns.

The upstream Actions runner registers with .NET's
RuntimeInformation.OSDescription, which on Linux is the full uname string.
Stored verbatim it put the host's kernel version, distribution build and build
date into an API response and onto the admin runners page — containers share
the host kernel, so this described the machine under the cluster.
"""

import pytest

from app.api.actions_runners import normalise_runner_os


@pytest.mark.parametrize(
    "reported, expected",
    [
        # The exact string that surfaced on the admin page.
        ("Linux 7.2.4-200.fc44.x86_64 #1 SMP PREEMPT_DYNAMIC Mon Sep  7 19:10:19 UTC 2026", "linux"),
        ("linux", "linux"),
        ("Linux", "linux"),
        ("Ubuntu 22.04.3 LTS (Linux 5.15)", "linux"),
        ("Darwin 23.5.0", "macos"),
        ("macOS 14.5", "macos"),
        ("Microsoft Windows 10.0.20348", "windows"),
        ("Windows", "windows"),
        # Absent or empty falls back rather than storing nothing.
        (None, "linux"),
        ("", "linux"),
        ("   ", "linux"),
    ],
)
def test_reported_os_is_reduced_to_a_platform(reported, expected):
    assert normalise_runner_os(reported) == expected


def test_an_unrecognised_platform_is_not_guessed():
    """Better visibly odd than confidently wrong.

    Mapping an unknown description onto linux would hide a runner that is not
    what the stack assumes; keeping its first token leaves it legible.
    """
    assert normalise_runner_os("Plan9 4.0 fossil") == "plan9"


def test_no_host_detail_survives():
    """The property that matters: nothing machine-specific is retained."""
    uname = "Linux 7.2.4-200.fc44.x86_64 #1 SMP PREEMPT_DYNAMIC Mon Sep  7 19:10:19 UTC 2026"
    result = normalise_runner_os(uname)
    for leak in ("7.2.4", "fc44", "x86_64", "SMP", "2026"):
        assert leak not in result


UNAME = "Linux 7.2.4-200.fc44.x86_64 #1 SMP PREEMPT_DYNAMIC Mon Sep  7 19:10:19 UTC 2026"
API = "/api/v3"


@pytest.mark.asyncio
async def test_registration_and_re_registration_both_normalise(client, test_token):
    """Through the endpoint, not the helper.

    The first fix patched only the paths that create a runner. The upstream
    runner re-registers with a token it already holds, which takes a
    different branch that assigned the description straight onto the row — so
    a restart put the uname string back and the page was unchanged. A unit
    test on the helper could not have caught that; this goes through both.
    """
    from tests.conftest import auth_headers

    created = await client.post(
        f"{API}/user/repos", json={"name": "os-probe-repo"},
        headers=auth_headers(test_token),
    )
    assert created.status_code in (200, 201), created.text

    token_resp = await client.post(
        f"{API}/repos/testuser/os-probe-repo/actions/runners/registration-token",
        headers=auth_headers(test_token),
    )
    assert token_resp.status_code == 200

    registered = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={
            "token": token_resp.json()["token"],
            "agentName": "os-probe",
            "labels": [{"name": "self-hosted"}],
            "osDescription": UNAME,
        },
    )
    assert registered.status_code == 200, registered.text
    runner_token = registered.json()["token"]

    listed = await client.get(
        f"{API}/repos/testuser/os-probe-repo/actions/runners",
        headers=auth_headers(test_token),
    )
    stored = next(
        r for r in listed.json()["runners"] if r["name"] == "os-probe"
    )
    assert stored["os"] == "linux", "registration kept the host uname string"

    # Re-register the way the upstream runner does on restart: no registration
    # token, authenticated with the runner token it already holds.
    again = await client.post(
        "/_apis/distributedtask/pools/1/agents",
        json={"name": "os-probe", "osDescription": UNAME},
        headers={"Authorization": f"Bearer {runner_token}"},
    )
    assert again.status_code == 200, again.text

    listed = await client.get(
        f"{API}/repos/testuser/os-probe-repo/actions/runners",
        headers=auth_headers(test_token),
    )
    stored = next(
        r for r in listed.json()["runners"] if r["name"] == "os-probe"
    )
    assert stored["os"] == "linux", "re-registration put the uname string back"
