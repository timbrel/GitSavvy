from __future__ import annotations

from functools import partial
from typing import Callable

import sublime

from ..base_commands import GsWindowCommand
from ..ui__quick_panel import SEPARATOR, show_quick_panel
from ..utils import open_folder_in_new_window


__all__ = (
    "gs_status_action_menu",
)


class gs_status_action_menu(GsWindowCommand):
    selected_index = 0

    def run(self) -> None:
        view = self.window.active_view()
        if not view:
            return

        actions = self.standard_status_actions()
        if not actions:
            return

        def on_action_selection(index: int) -> None:
            self.selected_index = index
            description, action = actions[index]
            action()

        selected_index = self.selected_index
        if 0 <= selected_index < len(actions) - 1:
            selected_action = actions[selected_index]
            if selected_action == SEPARATOR:
                selected_index += 1

        show_quick_panel(
            self.window,
            [a[0] for a in actions],
            on_action_selection,
            selected_index=selected_index,
        )

    def standard_status_actions(self) -> list[tuple[str, Callable[[], None]]]:
        actions: list[tuple[str, Callable[[], None]]] = []

        if self.in_rebase():
            actions += [
                ("Rebase --continue", self.rebase_continue),
                ("Rebase --abort", self.rebase_abort),
                ("Rebase --skip", self.rebase_skip),
            ]
        elif self.in_cherry_pick():
            actions += [
                ("Cherry-pick --continue", self.cherry_pick_continue),
                ("Cherry-pick --abort", self.cherry_pick_abort),
                ("Cherry-pick --skip", self.cherry_pick_skip),
            ]
        elif self.in_revert():
            actions += [
                ("Revert --continue", self.revert_continue),
                ("Revert --abort", self.revert_abort),
                ("Revert --skip", self.revert_skip),
            ]
        elif self.in_merge():
            actions += [
                ("Merge --continue", self.merge_continue),
                ("Merge --abort", self.merge_abort),
            ]
        else:
            state = self.current_state()
            branches = state.get("branches", [])
            current_branch = next(
                (
                    branch
                    for branch in branches
                    if branch.active
                ),
                None,
            )
            current_branch_name = current_branch.name if current_branch else None
            upstream = current_branch.upstream if current_branch else None
            remote_name = upstream.remote if upstream else None

            actions += [
                ("Fetch", partial(self.fetch, remote_name)),
            ]
            if current_branch_name:
                if upstream:
                    actions += [
                        ("Pull", self.pull),
                    ]
                actions += [
                    ("Push", partial(self.push, current_branch_name)),
                    ("Rebase on...", self.rebase_on),
                ]

        actions += [
            SEPARATOR,
            ("Checkout new branch", self.checkout_new_branch),
            ("Checkout in a new worktree", self.create_worktree),
            ("Create tag", self.create_tag),
        ]

        return actions

    def rebase_continue(self) -> None:
        self.window.run_command("gs_rebase_continue")

    def rebase_abort(self) -> None:
        self.window.run_command("gs_rebase_abort")

    def rebase_skip(self) -> None:
        self.window.run_command("gs_rebase_skip")

    def cherry_pick_continue(self) -> None:
        self.window.run_command("gs_cherry_pick_continue")

    def cherry_pick_abort(self) -> None:
        self.window.run_command("gs_cherry_pick_abort")

    def cherry_pick_skip(self) -> None:
        self.window.run_command("gs_cherry_pick_skip")

    def revert_continue(self) -> None:
        self.window.run_command("gs_revert_continue")

    def revert_abort(self) -> None:
        self.window.run_command("gs_revert_abort")

    def revert_skip(self) -> None:
        self.window.run_command("gs_revert_skip")

    def merge_continue(self) -> None:
        self.window.run_command("gs_merge_continue")

    def merge_abort(self) -> None:
        self.window.run_command("gs_merge_abort")

    def pull(self) -> None:  # type: ignore[override]
        self.window.run_command("gs_pull")

    def push(self, current_branch: str) -> None:  # type: ignore[override]
        self.window.run_command("gs_push", {"local_branch_name": current_branch})

    def fetch(self, remote: str | None) -> None:  # type: ignore[override]
        self.window.run_command("gs_fetch", {"remote": remote} if remote else None)

    def rebase_on(self) -> None:
        self.window.run_command("gs_rebase_on_branch")

    def checkout_new_branch(self) -> None:
        self.window.run_command("gs_checkout_new_branch")

    def create_worktree(self) -> None:
        def callback(w: sublime.Window) -> None:
            if not w.views():
                w.run_command("gs_show_status")

        commit_hash = self.get_short_hash("HEAD")
        worktree_path = self.create_new_worktree(commit_hash)
        open_folder_in_new_window(worktree_path, then=callback)

    def create_tag(self) -> None:
        self.window.run_command("gs_tag_create")
