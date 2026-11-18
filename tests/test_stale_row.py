"""An update of a row another request deleted answers 404, not 500."""

import pytest
from sqlalchemy.orm.exc import StaleDataError


@pytest.mark.asyncio
async def test_update_of_deleted_row_is_404(app, client):
    async def gone():
        raise StaleDataError("UPDATE statement on table 'workflow_jobs' expected to update 1 row(s); 0 were matched.")

    app.add_api_route("/_test/stale", gone, methods=["POST"])
    resp = await client.post("/_test/stale")
    assert resp.status_code == 404
    assert resp.json()["message"] == "Not Found"
