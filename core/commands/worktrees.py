from __future__ import annotations

from functools import partial
import re

import sublime

from ...common import util
from . import ref_undo
from ..exceptions import GitSavvyError
from ..git_command import GitCommand
from ..git_mixins.worktrees import WorktreeTarget
from ..runtime import on_new_thread
from ..ui__busy_spinner import busy_indicator
from ..ui__quick_panel import noop, show_actions_panel


__all__ = ()


def remove_worktree(
    cmd: GitCommand,
    view: sublime.View,
    target: WorktreeTarget,
    force: bool = False,
    *,
    unlock: bool = False
) -> None:
    window = view.window()
    if window is None:
        return
    retry = partial(on_new_thread(remove_worktree), cmd, view, target)
    try:
        with busy_indicator(view, start_after=0.5):
            cmd.remove_worktree(target.path, force=force, unlock=unlock)
    except GitSavvyError as e:
        if match := LOCKED_WORKTREE_RE.search(e.stderr):
            reason = f" ({match[1]})" if match[1] else ""
            show_actions_panel(window, [
                noop(f"Abort, worktree is locked{reason}."),
                (
                    "Unlock and delete the worktree",
                    partial(retry, force, unlock=True)
                )
            ])
        elif "contains modified or untracked files" in e.stderr and not force:
            show_actions_panel(window, [
                noop("Abort, worktree contains modified or untracked files."),
                ("Open worktree in a new window", partial(cmd.open_worktree_in_new_window, target.path)),
                ("Delete anyway", partial(retry, True))
            ])
        else:
            e.show_error_panel()
            raise
    else:
        ref_undo.add_worktree_undo(target, view.id())
    finally:
        util.view.refresh_gitsavvy_interfaces(window, refresh_sidebar=True)


LOCKED_WORKTREE_RE = re.compile(r"cannot remove a locked working tree(?:, lock reason: ([^\n]*))?")
