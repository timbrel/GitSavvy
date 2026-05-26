from __future__ import annotations

from contextlib import contextmanager
import time
from typing import cast, Iterator, List, NamedTuple, Tuple

from sublime_plugin import WindowCommand

from ...common import util
from ..git_command import GitCommand
from ..types import ShortHash
from ..ui__quick_panel import show_quick_panel


__all__ = (
    "RefUndoAction",
    "add_branch_undo",
    "add_tag_undo",
    "add_undo_action",
    "record_tag_recreate_action",
    "gs_ref_undo",
)


class RefUndoAction(NamedTuple):
    description: str
    command: Tuple[str, ...]
    timestamp: float


class gs_ref_undo(WindowCommand, GitCommand):
    def run(self) -> None:
        undo_actions = get_undo_actions(self)
        if not undo_actions:
            self.window.status_message("No ref deletion to undo.")
            return

        def on_selection(index: int) -> None:
            undo_action = undo_actions[index]
            self.git(*undo_action.command)
            remove_undo_action(self, undo_action)
            self.window.status_message(undo_action.description + ".")
            util.view.refresh_gitsavvy_interfaces(self.window)

        show_quick_panel(
            self.window,
            [undo_action.description for undo_action in undo_actions],
            on_selection
        )


def add_branch_undo(cmd: GitCommand, branch_name: str, old_hash: str) -> None:
    add_undo_action(
        cmd,
        RefUndoAction(
            "Re-create branch '{}' at {}".format(branch_name, cmd.get_short_hash(old_hash)),
            ("branch", branch_name, old_hash),
            time.time()
        )
    )


@contextmanager
def record_tag_recreate_action(
    cmd: GitCommand,
    tag_name: str,
    tag_ref_hash: ShortHash | None = None,
    dereferenced_target_hash: ShortHash | None = None
) -> Iterator[None]:
    ref = f"refs/tags/{tag_name}"
    tag_ref_hash = tag_ref_hash or cmd.git("rev-parse", "--short", ref).strip()
    dereferenced_target_hash = (
        dereferenced_target_hash
        or cmd.git("rev-parse", "--short", f"{ref}^{{}}").strip()
    )

    yield

    add_tag_undo(cmd, tag_name, tag_ref_hash, dereferenced_target_hash)


def add_tag_undo(
    cmd: GitCommand,
    tag_name: str,
    tag_ref_hash: ShortHash,
    dereferenced_target_hash: ShortHash,
) -> None:
    # Undo via `update-ref` instead of `git tag --force` so annotated tags
    # are restored as the exact same tag object.  That preserves the tagger,
    # timestamp, annotation message, and signature.  The trade-off is that
    # the tag object becomes unreachable after deletion and undo can fail if
    # Git prunes it before the user restores the ref.
    add_undo_action(
        cmd,
        RefUndoAction(
            "Re-create tag '{}' at {}".format(tag_name, dereferenced_target_hash),
            ("update-ref", f"refs/tags/{tag_name}", tag_ref_hash),
            time.time()
        )
    )


def add_undo_action(cmd: GitCommand, undo_action: RefUndoAction) -> None:
    actions = list(current_undo_actions(cmd))
    actions.append(undo_action)
    cmd.update_store({"ref_undo_actions": actions})


def get_undo_actions(cmd: GitCommand) -> List[RefUndoAction]:
    return sorted(
        current_undo_actions(cmd),
        key=lambda undo_action: undo_action.timestamp,
        reverse=True
    )


def remove_undo_action(cmd: GitCommand, undo_action: RefUndoAction) -> None:
    actions = list(current_undo_actions(cmd))
    if not actions:
        return

    actions.remove(undo_action)
    cmd.update_store({"ref_undo_actions": actions})


def current_undo_actions(cmd: GitCommand) -> List[RefUndoAction]:
    return cast(
        List[RefUndoAction],
        cmd.current_state().get("ref_undo_actions", [])
    )
