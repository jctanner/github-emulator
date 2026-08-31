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


async def set_default_branch(disk_path: str, branch: str) -> bool:
    """Point the bare repository's HEAD at an existing branch."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path
    verify = await asyncio.create_subprocess_exec(
        "git",
        "show-ref",
        "--verify",
        f"refs/heads/{branch}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    await verify.communicate()
    if verify.returncode != 0:
        return False
    update = await asyncio.create_subprocess_exec(
        "git",
        "symbolic-ref",
        "HEAD",
        f"refs/heads/{branch}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    await update.communicate()
    return update.returncode == 0


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


async def get_tag_count(disk_path: str) -> int:
    """Return the number of tag refs without constructing tag API objects."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    proc = await asyncio.create_subprocess_exec(
        "git",
        "for-each-ref",
        "--format=%(refname)",
        "refs/tags/",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return 0
    return sum(1 for line in stdout.decode().splitlines() if line.strip())


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

    Diffs normal and merge commits against their first parent. Root commits are
    diffed against the empty tree.
    Returns [{"filename", "status", "patch"}].
    """
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    proc = await asyncio.create_subprocess_exec(
        "git", "rev-list", "--parents", "-n", "1", sha,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return []

    rev_parts = stdout.decode().strip().split()
    if not rev_parts:
        return []

    parents = rev_parts[1:]
    if parents:
        diff_args = ["diff", "-p", "--find-renames", parents[0], sha]
    else:
        diff_args = ["diff-tree", "-p", "--root", "--no-commit-id", "-r", sha]

    proc = await asyncio.create_subprocess_exec(
        "git", *diff_args,
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


async def resolve_ref(disk_path: str, ref: str) -> str | None:
    """Resolve a git ref to a commit SHA."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--verify", ref,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    return stdout.decode().strip() or None


async def normalize_branch_ref(
    disk_path: str, ref: str, owner_login: str | None = None
) -> tuple[str, str | None]:
    """Return a git-usable branch ref and resolved SHA.

    GitHub PR inputs and imported data may use label-style refs like
    ``owner:branch``. For same-repository PRs, the bare repo only has
    ``branch`` under refs/heads, so prefer the stripped branch when it exists.
    """
    if not ref:
        return ref, None

    candidates = [ref]
    if ":" in ref:
        maybe_owner, maybe_branch = ref.split(":", 1)
        if maybe_branch and (owner_login is None or maybe_owner == owner_login):
            candidates.insert(0, maybe_branch)

    for candidate in candidates:
        sha = await resolve_ref(disk_path, candidate)
        if sha:
            return candidate, sha

    return ref, None


async def get_compare_diff(disk_path: str, base_ref: str, head_ref: str) -> list[dict]:
    """Get files changed between two refs with their patches.

    Uses git diff base...head so pull requests compare from the merge base to
    the proposed head branch. Falls back to base..head for unrelated histories.
    """
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    async def run_diff(revision_range: str):
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "--find-renames", "--patch", revision_range,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        return await proc.communicate(), proc.returncode

    (stdout, _), returncode = await run_diff(f"{base_ref}...{head_ref}")
    if returncode != 0:
        (stdout, _), returncode = await run_diff(f"{base_ref}..{head_ref}")
    if returncode != 0:
        return []

    return _parse_git_diff(stdout.decode(errors="replace"))


async def get_compare_commit_count(
    disk_path: str, base_ref: str, head_ref: str
) -> int:
    """Count commits reachable from a pull-request head but not its base."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path
    proc = await asyncio.create_subprocess_exec(
        "git",
        "rev-list",
        "--count",
        f"{base_ref}..{head_ref}",
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


async def get_compare_stats(
    disk_path: str, base_ref: str, head_ref: str
) -> tuple[int, int, int]:
    """Return additions, deletions, and changed-file count without patches."""
    env = os.environ.copy()
    env["GIT_DIR"] = disk_path

    async def run_diff(revision_range: str):
        proc = await asyncio.create_subprocess_exec(
            "git",
            "diff",
            "--numstat",
            revision_range,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, _ = await proc.communicate()
        return stdout, proc.returncode

    stdout, returncode = await run_diff(f"{base_ref}...{head_ref}")
    if returncode != 0:
        stdout, returncode = await run_diff(f"{base_ref}..{head_ref}")
    if returncode != 0:
        return 0, 0, 0

    additions = 0
    deletions = 0
    changed_files = 0
    for line in stdout.decode(errors="replace").splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3:
            continue
        added, deleted, _ = fields
        additions += int(added) if added.isdigit() else 0
        deletions += int(deleted) if deleted.isdigit() else 0
        changed_files += 1
    return additions, deletions, changed_files


def _parse_git_diff(output: str) -> list[dict]:
    """Parse git patch output into changed-file records."""
    files = []
    current_file = None
    patch_lines = []

    for line in output.split("\n"):
        if line.startswith("diff --git "):
            if current_file is not None:
                current_file["patch"] = "\n".join(patch_lines)
                current_file["changes"] = (
                    current_file["additions"] + current_file["deletions"]
                )
                files.append(current_file)

            parts = line.split(" b/", 1)
            filename = parts[1] if len(parts) > 1 else ""
            current_file = {
                "filename": filename,
                "status": "modified",
                "additions": 0,
                "deletions": 0,
                "changes": 0,
                "patch": "",
            }
            patch_lines = [line]
            continue

        if current_file is None:
            continue

        if line.startswith("new file"):
            current_file["status"] = "added"
        elif line.startswith("deleted file"):
            current_file["status"] = "deleted"
        elif line.startswith("rename from "):
            current_file["status"] = "renamed"
            current_file["previous_filename"] = line.removeprefix("rename from ")
        elif line.startswith("rename to "):
            current_file["filename"] = line.removeprefix("rename to ")
        elif line.startswith("+++ b/"):
            current_file["filename"] = line.removeprefix("+++ b/")
        elif line.startswith("+") and not line.startswith("+++"):
            current_file["additions"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            current_file["deletions"] += 1

        patch_lines.append(line)

    if current_file is not None:
        current_file["patch"] = "\n".join(patch_lines)
        current_file["changes"] = current_file["additions"] + current_file["deletions"]
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
