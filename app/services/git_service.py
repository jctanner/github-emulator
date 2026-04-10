"""Git operations service for managing bare repositories."""

import asyncio
import os
import shutil
from typing import Optional


async def init_bare_repo(disk_path: str, default_branch: str = "main") -> None:
    """Initialize a bare git repository.

    Args:
        disk_path: Filesystem path for the bare repo.
        default_branch: Name of the default branch.
    """
    os.makedirs(disk_path, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "git", "init", "--bare", disk_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    # Set the default branch in HEAD
    head_path = os.path.join(disk_path, "HEAD")
    with open(head_path, "w") as f:
        f.write(f"ref: refs/heads/{default_branch}\n")


async def create_initial_commit(
    disk_path: str,
    default_branch: str,
    owner_name: str,
    owner_email: str,
) -> None:
    """Create an initial commit with a README.md in a bare repository.

    This uses a temporary working directory, creates the commit, and
    pushes it into the bare repo.

    Args:
        disk_path: Filesystem path of the bare repo.
        default_branch: The branch to create the commit on.
        owner_name: Name for the commit author.
        owner_email: Email for the commit author.
    """
    # Derive the repo name from the disk_path for the README content
    repo_name = os.path.basename(disk_path).replace(".git", "")
    tmp_dir = disk_path + ".tmp_init"

    try:
        # Clone the bare repo into a temporary directory
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", disk_path, tmp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        # Create README.md
        readme_path = os.path.join(tmp_dir, "README.md")
        with open(readme_path, "w") as f:
            f.write(f"# {repo_name}\n")

        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = owner_name
        env["GIT_AUTHOR_EMAIL"] = owner_email
        env["GIT_COMMITTER_NAME"] = owner_name
        env["GIT_COMMITTER_EMAIL"] = owner_email

        # Configure git in the temp directory
        await _run_git(tmp_dir, ["checkout", "-b", default_branch], env=env)
        await _run_git(tmp_dir, ["add", "README.md"], env=env)
        await _run_git(
            tmp_dir,
            ["commit", "-m", "Initial commit"],
            env=env,
        )
        await _run_git(
            tmp_dir,
            ["push", "origin", default_branch],
            env=env,
        )
    finally:
        # Clean up the temporary directory
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)


async def get_repo_size(disk_path: str) -> int:
    """Get the size of a bare repository in kilobytes.

    Args:
        disk_path: Filesystem path of the bare repo.

    Returns:
        Size in KB.
    """
    total = 0
    for dirpath, _dirnames, filenames in os.walk(disk_path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            try:
                total += os.path.getsize(filepath)
            except OSError:
                pass
    return total // 1024


async def get_default_branch(disk_path: str) -> str:
    """Read the default branch from the HEAD ref.

    Args:
        disk_path: Filesystem path of the bare repo.

    Returns:
        Branch name (e.g. "main").
    """
    head_path = os.path.join(disk_path, "HEAD")
    try:
        with open(head_path, "r") as f:
            content = f.read().strip()
        # Format: "ref: refs/heads/main"
        if content.startswith("ref: refs/heads/"):
            return content[len("ref: refs/heads/"):]
        return content
    except FileNotFoundError:
        return "main"


async def get_branches(disk_path: str) -> list[dict]:
    """List all branches in a bare repository with their SHAs.

    Args:
        disk_path: Filesystem path of the bare repo.

    Returns:
        List of dicts with "name" and "sha" keys.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "--git-dir", disk_path,
        "for-each-ref", "--format=%(refname:short) %(objectname)",
        "refs/heads/",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()

    branches = []
    for line in stdout.decode().strip().splitlines():
        if not line:
            continue
        parts = line.split(" ", 1)
        if len(parts) == 2:
            branches.append({"name": parts[0], "sha": parts[1]})
    return branches


async def get_commit_info(disk_path: str, sha: str) -> dict:
    """Get detailed information about a commit.

    Args:
        disk_path: Filesystem path of the bare repo.
        sha: The commit SHA to look up.

    Returns:
        Dict with commit details: sha, message, author_name, author_email,
        author_date, committer_name, committer_email, committer_date, tree_sha.
    """
    fmt = "%H%n%s%n%b%n%an%n%ae%n%aI%n%cn%n%ce%n%cI%n%T"
    proc = await asyncio.create_subprocess_exec(
        "git", "--git-dir", disk_path,
        "log", "-1", f"--format={fmt}", sha,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    lines = stdout.decode().strip().splitlines()

    if len(lines) < 10:
        return {}

    return {
        "sha": lines[0],
        "message": lines[1],
        "body": lines[2] if lines[2] else None,
        "author_name": lines[3],
        "author_email": lines[4],
        "author_date": lines[5],
        "committer_name": lines[6],
        "committer_email": lines[7],
        "committer_date": lines[8],
        "tree_sha": lines[9],
    }


async def get_ref_sha(disk_path: str, ref: str) -> Optional[str]:
    """Resolve a ref (branch name) to its SHA.

    Args:
        disk_path: Filesystem path of the bare repo.
        ref: The ref to resolve (e.g. branch name).

    Returns:
        The SHA string, or None if the ref doesn't exist.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "--git-dir", disk_path,
        "rev-parse", f"refs/heads/{ref}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return None
    return stdout.decode().strip()


async def delete_bare_repo(disk_path: str) -> None:
    """Delete a bare repository from disk.

    Args:
        disk_path: Filesystem path of the bare repo.
    """
    if os.path.exists(disk_path):
        shutil.rmtree(disk_path)


async def _run_git(
    cwd: str, args: list[str], env: Optional[dict] = None
) -> tuple[str, str]:
    """Run a git command in a working directory.

    Args:
        cwd: Working directory.
        args: Git command arguments.
        env: Optional environment variables.

    Returns:
        Tuple of (stdout, stderr) as strings.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    return stdout.decode(), stderr.decode()
