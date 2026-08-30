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
        ("GET", "/api/_ui/session"),
        ("POST", "/api/_ui/session"),
        ("DELETE", "/api/_ui/session"),
        ("GET", "/api/_ui/repos/{owner}/{repo}/summary"),
        ("GET", "/api/_ui/repos/{owner}/{repo}/navigation"),
        ("POST", "/admin/api/apps"),
        ("GET", "/api/v3/repos/{owner}/{repo}/actions/runs"),
        ("GET", "/_apis/connectionData"),
        ("GET", "/_apis/distributedtask/session/{session_id}/messages"),
    }
    assert expected <= routes
    assert ("GET", "/api/v3/session") not in routes
    assert ("GET", "/api/_ui/v1/session") not in routes
    assert (
        "GET",
        "/api/_ui/v1/repos/{owner}/{repo}/summary",
    ) not in routes
    assert ("GET", "/_browser/api/v1/session") not in routes
    assert (
        "GET",
        "/_browser/api/v1/repos/{owner}/{repo}/summary",
    ) not in routes
    assert (
        "GET",
        "/api/v3/browser/repos/{owner}/{repo}/summary",
    ) not in routes

    mounts = {getattr(route, "path", None) for route in app.routes}
    assert "/ui" in mounts
    assert "/ui-legacy" in mounts
