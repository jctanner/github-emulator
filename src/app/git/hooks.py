"""Server-side git hook scripts.

Installs hook scripts in bare repositories for post-receive processing.
"""

import os
import stat


POST_RECEIVE_HOOK = """#!/bin/sh
# GitHub Emulator post-receive hook
# This hook is called after a successful push.
# It can be used to trigger webhook deliveries, update stats, etc.
#
# For now, the emulator handles post-push processing in the API layer,
# so this hook is a no-op placeholder.
exit 0
"""

PRE_RECEIVE_HOOK = """#!/bin/sh
# GitHub Emulator pre-receive hook
# This hook runs before refs are updated.
# It can be used to enforce branch protection rules.
#
# Input: oldref newref refname (one line per ref)
exit 0
"""


def install_hooks(disk_path: str) -> None:
    """Install default hook scripts in a bare repository."""
    hooks_dir = os.path.join(disk_path, "hooks")
    os.makedirs(hooks_dir, exist_ok=True)

    for name, content in [
        ("post-receive", POST_RECEIVE_HOOK),
        ("pre-receive", PRE_RECEIVE_HOOK),
    ]:
        hook_path = os.path.join(hooks_dir, name)
        with open(hook_path, "w") as f:
            f.write(content)
        os.chmod(hook_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
