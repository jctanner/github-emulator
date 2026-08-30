"""Search indexing service -- indexes files and commits for code/commit search."""

import logging
import os

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.git.bare_repo import list_tree, get_file_content, get_log
from app.models.search_index import FileContent, CommitMetadata
from app.models.repository import Repository

logger = logging.getLogger("github_emulator.index")

# Extension -> language mapping
LANGUAGE_MAP = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".jsx": "JavaScript", ".tsx": "TypeScript",
    ".java": "Java", ".c": "C", ".cpp": "C++", ".h": "C",
    ".cs": "C#", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
    ".php": "PHP", ".swift": "Swift", ".kt": "Kotlin",
    ".scala": "Scala", ".r": "R", ".R": "R",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".json": "JSON", ".xml": "XML", ".yaml": "YAML", ".yml": "YAML",
    ".md": "Markdown", ".rst": "reStructuredText",
    ".sql": "SQL", ".pl": "Perl", ".lua": "Lua",
    ".dockerfile": "Dockerfile", ".toml": "TOML",
    ".ini": "INI", ".cfg": "INI", ".conf": "INI",
    ".txt": "Text", ".csv": "CSV",
}

# Files likely to be binary
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a",
    ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".avi", ".mov", ".wav",
    ".pyc", ".pyo", ".class", ".jar",
}


def _detect_language(path: str) -> str | None:
    """Detect language from file extension."""
    name = os.path.basename(path).lower()
    if name == "dockerfile":
        return "Dockerfile"
    if name == "makefile":
        return "Makefile"
    _, ext = os.path.splitext(name)
    return LANGUAGE_MAP.get(ext)


def _is_binary(path: str) -> bool:
    """Check if a file is likely binary based on extension."""
    _, ext = os.path.splitext(path.lower())
    return ext in BINARY_EXTENSIONS


async def _walk_tree(disk_path: str, ref: str, base_path: str = "") -> list[dict]:
    """Recursively walk the git tree and collect file entries."""
    entries = await list_tree(disk_path, ref, base_path)
    if entries is None:
        return []

    files = []
    for entry in entries:
        full_path = f"{base_path}/{entry['name']}" if base_path else entry["name"]
        if entry["type"] == "blob":
            files.append({"path": full_path, "sha": entry["sha"]})
        elif entry["type"] == "tree":
            sub_files = await _walk_tree(disk_path, ref, full_path)
            files.extend(sub_files)
    return files


async def index_repository(db: AsyncSession, repo: Repository) -> int:
    """Index a repository's files and commits for search.

    Returns the number of items indexed.
    """
    disk_path = repo.disk_path
    if not disk_path or not os.path.isdir(disk_path):
        return 0

    ref = repo.default_branch or "main"
    count = 0

    # Clear existing index for this repo
    await db.execute(delete(FileContent).where(FileContent.repo_id == repo.id))
    await db.execute(delete(CommitMetadata).where(CommitMetadata.repo_id == repo.id))

    # Index files
    try:
        files = await _walk_tree(disk_path, ref)
        for file_entry in files:
            path = file_entry["path"]
            sha = file_entry["sha"]

            content_text = None
            size = 0
            if not _is_binary(path):
                raw = await get_file_content(disk_path, ref, path)
                if raw is not None:
                    size = len(raw)
                    try:
                        content_text = raw.decode("utf-8", errors="replace")
                        # Skip very large files
                        if len(content_text) > 1_000_000:
                            content_text = content_text[:1_000_000]
                    except Exception:
                        content_text = None

            language = _detect_language(path)

            fc = FileContent(
                repo_id=repo.id,
                file_path=path,
                blob_sha=sha,
                content=content_text,
                language=language,
                size=size,
                ref=ref,
            )
            db.add(fc)
            count += 1
    except Exception:
        logger.exception("Error indexing files for repo %s", repo.full_name)

    # Index commits
    try:
        commits = await get_log(disk_path, ref, max_count=500)
        for commit in commits:
            cm = CommitMetadata(
                repo_id=repo.id,
                commit_sha=commit["sha"],
                author_name=commit.get("author_name"),
                author_email=commit.get("author_email"),
                committer_name=commit.get("committer_name"),
                committer_email=commit.get("committer_email"),
                message=commit.get("message", ""),
                author_date=commit.get("author_date"),
                committer_date=commit.get("committer_date"),
            )
            db.add(cm)
            count += 1
    except Exception:
        logger.exception("Error indexing commits for repo %s", repo.full_name)

    await db.commit()
    logger.info("Indexed %d items for repo %s", count, repo.full_name)
    return count
