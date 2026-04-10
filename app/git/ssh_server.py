"""Git SSH transport server using asyncssh.

Provides git clone/push/pull over SSH with public key authentication
backed by the SSHKey database table.
"""

import asyncio
import logging
import os
from typing import Optional

try:
    import asyncssh
    HAS_ASYNCSSH = True
except ImportError:
    HAS_ASYNCSSH = False

from app.config import settings

logger = logging.getLogger("github_emulator.ssh")


def _get_host_key_path() -> str:
    """Get the path for the SSH host key, generating one if needed."""
    if settings.SSH_HOST_KEY_PATH:
        return settings.SSH_HOST_KEY_PATH
    return os.path.join(settings.DATA_DIR, "ssh_host_key")


async def _ensure_host_key() -> str:
    """Ensure the SSH host key exists, generating one on first run."""
    key_path = _get_host_key_path()
    if not os.path.exists(key_path):
        logger.info("Generating SSH host key at %s", key_path)
        os.makedirs(os.path.dirname(key_path), exist_ok=True)
        key = asyncssh.generate_private_key("ssh-rsa", key_size=2048)
        key.write_private_key(key_path)
        key.write_public_key(key_path + ".pub")
    return key_path


async def _lookup_user_by_key(public_key) -> Optional[tuple]:
    """Look up a user by their SSH public key.

    Returns (user_id, login) or None.
    """
    from app.database import async_session
    from app.models.ssh_key import SSHKey
    from app.models.user import User
    from sqlalchemy import select

    # Get the key data for comparison
    try:
        incoming_key_str = public_key.export_public_key("openssh").decode().strip()
    except Exception:
        return None

    # Extract just the key type + data (without comment)
    incoming_parts = incoming_key_str.split()
    if len(incoming_parts) < 2:
        return None
    incoming_key_data = f"{incoming_parts[0]} {incoming_parts[1]}"

    async with async_session() as db:
        result = await db.execute(select(SSHKey))
        keys = result.scalars().all()
        for ssh_key in keys:
            stored_parts = ssh_key.key.strip().split()
            if len(stored_parts) >= 2:
                stored_key_data = f"{stored_parts[0]} {stored_parts[1]}"
                if stored_key_data == incoming_key_data:
                    # Found a match, look up the user
                    user_result = await db.execute(
                        select(User).where(User.id == ssh_key.user_id)
                    )
                    user = user_result.scalar_one_or_none()
                    if user:
                        return (user.id, user.login)
    return None


async def _resolve_repo(repo_path: str) -> Optional[tuple]:
    """Resolve a git repo path to (repository, disk_path).

    Accepts paths like:
    - /owner/repo.git
    - /owner/repo
    - owner/repo.git
    - owner/repo
    """
    from app.database import async_session
    from app.models.repository import Repository
    from sqlalchemy import select

    # Normalize path
    path = repo_path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]

    parts = path.split("/")
    if len(parts) != 2:
        return None

    full_name = f"{parts[0]}/{parts[1]}"

    async with async_session() as db:
        result = await db.execute(
            select(Repository).where(Repository.full_name == full_name)
        )
        repo = result.scalar_one_or_none()
        if repo is None:
            return None
        return (repo, repo.disk_path)


