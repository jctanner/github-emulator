"""Bare git repository management.

Functions for creating, inspecting, and managing bare git repositories.
"""

import asyncio
import os
import shutil
import tempfile
from typing import Optional


async def init_bare_repo(disk_path: str, default_branch: str = "main") -> None:
    """Initialize a new bare git repository."""
    os.makedirs(disk_path, exist_ok=True)

    proc = await asyncio.create_subprocess_exec(
        "git", "init", "--bare", disk_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    # Set default branch
    proc = await asyncio.create_subprocess_exec(
        "git", "symbolic-ref", "HEAD", f"refs/heads/{default_branch}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=disk_path,
    )
    await proc.communicate()

    # Enable receive.denyCurrentBranch for bare repos (not strictly necessary
    # but ensures compatibility)
    proc = await asyncio.create_subprocess_exec(
        "git", "config", "--local", "receive.denyCurrentBranch", "ignore",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=disk_path,
    )
    await proc.communicate()

    # Enable HTTP backend info update
    proc = await asyncio.create_subprocess_exec(
        "git", "config", "--local", "http.receivepack", "true",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=disk_path,
    )
    await proc.communicate()


async def create_initial_commit(
    disk_path: str,
    default_branch: str,
    repo_name: str,
    owner_name: str,
    owner_email: str,
) -> Optional[str]:
    """Create an initial commit with a README.md in a bare repo.

    Returns the commit SHA, or None on failure.
    """
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path
    env["GIT_AUTHOR_NAME"] = owner_name or "GitHub Emulator"
    env["GIT_AUTHOR_EMAIL"] = owner_email or "noreply@github-emulator.local"
    env["GIT_COMMITTER_NAME"] = env["GIT_AUTHOR_NAME"]
    env["GIT_COMMITTER_EMAIL"] = env["GIT_AUTHOR_EMAIL"]

    readme_content = f"# {repo_name}\n"

    # Create a blob for README.md
    proc = await asyncio.create_subprocess_exec(
        "git", "hash-object", "-w", "--stdin",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate(input=readme_content.encode())
    blob_sha = stdout.decode().strip()
    if not blob_sha:
        return None

    # Create a tree with README.md
    tree_entry = f"100644 blob {blob_sha}\tREADME.md\n"
    proc = await asyncio.create_subprocess_exec(
        "git", "mktree",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate(input=tree_entry.encode())
    tree_sha = stdout.decode().strip()
    if not tree_sha:
        return None

    # Create the initial commit
    proc = await asyncio.create_subprocess_exec(
        "git", "commit-tree", tree_sha, "-m", "Initial commit",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    commit_sha = stdout.decode().strip()
    if not commit_sha:
        return None

    # Update the default branch ref
    proc = await asyncio.create_subprocess_exec(
        "git", "update-ref", f"refs/heads/{default_branch}", commit_sha,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    await proc.communicate()

    # Update server info for dumb HTTP clients
    proc = await asyncio.create_subprocess_exec(
        "git", "update-server-info",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    await proc.communicate()

    return commit_sha


async def write_file(
    disk_path: str,
    branch: str,
    path: str,
    content: bytes,
    message: str,
    author_name: str,
    author_email: str,
) -> str:
    """Write/update a file in a bare repo. Returns the new commit SHA."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path
    env["GIT_AUTHOR_NAME"] = author_name or "GitHub Emulator"
    env["GIT_AUTHOR_EMAIL"] = author_email or "noreply@github-emulator.local"
    env["GIT_COMMITTER_NAME"] = env["GIT_AUTHOR_NAME"]
    env["GIT_COMMITTER_EMAIL"] = env["GIT_AUTHOR_EMAIL"]

    # 1. Create blob from content
    proc = await asyncio.create_subprocess_exec(
        "git", "hash-object", "-w", "--stdin",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate(input=content)
    blob_sha = stdout.decode().strip()
    if not blob_sha:
        raise RuntimeError("Failed to create blob")

    # 2. Create a temp index file
    fd, tmp_index = tempfile.mkstemp(prefix="git_index_")
    os.close(fd)
    try:
        idx_env = env.copy()
        idx_env["GIT_INDEX_FILE"] = tmp_index

        # 3. Read current tree into temp index
        proc = await asyncio.create_subprocess_exec(
            "git", "read-tree", branch,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=idx_env,
        )
        await proc.communicate()

        # 4. Add/update the file entry
        proc = await asyncio.create_subprocess_exec(
            "git", "update-index", "--add",
            "--cacheinfo", f"100644,{blob_sha},{path}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=idx_env,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"update-index failed: {stderr.decode()}")

        # 5. Write tree
        proc = await asyncio.create_subprocess_exec(
            "git", "write-tree",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=idx_env,
        )
        stdout, _ = await proc.communicate()
        tree_sha = stdout.decode().strip()
        if not tree_sha:
            raise RuntimeError("Failed to write tree")
    finally:
        if os.path.exists(tmp_index):
            os.unlink(tmp_index)

    # 6. Get parent commit SHA
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", branch,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    parent_sha = stdout.decode().strip()

    # 7. Create commit
    proc = await asyncio.create_subprocess_exec(
        "git", "commit-tree", tree_sha, "-p", parent_sha, "-m", message,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    commit_sha = stdout.decode().strip()
    if not commit_sha:
        raise RuntimeError("Failed to create commit")

    # 8. Advance the branch ref
    proc = await asyncio.create_subprocess_exec(
        "git", "update-ref", f"refs/heads/{branch}", commit_sha,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    await proc.communicate()

    return commit_sha


async def delete_bare_repo(disk_path: str) -> None:
    """Delete a bare git repository from disk."""
    if os.path.isdir(disk_path):
        shutil.rmtree(disk_path)


async def get_repo_size_kb(disk_path: str) -> int:
    """Get the size of a bare repo in kilobytes."""
    total = 0
    if not os.path.isdir(disk_path):
        return 0
    for dirpath, dirnames, filenames in os.walk(disk_path):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
    return total // 1024


async def get_branches(disk_path: str) -> list[dict]:
    """List branches in a bare repo with their SHAs."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    proc = await asyncio.create_subprocess_exec(
        "git", "for-each-ref", "--format=%(refname:short) %(objectname)",
        "refs/heads/",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []

    branches = []
    for line in stdout.decode().strip().split("\n"):
        if not line.strip():
            continue
        parts = line.strip().split(" ", 1)
        if len(parts) == 2:
            branches.append({"name": parts[0], "sha": parts[1]})
    return branches


async def get_default_branch(disk_path: str) -> str:
    """Get the default branch name from HEAD."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    proc = await asyncio.create_subprocess_exec(
        "git", "symbolic-ref", "--short", "HEAD",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode().strip() or "main"


async def get_commit_info(disk_path: str, sha: str) -> Optional[dict]:
    """Get commit information for a given SHA."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    proc = await asyncio.create_subprocess_exec(
        "git", "cat-file", "-p", sha,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return None

    lines = stdout.decode().split("\n")
    info = {
        "sha": sha,
        "tree": "",
        "parents": [],
        "author": {},
        "committer": {},
        "message": "",
    }

    in_message = False
    message_lines = []

    for line in lines:
        if in_message:
            message_lines.append(line)
        elif line == "":
            in_message = True
        elif line.startswith("tree "):
            info["tree"] = line[5:]
        elif line.startswith("parent "):
            info["parents"].append(line[7:])
        elif line.startswith("author "):
            info["author"] = _parse_signature(line[7:])
        elif line.startswith("committer "):
            info["committer"] = _parse_signature(line[10:])

    info["message"] = "\n".join(message_lines).strip()
    return info


def _parse_signature(sig: str) -> dict:
    """Parse a git author/committer line."""
    # Format: "Name <email> timestamp timezone"
    parts = sig.rsplit(" ", 2)
    if len(parts) >= 3:
        name_email = parts[0]
        timestamp = parts[1]
        tz = parts[2]
        # Parse name and email
        if "<" in name_email:
            name = name_email.split("<")[0].strip()
            email = name_email.split("<")[1].rstrip(">").strip()
        else:
            name = name_email
            email = ""
        return {
            "name": name,
            "email": email,
            "date": timestamp,
        }
    return {"name": sig, "email": "", "date": ""}


async def get_file_content(
    disk_path: str, ref: str, path: str
) -> Optional[bytes]:
    """Get file content from a bare repo at a given ref and path."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    proc = await asyncio.create_subprocess_exec(
        "git", "show", f"{ref}:{path}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return None
    return stdout


async def list_tree(
    disk_path: str, ref: str, path: str = ""
) -> Optional[list[dict]]:
    """List directory contents at a given ref and path."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    tree_ref = f"{ref}:{path}" if path else ref
    proc = await asyncio.create_subprocess_exec(
        "git", "ls-tree", tree_ref,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        return None

    entries = []
    for line in stdout.decode().strip().split("\n"):
        if not line.strip():
            continue
        # Format: mode type sha\tname
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        meta, name = parts
        meta_parts = meta.split()
        if len(meta_parts) != 3:
            continue
        entries.append({
            "mode": meta_parts[0],
            "type": meta_parts[1],
            "sha": meta_parts[2],
            "name": name,
        })
    return entries


async def get_tags(disk_path: str) -> list[dict]:
    """List tags with name, sha, and tagger date (via git for-each-ref)."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    proc = await asyncio.create_subprocess_exec(
        "git", "for-each-ref",
        "--format=%(refname:short) %(objectname) %(creatordate:iso8601)",
        "refs/tags/",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []

    tags = []
    for line in stdout.decode().strip().split("\n"):
        if not line.strip():
            continue
        parts = line.strip().split(" ", 2)
        if len(parts) >= 2:
            tags.append({
                "name": parts[0],
                "sha": parts[1],
                "date": parts[2] if len(parts) > 2 else "",
            })
    return tags


async def get_commit_count(disk_path: str, ref: str) -> int:
    """Return total commit count on a branch (git rev-list --count)."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    proc = await asyncio.create_subprocess_exec(
        "git", "rev-list", "--count", ref,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return 0
    try:
        return int(stdout.decode().strip())
    except ValueError:
        return 0


async def get_commit_diff(disk_path: str, sha: str) -> list[dict]:
    """Get files changed in a commit with their patches.

    Uses git diff-tree -p --no-commit-id -r {sha}.
    Returns [{"filename", "status", "patch"}].
    """
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    proc = await asyncio.create_subprocess_exec(
        "git", "diff-tree", "-p", "--no-commit-id", "-r", sha,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []

    output = stdout.decode(errors="replace")
    files = []
    current_file = None
    patch_lines = []

    for line in output.split("\n"):
        if line.startswith("diff --git "):
            # Save previous file
            if current_file is not None:
                current_file["patch"] = "\n".join(patch_lines)
                files.append(current_file)
            # Parse filename from "diff --git a/path b/path"
            parts = line.split(" b/", 1)
            filename = parts[1] if len(parts) > 1 else ""
            current_file = {"filename": filename, "status": "modified", "patch": ""}
            patch_lines = [line]
        elif current_file is not None:
            if line.startswith("new file"):
                current_file["status"] = "added"
            elif line.startswith("deleted file"):
                current_file["status"] = "deleted"
            patch_lines.append(line)

    if current_file is not None:
        current_file["patch"] = "\n".join(patch_lines)
        files.append(current_file)

    return files


async def get_log(
    disk_path: str,
    ref: str = "HEAD",
    max_count: int = 30,
    skip: int = 0,
    path: str | None = None,
) -> list[dict]:
    """Get git log entries."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    args = [
        "git", "log",
        f"--max-count={max_count}",
        f"--skip={skip}",
        "--format=%H%n%T%n%P%n%an%n%ae%n%aI%n%cn%n%ce%n%cI%n%s%n%b%n---END---",
        ref,
    ]
    if path:
        args.extend(["--", path])

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []

    commits = []
    entries = stdout.decode().split("---END---\n")
    for entry in entries:
        lines = entry.strip().split("\n")
        if len(lines) < 10:
            continue
        commit = {
            "sha": lines[0],
            "tree_sha": lines[1],
            "parent_shas": lines[2].split() if lines[2] else [],
            "author_name": lines[3],
            "author_email": lines[4],
            "author_date": lines[5],
            "committer_name": lines[6],
            "committer_email": lines[7],
            "committer_date": lines[8],
            "message": lines[9],
            "body": "\n".join(lines[10:]).strip() if len(lines) > 10 else "",
        }
        commits.append(commit)
    return commits
