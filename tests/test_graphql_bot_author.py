"""An App-authored issue or pull request has a Bot author over GraphQL.

GitHub types `author` as the Actor interface: a User, or a Bot whose login is
the App slug without the "[bot]" suffix REST shows. gh asks for
`author{login,...on User{id,name}}` and reports an author without an id as
`{"is_bot": true, "login": "app/<login>"}`, which scripts use to recognise
an App. The emulator returned every author as a User, so gh saw the bot as a
person named "fullsend-test-coder[bot]".
"""

import pytest
from sqlalchemy import select

from app.models.issue import Issue
from app.models.user import User
from tests.conftest import API, auth_headers

GH_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) { author { __typename login ... on User { id name } } }
  }
}
"""


async def _author(client, test_token, number):
    resp = await client.post(
        "/graphql",
        json={"query": GH_QUERY, "variables": {"owner": "testuser", "repo": "bot-authors", "number": number}},
        headers=auth_headers(test_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "errors" not in body, body
    return body["data"]["repository"]["issue"]["author"]


@pytest.mark.asyncio
async def test_an_app_bot_author_is_a_bot_with_the_bare_slug(client, db_session, test_user, test_token):
    await client.post(f"{API}/user/repos", json={"name": "bot-authors"}, headers=auth_headers(test_token))
    human = await client.post(
        f"{API}/repos/testuser/bot-authors/issues", json={"title": "by a person"},
        headers=auth_headers(test_token),
    )
    by_bot = await client.post(
        f"{API}/repos/testuser/bot-authors/issues", json={"title": "by an app"},
        headers=auth_headers(test_token),
    )
    bot = User(login="fullsend-test-coder[bot]", type="Bot", hashed_password="!")
    db_session.add(bot)
    await db_session.flush()
    issue = (await db_session.execute(
        select(Issue).where(Issue.number == by_bot.json()["number"])
    )).scalar_one()
    issue.user_id = bot.id
    await db_session.commit()

    author = await _author(client, test_token, by_bot.json()["number"])
    # No id: that is how gh tells a bot, and prints it as "app/<login>".
    assert author == {"__typename": "Bot", "login": "fullsend-test-coder"}

    person = await _author(client, test_token, human.json()["number"])
    assert person["__typename"] == "User"
    assert person["login"] == "testuser"
    assert person["id"]

    # REST keeps GitHub's REST shape for the same account.
    rest = await client.get(
        f"{API}/repos/testuser/bot-authors/issues/{by_bot.json()['number']}",
        headers=auth_headers(test_token),
    )
    assert rest.json()["user"]["login"] == "fullsend-test-coder[bot]"
    assert rest.json()["user"]["type"] == "Bot"
