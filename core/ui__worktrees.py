from __future__ import annotations

from functools import partial
import re
from typing import Callable

import sublime

from .exceptions import GitSavvyError
from .git_command import GitCommand
from .ui__quick_panel import noop, show_actions_panel


def remove_worktree_with_recovery(
    cmd: GitCommand,
    window: sublime.Window,
    path: str,
    force: bool,
    unlock: bool,
    retry: Callable[..., None]
) -> None:
    try:
        cmd.remove_worktree(path, force=force, unlock=unlock)
    except GitSavvyError as e:
        if match := LOCKED_WORKTREE_RE.search(e.stderr):
            reason = f" ({match[1]})" if match[1] else ""
            show_actions_panel(window, [
                noop(f"Abort, worktree is locked{reason}."),
                (
                    "Unlock and delete the worktree",
                    partial(retry, path, force, unlock=True)
                )
            ])
        elif "contains modified or untracked files" in e.stderr and not force:
            show_actions_panel(window, [
                noop("Abort, worktree contains modified or untracked files."),
                ("Open worktree in a new window", partial(cmd.open_worktree_in_new_window, path)),
                ("Delete anyway", partial(retry, path, True))
            ])
        else:
            e.show_error_panel()
            raise


LOCKED_WORKTREE_RE = re.compile(r"cannot remove a locked working tree(?:, lock reason: ([^\n]*))?")
