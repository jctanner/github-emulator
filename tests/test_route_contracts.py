"""Small route inventories for externally important emulator boundaries."""

def _routes(app) -> set[tuple[str, str]]:
    return {
        (method.upper(), path)
        for path, operations in app.openapi()["paths"].items()
        for method in operations
    }


def test_ui_admin_api_and_actions_route_contracts(app):
    routes = _routes(app)
    expected = {
        ("GET", "/ui/{owner}"),
        ("GET", "/ui/_admin/login"),
        ("GET", "/ui/{owner}/{repo_name}/settings"),
        ("GET", "/ui/{owner}/{repo_name}/settings/installations"),
        ("POST", "/admin/api/apps"),
        ("GET", "/api/v3/repos/{owner}/{repo}/actions/runs"),
        ("GET", "/_apis/connectionData"),
        ("GET", "/_apis/distributedtask/session/{session_id}/messages"),
    }
    assert expected <= routes

    mounts = {getattr(route, "path", None) for route in app.routes}
    assert "/ui/_admin/static" in mounts
    assert "/ui/static" in mounts
