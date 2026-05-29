from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import threading
import time
from typing import DefaultDict, Iterator, List, NamedTuple, Tuple

from typing_extensions import TypeAlias

import sublime
from sublime_plugin import EventListener

from ...common import util
from ..base_commands import GsTextCommand
from ..git_command import GitCommand
from ..types import CommitHash, FullHash, ShortHash
from ..ui__quick_panel import show_quick_panel


__all__ = (
    "gs_ref_undo",
    "GsRefUndoCleanup",
)


class RefUndoAction(NamedTuple):
    description: str
    command: Tuple[str, ...]
    timestamp: float


UndoOwner: TypeAlias = "sublime.ViewId"


undo_actions_by_owner: DefaultDict[UndoOwner, List[RefUndoAction]] = defaultdict(list)
lock = threading.Lock()


class gs_ref_undo(GsTextCommand):
    def run(self, edit) -> None:
        undo_owner = self.view.id()
        undo_actions = get_undo_actions(undo_owner)
        if not undo_actions:
            self.window.status_message("No ref deletion to undo.")
            return

        def on_selection(index: int) -> None:
            undo_action = undo_actions[index]
            self.git(*undo_action.command)
            remove_undo_action(undo_owner, undo_action)
            self.window.status_message(undo_action.description + ".")
            util.view.refresh_gitsavvy_interfaces(self.window)

        show_quick_panel(
            self.window,
            [undo_action.description for undo_action in undo_actions],
            on_selection
        )


class GsRefUndoCleanup(EventListener):
    def on_close(self, view) -> None:
        clear_undo_actions(view.id())


def add_branch_undo(
    cmd: GitCommand,
    branch_name: str,
    old_hash: FullHash | ShortHash,
    undo_owner: UndoOwner
) -> None:
    add_undo_action(
        undo_owner,
        RefUndoAction(
            "Re-create branch '{}' at {}".format(branch_name, cmd.to_short_hash(old_hash)),
            ("branch", branch_name, old_hash),
            time.time()
        )
    )


def add_branch_move_undo(
    cmd: GitCommand,
    branch_name: str,
    old_hash: FullHash | ShortHash,
    undo_owner: UndoOwner
) -> None:
    add_undo_action(
        undo_owner,
        RefUndoAction(
            "Move branch '{}' back to {}".format(branch_name, cmd.to_short_hash(old_hash)),
            ("branch", "--force", branch_name, old_hash),
            time.time()
        )
    )


@contextmanager
def record_tag_recreate_action(
    cmd: GitCommand,
    tag_name: str,
    undo_owner: UndoOwner | None = None
) -> Iterator[None]:
    tag_ref_hash, dereferenced_target_hash = cmd.resolve_tag(tag_name)

    yield

    add_tag_undo(cmd, tag_name, tag_ref_hash, dereferenced_target_hash, undo_owner)


def add_tag_undo(
    cmd: GitCommand,
    tag_name: str,
    tag_ref_hash: CommitHash,
    dereferenced_target_hash: CommitHash,
    undo_owner: UndoOwner | None = None
) -> None:
    undo_owner = undo_owner or resolve_undo_owner(cmd)
    if undo_owner is None:
        return

    # Undo via `update-ref` instead of `git tag --force` so annotated tags
    # are restored as the exact same tag object.  That preserves the tagger,
    # timestamp, annotation message, and signature.  The trade-off is that
    # the tag object becomes unreachable after deletion and undo can fail if
    # Git prunes it before the user restores the ref.
    add_undo_action(
        undo_owner,
        RefUndoAction(
            "Re-create tag '{}' at {}".format(
                tag_name, cmd.to_short_hash(dereferenced_target_hash)),
            ("update-ref", f"refs/tags/{tag_name}", tag_ref_hash),
            time.time()
        )
    )


def add_undo_action(undo_owner: UndoOwner, undo_action: RefUndoAction) -> None:
    with lock:
        undo_actions_by_owner[undo_owner].append(undo_action)


def get_undo_actions(undo_owner: UndoOwner) -> List[RefUndoAction]:
    return sorted(
        current_undo_actions(undo_owner),
        key=lambda undo_action: undo_action.timestamp,
        reverse=True
    )


def remove_undo_action(undo_owner: UndoOwner, undo_action: RefUndoAction) -> None:
    with lock:
        actions = undo_actions_by_owner.get(undo_owner)
        if not actions:
            return

        actions.remove(undo_action)
        if not actions:
            del undo_actions_by_owner[undo_owner]


def clear_undo_actions(undo_owner: UndoOwner) -> None:
    with lock:
        undo_actions_by_owner.pop(undo_owner, None)


def current_undo_actions(undo_owner: UndoOwner) -> List[RefUndoAction]:
    with lock:
        return list(undo_actions_by_owner.get(undo_owner, []))


def resolve_undo_owner(cmd: GitCommand) -> UndoOwner | None:
    view = cmd._current_view()
    return view.id() if view else None
