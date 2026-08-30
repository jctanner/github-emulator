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
        ("GET", "/api/v3/session"),
        ("POST", "/api/v3/session"),
        ("DELETE", "/api/v3/session"),
        ("POST", "/admin/api/apps"),
        ("GET", "/api/v3/repos/{owner}/{repo}/actions/runs"),
        ("GET", "/_apis/connectionData"),
        ("GET", "/_apis/distributedtask/session/{session_id}/messages"),
    }
    assert expected <= routes

    mounts = {getattr(route, "path", None) for route in app.routes}
    assert "/ui" in mounts
    assert "/ui-legacy" in mounts
