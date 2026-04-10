"""GitHub repository import service.

Supports importing a single repository by URL, or bulk-importing all
repositories from a GitHub user or organization.
"""

import asyncio
import os
import re
import shutil
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.git.bare_repo import get_branches as get_disk_branches, get_default_branch, get_repo_size_kb
from app.models.branch import Branch
from app.models.import_job import ImportJob
from app.models.repository import Repository
from app.models.user import User


def parse_github_url(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub URL.

    Accepts https://github.com/owner/repo[.git] forms.
    Raises ValueError if the URL doesn't match.
    """
    pattern = r"(?:https?://)?github\.com/([^/]+)/([^/.]+?)(?:\.git)?/?$"
    m = re.match(pattern, url.strip())
    if not m:
        raise ValueError(f"Invalid GitHub URL: {url}")
    return m.group(1), m.group(2)


async def start_single_import(db, source_url: str, owner_id: int, github_token: str | None = None) -> ImportJob:
    """Create a single-repo import job and launch it in the background."""
    _, repo_name = parse_github_url(source_url)

    job = ImportJob(
        job_type="single",
        status="pending",
        source_url=source_url.strip(),
        repo_name=repo_name,
        owner_id=owner_id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    asyncio.create_task(_do_single_import(job.id, source_url.strip(), owner_id, github_token))
    return job


async def start_bulk_import(
    db, github_name: str, owner_id: int, github_token: str | None = None, source_type: str = "user"
) -> ImportJob:
    """Create a bulk import parent job and launch discovery in the background."""
    job = ImportJob(
        job_type="bulk",
        status="running",
        source_url=f"https://github.com/{github_name}",
        owner_id=owner_id,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    asyncio.create_task(_do_bulk_import(job.id, github_name, owner_id, github_token, source_type))
    return job


def _get_next_link(link_header: str | None) -> str | None:
    """Parse GitHub Link header for the next page URL."""
    if not link_header:
        return None
    for part in link_header.split(","):
        part = part.strip()
        if 'rel="next"' in part:
            m = re.search(r"<([^>]+)>", part)
            if m:
                return m.group(1)
    return None


async def _do_bulk_import(
    parent_job_id: int,
    github_name: str,
    owner_id: int,
    github_token: str | None,
    source_type: str,
) -> None:
    """Discover repos from a GitHub user/org and spawn child import jobs."""
    async with async_session() as db:
        try:
            if source_type == "org":
                api_url = f"https://api.github.com/orgs/{github_name}/repos?per_page=100&type=all"
            else:
                api_url = f"https://api.github.com/users/{github_name}/repos?per_page=100&type=all"

            headers = {"Accept": "application/vnd.github+json"}
            if github_token:
                headers["Authorization"] = f"Bearer {github_token}"

            all_repos = []
            url = api_url

            async with httpx.AsyncClient(timeout=30.0) as client:
                while url:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code in (403, 429):
                        msg = "GitHub API rate limit exceeded. Try providing a GitHub token."
                        result = await db.execute(
                            select(ImportJob).where(ImportJob.id == parent_job_id)
                        )
                        parent = result.scalar_one()
                        parent.status = "failed"
                        parent.error_message = msg
                        parent.completed_at = datetime.now(timezone.utc)
                        await db.commit()
                        return
                    if resp.status_code != 200:
                        result = await db.execute(
                            select(ImportJob).where(ImportJob.id == parent_job_id)
                        )
                        parent = result.scalar_one()
                        parent.status = "failed"
                        parent.error_message = f"GitHub API returned {resp.status_code}: {resp.text[:500]}"
                        parent.completed_at = datetime.now(timezone.utc)
                        await db.commit()
                        return

                    repos_page = resp.json()
                    all_repos.extend(repos_page)
                    url = _get_next_link(resp.headers.get("Link"))

            # Update parent with repo count
            result = await db.execute(
                select(ImportJob).where(ImportJob.id == parent_job_id)
            )
            parent = result.scalar_one()
            parent.repo_count = len(all_repos)

            if len(all_repos) == 0:
                parent.status = "completed"
                parent.completed_at = datetime.now(timezone.utc)
                await db.commit()
                return

            await db.commit()

            # Spawn child jobs
            for repo_data in all_repos:
                clone_url = repo_data.get("clone_url", "")
                repo_name = repo_data.get("name", "")
                if not clone_url:
                    continue

                child = ImportJob(
                    job_type="single",
                    status="pending",
                    source_url=clone_url,
                    repo_name=repo_name,
                    owner_id=owner_id,
                    parent_job_id=parent_job_id,
                )
                db.add(child)
                await db.commit()
                await db.refresh(child)

                asyncio.create_task(
                    _do_single_import(child.id, clone_url, owner_id, github_token)
                )

        except Exception as exc:
            result = await db.execute(
                select(ImportJob).where(ImportJob.id == parent_job_id)
            )
            parent = result.scalar_one_or_none()
            if parent:
                parent.status = "failed"
                parent.error_message = str(exc)[:1000]
                parent.completed_at = datetime.now(timezone.utc)
                await db.commit()


async def _do_single_import(
    job_id: int,
    source_url: str,
    owner_id: int,
    github_token: str | None,
) -> None:
    """Clone a single GitHub repo and create the local Repository record."""
    async with async_session() as db:
        disk_path = None
        try:
            # Load the job
            result = await db.execute(
                select(ImportJob).where(ImportJob.id == job_id)
            )
            job = result.scalar_one()
            job.status = "running"
            await db.commit()

            # Load the target user
            result = await db.execute(
                select(User).where(User.id == owner_id)
            )
            owner = result.scalar_one()

            # Extract repo name
            _, repo_name = parse_github_url(source_url)

            # Check for duplicate
            full_name = f"{owner.login}/{repo_name}"
            existing = await db.execute(
                select(Repository).where(Repository.full_name == full_name)
            )
            if existing.scalar_one_or_none() is not None:
                result = await db.execute(
                    select(ImportJob).where(ImportJob.id == job_id)
                )
                job = result.scalar_one()
                job.status = "failed"
                job.error_message = f"Repository '{full_name}' already exists."
                job.completed_at = datetime.now(timezone.utc)
                await db.commit()
                await _maybe_complete_parent(db, job.parent_job_id)
                return

            # Build disk path
            disk_path = os.path.join(settings.DATA_DIR, "repos", owner.login, f"{repo_name}.git")
            os.makedirs(os.path.dirname(disk_path), exist_ok=True)

            # Build clone URL with token if provided
            clone_url = source_url.strip()
            if github_token and clone_url.startswith("https://"):
                clone_url = clone_url.replace("https://", f"https://x-access-token:{github_token}@")

            # Clone bare
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--bare", clone_url, disk_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                shutil.rmtree(disk_path, ignore_errors=True)
                disk_path = None
                result = await db.execute(
                    select(ImportJob).where(ImportJob.id == job_id)
                )
                job = result.scalar_one()
                job.status = "failed"
                job.error_message = stderr.decode(errors="replace")[:1000]
                job.completed_at = datetime.now(timezone.utc)
                await db.commit()
                await _maybe_complete_parent(db, job.parent_job_id)
                return

            # Detect default branch
            default_branch = await get_default_branch(disk_path)

            # Create Repository record
            repo = Repository(
                owner_id=owner.id,
                owner_type="User",
                name=repo_name,
                full_name=full_name,
                disk_path=disk_path,
                default_branch=default_branch,
                visibility="public",
            )
            db.add(repo)
            await db.commit()
            await db.refresh(repo)

            # Sync branches
            await _sync_branches_to_db(db, repo)

            # Update size and pushed_at
            size_kb = await get_repo_size_kb(disk_path)
            result = await db.execute(
                select(Repository).where(Repository.id == repo.id)
            )
            repo = result.scalar_one()
            repo.size = size_kb
            repo.pushed_at = datetime.now(timezone.utc)
            await db.commit()

            # Mark job completed
            result = await db.execute(
                select(ImportJob).where(ImportJob.id == job_id)
            )
            job = result.scalar_one()
            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            await db.commit()

            await _maybe_complete_parent(db, job.parent_job_id)

        except Exception as exc:
            if disk_path and os.path.isdir(disk_path):
                shutil.rmtree(disk_path, ignore_errors=True)
            try:
                result = await db.execute(
                    select(ImportJob).where(ImportJob.id == job_id)
                )
                job = result.scalar_one_or_none()
                if job:
                    job.status = "failed"
                    job.error_message = str(exc)[:1000]
                    job.completed_at = datetime.now(timezone.utc)
                    await db.commit()
                    await _maybe_complete_parent(db, job.parent_job_id)
            except Exception:
                pass


async def _maybe_complete_parent(db, parent_job_id: int | None) -> None:
    """Increment the parent's completed_count; mark completed if all children done."""
    if parent_job_id is None:
        return

    result = await db.execute(
        select(ImportJob).where(ImportJob.id == parent_job_id)
    )
    parent = result.scalar_one_or_none()
    if not parent:
        return

    parent.completed_count = (parent.completed_count or 0) + 1

    if parent.repo_count is not None and parent.completed_count >= parent.repo_count:
        parent.status = "completed"
        parent.completed_at = datetime.now(timezone.utc)

    await db.commit()


async def _sync_branches_to_db(db, repository: Repository) -> None:
    """Sync branch refs from disk into the branches table."""
    disk_branches = await get_disk_branches(repository.disk_path)
    disk_map = {b["name"]: b["sha"] for b in disk_branches}

    result = await db.execute(
        select(Branch).where(Branch.repo_id == repository.id)
    )
    existing = {b.name: b for b in result.scalars().all()}

    for name, sha in disk_map.items():
        if name in existing:
            if existing[name].sha != sha:
                existing[name].sha = sha
        else:
            db.add(Branch(repo_id=repository.id, name=name, sha=sha))

    for name, branch in existing.items():
        if name not in disk_map:
            await db.delete(branch)

    await db.commit()