if HAS_ASYNCSSH:

    class GitSSHServer(asyncssh.SSHServer):
        """SSH server that authenticates via public key lookup."""

        def __init__(self):
            self._user_info = None

        def connection_made(self, conn):
            self._conn = conn
            # Store ourselves on the connection so the process handler
            # can retrieve user_info after auth.
            conn.set_extra_info(git_user_info=None)

        def begin_auth(self, username):
            # Always require public key auth
            return True

        def public_key_auth_supported(self):
            return True

        async def validate_public_key(self, username, key):
            user_info = await _lookup_user_by_key(key)
            if user_info is not None:
                self._user_info = user_info
                self._conn.set_extra_info(git_user_info=user_info)
                return True
            return False

    async def _handle_git_process(process: asyncssh.SSHServerProcess) -> None:
        """Handle a git SSH command (git-upload-pack or git-receive-pack).

        The process channels are in binary mode (encoding=None on the server),
        so all reads/writes use bytes.
        """
        try:
            await _run_git_process(process)
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                process.stderr.write(f"Internal error: {e}\n".encode())
                process.exit(1)
            except Exception:
                pass

    async def _run_git_process(process: asyncssh.SSHServerProcess) -> None:
        command = process.command
        if not command:
            process.stderr.write(b"Interactive sessions are not supported.\n")
            process.exit(1)
            return

        # Parse git command
        parts = command.split()
        if len(parts) < 2:
            process.stderr.write(f"Unknown command: {command}\n".encode())
            process.exit(1)
            return

        git_cmd = parts[0]
        repo_path = parts[1].strip("'\"")

        if git_cmd not in ("git-upload-pack", "git-receive-pack"):
            process.stderr.write(f"Unknown command: {git_cmd}\n".encode())
            process.exit(1)
            return

        # Resolve repo
        repo_info = await _resolve_repo(repo_path)
        if repo_info is None:
            process.stderr.write(
                f"Repository not found: {repo_path}\n".encode()
            )
            process.exit(1)
            return

        repo, disk_path = repo_info

        if not disk_path or not os.path.isdir(disk_path):
            process.stderr.write(
                f"Repository not found on disk: {repo_path}\n".encode()
            )
            process.exit(1)
            return

        # Check access for push — get user info from the connection
        user_info = process.get_extra_info("git_user_info")

        if git_cmd == "git-receive-pack":
            if user_info is None:
                process.stderr.write(b"Authentication required for push.\n")
                process.exit(1)
                return

        # Run the git command
        env = os.environ.copy()
        env["GIT_DIR"] = disk_path

        proc = await asyncio.create_subprocess_exec(
            git_cmd, disk_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # Pipe SSH stdin -> git subprocess stdin (bytes throughout).
        # Use read() instead of async-for (which calls readline()) because
        # git protocol uses pkt-line framing — the flush packet "0000" has
        # no trailing newline, so readline() would block forever waiting
        # for \n that never arrives.
        async def pipe_stdin():
            try:
                while True:
                    data = await process.stdin.read(65536)
                    if not data:
                        break
                    proc.stdin.write(data)
                    await proc.stdin.drain()
            except Exception:
                pass
            finally:
                try:
                    proc.stdin.close()
                except Exception:
                    pass

        # Pipe git subprocess stdout -> SSH stdout (bytes throughout)
        async def pipe_stdout():
            try:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    process.stdout.write(chunk)
            except Exception:
                pass

        # Pipe git subprocess stderr -> SSH stderr (bytes throughout)
        async def pipe_stderr():
            try:
                while True:
                    chunk = await proc.stderr.read(65536)
                    if not chunk:
                        break
                    process.stderr.write(chunk)
            except Exception:
                pass

        # Stdin pipe runs as a separate task — the SSH client may not
        # close its stdin until it gets the response, so we cancel it
        # once the subprocess exits.
        stdin_task = asyncio.create_task(pipe_stdin())

        # Wait for subprocess stdout/stderr to reach EOF (process exited)
        await asyncio.gather(pipe_stdout(), pipe_stderr())
        exit_code = await proc.wait()

        stdin_task.cancel()
        try:
            await stdin_task
        except (asyncio.CancelledError, Exception):
            pass

        # Signal EOF on the SSH channel and send exit status.
        # Call exit() before post-push work so the client isn't blocked.
        try:
            process.stdout.write_eof()
        except (OSError, Exception):
            pass
        process.exit(exit_code)

        # Post-push: update pushed_at, sync branches, and trigger indexing
        if git_cmd == "git-receive-pack" and exit_code == 0:
            try:
                from datetime import datetime, timezone
                from app.database import async_session
                from app.services.index_service import index_repository
                from app.git.smart_http import _sync_branches_to_db

                async with async_session() as db:
                    from sqlalchemy import select
                    from app.models.repository import Repository as RepoModel
                    result = await db.execute(
                        select(RepoModel).where(RepoModel.id == repo.id)
                    )
                    fresh_repo = result.scalar_one_or_none()
                    if fresh_repo:
                        fresh_repo.pushed_at = datetime.now(timezone.utc)
                        await db.commit()
                        await _sync_branches_to_db(db, fresh_repo)
                        await index_repository(db, fresh_repo)
            except Exception:
                pass

    async def start_ssh_server() -> Optional[asyncssh.SSHAcceptor]:
        """Start the SSH server. Returns the server acceptor or None."""
        if not settings.SSH_ENABLED:
            logger.info("SSH transport disabled")
            return None

        try:
            key_path = await _ensure_host_key()
            server = await asyncssh.create_server(
                GitSSHServer,
                "",
                settings.SSH_PORT,
                server_host_keys=[key_path],
                process_factory=_handle_git_process,
                encoding=None,  # Binary mode — git protocol is binary
            )
            logger.info("SSH server listening on port %d", settings.SSH_PORT)
            return server
        except Exception:
            logger.exception("Failed to start SSH server")
            return None

else:
    async def start_ssh_server():
        logger.warning("asyncssh not installed; SSH transport disabled")
        return None
