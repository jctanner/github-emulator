"""GitHub Actions event-trigger matching."""

import fnmatch

def evaluate_trigger(workflow_yaml: dict, event: str, payload: dict) -> bool:
    """Check if a workflow's `on:` configuration matches the given event and payload."""
    on_config = workflow_yaml.get("on") or workflow_yaml.get(True)
    if on_config is None:
        return False

    if isinstance(on_config, str):
        return on_config == event

    if isinstance(on_config, list):
        return event in on_config

    if isinstance(on_config, dict):
        event_config = on_config.get(event)
        if event_config is None and event not in on_config:
            return False

        # PyYAML may parse an empty mapping as {}, and GitHub treats both an
        # empty mapping and a null value as the event's default configuration.
        if event_config is None or event_config == {}:
            return True

        if event == "push":
            return _match_push(event_config, payload)
        if event in ("pull_request", "pull_request_target"):
            return _match_pull_request(event_config, payload)
        if event in ("issues", "issue_comment", "pull_request_review"):
            return _match_types(event_config, payload)
        if event == "workflow_dispatch":
            return True

        return True

    return False


def _match_push(config: dict, payload: dict) -> bool:
    if not isinstance(config, dict):
        return True
    ref = payload.get("ref", "")
    branch = ref.removeprefix("refs/heads/")
    tag = ref.removeprefix("refs/tags/")

    if "branches" in config:
        patterns = config["branches"]
        if not any(fnmatch.fnmatch(branch, p) for p in patterns):
            return False

    if "branches-ignore" in config:
        patterns = config["branches-ignore"]
        if any(fnmatch.fnmatch(branch, p) for p in patterns):
            return False

    if "tags" in config:
        patterns = config["tags"]
        if not ref.startswith("refs/tags/"):
            return False
        if not any(fnmatch.fnmatch(tag, p) for p in patterns):
            return False

    if "tags-ignore" in config:
        patterns = config["tags-ignore"]
        if ref.startswith("refs/tags/") and any(fnmatch.fnmatch(tag, p) for p in patterns):
            return False

    if "paths" in config:
        changed = _get_changed_files(payload)
        if not any(fnmatch.fnmatch(f, p) for f in changed for p in config["paths"]):
            return False

    if "paths-ignore" in config:
        changed = _get_changed_files(payload)
        if all(
            any(fnmatch.fnmatch(f, p) for p in config["paths-ignore"])
            for f in changed
        ):
            return False

    return True


def _match_pull_request(config: dict, payload: dict) -> bool:
    if not isinstance(config, dict):
        return True
    if "types" in config:
        action = payload.get("action", "opened")
        if action not in config["types"]:
            return False

    if "branches" in config:
        base_branch = payload.get("pull_request", {}).get("base", {}).get("ref", "")
        if not any(fnmatch.fnmatch(base_branch, p) for p in config["branches"]):
            return False

    if "branches-ignore" in config:
        base_branch = payload.get("pull_request", {}).get("base", {}).get("ref", "")
        if any(fnmatch.fnmatch(base_branch, p) for p in config["branches-ignore"]):
            return False

    return True


def _match_types(config: object, payload: dict) -> bool:
    """Apply the common ``types`` allowlist used by activity triggers."""
    if not isinstance(config, dict):
        return True
    types = config.get("types")
    if types is None:
        return True
    if isinstance(types, str):
        types = [types]
    return payload.get("action", "") in types


def _get_changed_files(payload: dict) -> list[str]:
    files = []
    for commit in payload.get("commits", []):
        files.extend(commit.get("added", []))
        files.extend(commit.get("modified", []))
        files.extend(commit.get("removed", []))
    return files
