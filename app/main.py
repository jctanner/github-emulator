"""GitHub Emulator -- FastAPI application factory.

This is the main entry point that wires together all routers,
middleware, the GraphQL schema, the admin frontend, and the
Git Smart HTTP protocol handler.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db, async_session, get_db

logger = logging.getLogger("github_emulator")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialise the database on startup."""
    os.makedirs(settings.DATA_DIR, exist_ok=True)
    os.makedirs(os.path.join(settings.DATA_DIR, "repos"), exist_ok=True)

    await init_db()
    await _ensure_admin_user()

    logger.info("GitHub Emulator started at %s", settings.BASE_URL)

    # Start SSH server
    ssh_server = None
    try:
        from app.git.ssh_server import start_ssh_server
        ssh_server = await start_ssh_server()
    except Exception:
        logger.warning("SSH server failed to start")

    yield

    # Stop SSH server
    if ssh_server is not None:
        try:
            ssh_server.close()
        except Exception:
            pass

    logger.info("GitHub Emulator shutting down")


async def _ensure_admin_user():
    """Create the default admin user and token if no users exist."""
    from app.models.user import User
    from app.models.token import PersonalAccessToken
    from sqlalchemy import select, func

    from app.services.auth_service import hash_password

    async with async_session() as db:
        count = (await db.execute(select(func.count(User.id)))).scalar() or 0
        if count > 0:
            return

        hashed = hash_password(settings.ADMIN_PASSWORD)
        admin = User(
            login=settings.ADMIN_USERNAME,
            hashed_password=hashed,
            name="Admin",
            email="admin@github-emulator.local",
            site_admin=True,
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)

        logger.info(
            "Created admin user: %s (password: %s)",
            settings.ADMIN_USERNAME,
            settings.ADMIN_PASSWORD,
        )


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="GitHub Emulator",
        description="A GitHub-compatible API emulator for integration testing",
        version="0.1.0",
        lifespan=lifespan,
    )

    # -- Middleware (applied in reverse order) --------------------------------
    from app.middleware.rate_limit import RateLimitMiddleware
    from app.middleware.api_version import ApiVersionMiddleware
    from app.middleware.etag import ETagMiddleware
    from app.middleware.error_handler import register_error_handlers

    app.add_middleware(ApiVersionMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(ETagMiddleware)
    register_error_handlers(app)

    # -- REST API routers -----------------------------------------------------
    from app.api.root import router as root_router
    from app.api.users import router as users_router
    from app.api.repos import router as repos_router
    from app.api.issues import router as issues_router
    from app.api.pulls import router as pulls_router
    from app.api.comments import router as comments_router
    from app.api.labels import router as labels_router
    from app.api.milestones import router as milestones_router
    from app.api.branches import router as branches_router
    from app.api.commits import router as commits_router
    from app.api.contents import router as contents_router
    from app.api.git_refs import router as git_refs_router
    from app.api.git_commits import router as git_commits_router
    from app.api.git_trees import router as git_trees_router
    from app.api.git_blobs import router as git_blobs_router
    from app.api.git_tags import router as git_tags_router
    from app.api.webhooks import router as webhooks_router
    from app.api.statuses import router as statuses_router
    from app.api.check_runs import router as check_runs_router
    from app.api.releases import router as releases_router
    from app.api.collaborators import router as collaborators_router
    from app.api.forks import router as forks_router
    from app.api.reactions import router as reactions_router
    from app.api.events import router as events_router
    from app.api.search import router as search_router
    from app.api.orgs import router as orgs_router
    from app.api.teams import router as teams_router
    from app.api.notifications import router as notifications_router
    from app.api.gists import router as gists_router
    from app.api.starring import router as starring_router
    from app.api.reviews import router as reviews_router
    from app.api.oauth import router as oauth_router
    from app.api.actions import router as actions_router
    from app.api.markdown import router as markdown_router
    from app.api.emojis import router as emojis_router
    from app.api.gitignore import router as gitignore_router
    from app.api.licenses import router as licenses_router
    from app.api.user_keys import router as user_keys_router
    from app.api.deploy_keys import router as deploy_keys_router
    from app.api.review_comments import router as review_comments_router

    # -- REST API routers (under /api/v3/ prefix) ----------------------------
    api_routers = [
        users_router, repos_router, issues_router, pulls_router,
        comments_router, labels_router, milestones_router, branches_router,
        commits_router, contents_router, git_refs_router, git_commits_router,
        git_trees_router, git_blobs_router, git_tags_router, webhooks_router,
        statuses_router, check_runs_router, releases_router,
        collaborators_router, forks_router, reactions_router, events_router,
        search_router, orgs_router, teams_router, notifications_router,
        gists_router, starring_router, reviews_router,
        actions_router, markdown_router, emojis_router, gitignore_router,
        licenses_router, user_keys_router, deploy_keys_router,
        review_comments_router,
    ]
    for router in api_routers:
        app.include_router(router, prefix="/api/v3")

    # Root-level API endpoints (discovery doc, meta, rate_limit)
    app.include_router(root_router)

    # OAuth routes stay at root (web-facing, not API paths)
    app.include_router(oauth_router)

    # -- GraphQL API ----------------------------------------------------------
    try:
        from strawberry.fastapi import GraphQLRouter
        from app.graphql.schema import schema

        from fastapi import Depends, Request as FastAPIRequest
        from sqlalchemy.ext.asyncio import AsyncSession

        async def get_graphql_context(
            request: FastAPIRequest,
            db: AsyncSession = Depends(get_db),
        ):
            """Provide db session and authenticated user to GraphQL resolvers."""
            from app.api.deps import get_current_user

            user = None
            try:
                user = await get_current_user(request, db)
            except Exception:
                pass
            return {"db": db, "user": user, "request": request}

        graphql_router = GraphQLRouter(
            schema,
            context_getter=get_graphql_context,
        )
        # Mount at both /graphql (legacy) and /api/graphql (GH Enterprise)
        app.include_router(graphql_router, prefix="/graphql")
        app.include_router(graphql_router, prefix="/api/graphql")
    except ImportError:
        logger.warning("Strawberry not installed; GraphQL endpoint disabled")

    # -- Admin frontend -------------------------------------------------------
    from app.admin.routes import router as admin_router
    from app.admin.routes import get_static_files_app

    app.include_router(admin_router)
    app.mount(
        "/admin/static",
        get_static_files_app(),
        name="admin-static",
    )

    # -- Git Smart HTTP protocol handler --------------------------------------
    from app.git.smart_http import router as git_router
    app.include_router(git_router, tags=["git"])

    # -- Web frontend (LAST -- catch-all /{owner}/{repo} patterns) -----------
    from app.web.routes import router as web_router

    _WEB_STATIC = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "web", "static"
    )
    app.mount("/ui/static", StaticFiles(directory=_WEB_STATIC), name="web-static")
    app.include_router(web_router)

    return app


# Application instance for uvicorn
app = create_app()
