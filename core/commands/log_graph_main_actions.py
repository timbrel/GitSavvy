from __future__ import annotations

from functools import partial
import os
from itertools import chain
from typing import Callable, Dict, List, Optional, Tuple

import sublime
from sublime_plugin import WindowCommand

from ...common import util
from ..fns import filter_, unique
from ..git_command import GitCommand
from ..git_mixins.branches import Branch
from ..ui__quick_panel import SEPARATOR, show_quick_panel
from . import multi_selector
from .log_graph_helper import (
    LineInfo,
    ListItems,
    describe_graph_line,
    describe_head,
    format_revision_list,
)


class gs_log_graph_action(WindowCommand, GitCommand):
    selected_index = 0

    def run(self) -> None:
        view = self.window.active_view()
        if not view:
            return

        branches = {b.canonical_name: b for b in self.get_branches()}
        infos = list(filter_(
            describe_graph_line(line, branches)
            for line in unique(
                view.substr(line)
                for s in multi_selector.get_selection(view)
                for line in view.lines(s)
            )
        ))
        if not infos:
            return

        actions = (
            self.actions_for_single_line(view, infos[0], branches)
            if len(infos) == 1
            else self.actions_for_multiple_lines(view, infos)
        )
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

    def _get_file_path(self, view: sublime.View) -> Optional[str]:
        settings = view.settings()
        apply_filters = settings.get("git_savvy.log_graph_view.apply_filters")
        paths = (
            settings.get("git_savvy.log_graph_view.paths", [])
            if apply_filters
            else []
        )  # type: List[str]
        if len(paths) == 1:
            return os.path.normcase(os.path.join(self.repo_path, paths[0]))

        return None

    def actions_for_multiple_lines(
        self, view: sublime.View, infos: List[LineInfo]
    ) -> List[Tuple[str, Callable[[], None]]]:
        file_path = self._get_file_path(view)
        actions: List[Tuple[str, Callable[[], None]]] = []

        if len(infos) == 2:
            def display_name(info: LineInfo) -> str:
                if info.get("local_branches"):
                    return info["local_branches"][0]
                branches = info.get("branches", [])
                if len(branches) == 1:
                    return branches[0]
                elif len(branches) == 0 and info.get("tags"):
                    return info["tags"][0]
                else:
                    return info["commit"]

            base_commit = display_name(infos[1])
            target_commit = display_name(infos[0])

            actions += [
                (
                    "Diff {}{}...{}".format(
                        "file " if file_path else "", base_commit, target_commit
                    ),
                    partial(self.sym_diff_two_commits, base_commit, target_commit, file_path)
                ),
                (
                    "Diff {}{}..{}".format(
                        "file " if file_path else "", base_commit, target_commit
                    ),
                    partial(self.diff_commit, base_commit, target_commit, file_path)
                ),
                (
                    "Compare {}'{}' and '{}'".format(
                        "file between " if file_path else "", base_commit, target_commit
                    ),
                    partial(self.compare_commits, base_commit, target_commit, file_path)
                ),
                (
                    "Show file history from {}..{}".format(base_commit, target_commit)
                    if file_path
                    else "Show graph for {}..{}".format(base_commit, target_commit),
                    partial(self.graph_two_revisions, base_commit, target_commit, file_path)
                ),
                (
                    "Show file history from {}..{}".format(target_commit, base_commit)
                    if file_path
                    else "Show graph for {}..{}".format(target_commit, base_commit),
                    partial(self.graph_two_revisions, target_commit, base_commit, file_path)
                )

            ]

        pickable = list(reversed([
            info["commit"]
            for info in infos
            if "HEAD" not in info
        ]))
        if pickable:
            actions += [
                (
                    "Cherry-pick {}".format(format_revision_list(pickable)),
                    partial(self.cherry_pick, *pickable)
                )
            ]

        revertable = list(reversed([info["commit"] for info in infos]))
        actions += [
            (
                "Revert {}".format(format_revision_list(revertable)),
                partial(self.revert_commit, *revertable)
            )
        ]

        return actions

    def sym_diff_two_commits(self, base_commit: str, target_commit: str, file_path: Optional[str] = None) -> None:
        self.window.run_command("gs_diff", {
            "in_cached_mode": False,
            "file_path": file_path,
            "base_commit": "{}...{}".format(base_commit, target_commit),
            "disable_stage": True
        })

    def graph_two_revisions(self, base_commit: str, target_commit: str, file_path: Optional[str] = None) -> None:
        branches = ["{}..{}".format(base_commit, target_commit)]
        self.window.run_command("gs_graph", {
            'all': False,
            'file_path': file_path,
            'branches': branches,
            'follow': base_commit
        })

    def actions_for_single_line(
        self, view: sublime.View, info: LineInfo, branches: Dict[str, Branch]
    ) -> List[Tuple[str, Callable[[], None]]]:
        commit_hash = info["commit"]
        file_path = self._get_file_path(view)
        actions: List[Tuple[str, Callable[[], None]]] = []
        on_checked_out_branch = "HEAD" in info and info["HEAD"] in info.get("local_branches", [])
        if on_checked_out_branch:
            current_branch = info["HEAD"]
            b = branches[current_branch]
            if b.upstream:
                actions += [
                    ("Fetch", partial(self.fetch, current_branch)),
                    ("Pull", self.pull),
                ]
            actions += [
                ("Push", partial(self.push, current_branch)),
                ("Rebase on...", partial(self.rebase_on)),
                SEPARATOR,
            ]

        actions += [
            ("Checkout '{}'".format(branch_name), partial(self.checkout, branch_name))
            for branch_name in info.get("local_branches", [])
            if info.get("HEAD") != branch_name
        ]

        good_commit_name = (
            info["tags"][0]
            if info.get("tags")
            else commit_hash
        )
        if "HEAD" not in info or info["HEAD"] != commit_hash:
            actions += [
                (
                    "Checkout '{}' detached".format(good_commit_name),
                    partial(self.checkout, good_commit_name)
                ),
            ]

        for branch_name in info.get("local_branches", []):
            if branch_name == info.get("HEAD"):
                continue

            b = branches[branch_name]
            if b.upstream and b.upstream.status != "gone":
                if "behind" in b.upstream.status and "ahead" not in b.upstream.status:
                    actions += [
                        (
                            "Fast-forward '{}' to '{}'".format(branch_name, b.upstream.canonical_name),
                            partial(self.move_branch, branch_name, b.upstream.canonical_name)
                        ),
                    ]
                else:
                    actions += [
                        (
                            "Update '{}' from '{}'".format(branch_name, b.upstream.canonical_name),
                            partial(self.update_from_tracking, b.upstream.remote, b.upstream.branch, b.name)
                        ),
                    ]

                actions += [
                    (
                        "Push '{}' to '{}'".format(branch_name, b.upstream.canonical_name),
                        partial(self.push, branch_name)
                    ),
                ]
            else:
                actions += [
                    (
                        "Push '{}'".format(branch_name),
                        partial(self.push, branch_name)
                    ),
                ]

        if file_path:
            actions += [
                ("Show file at commit", partial(self.show_file_at_commit, commit_hash, file_path)),
                ("Blame file at commit", partial(self.blame_file_atcommit, commit_hash, file_path)),
                (
                    "Checkout file at commit",
                    partial(self.checkout_file_at_commit, commit_hash, file_path)
                )
            ]

        actions += [
            (
                "Create branch at '{}'".format(good_commit_name),
                partial(self.create_branch, commit_hash)
            ),
            (
                "Create tag at '{}'".format(commit_hash),
                partial(self.create_tag, commit_hash)
            )
        ]
        actions += [
            ("Delete tag '{}'".format(tag_name), partial(self.delete_tag, tag_name))
            for tag_name in info.get("tags", [])
        ]

        head_info = describe_head(view, branches)
        head_is_on_a_branch = head_info and head_info["HEAD"] != head_info["commit"]
        cursor_is_not_on_head = head_info and head_info["commit"] != info["commit"]

        def get_list(info: LineInfo, key: ListItems) -> List[str]:
            return info.get(key, [])

        if head_info and head_is_on_a_branch and cursor_is_not_on_head:
            get = partial(get_list, info)  # type: Callable[[ListItems], List[str]]
            good_move_target = next(
                chain(get("local_branches"), get("branches")),
                good_commit_name
            )
            actions += [
                (
                    "Move '{}' to '{}'".format(head_info["HEAD"], good_move_target),
                    partial(self.checkout_b, head_info["HEAD"], good_commit_name)
                ),
            ]

        if not head_info or cursor_is_not_on_head:
            good_head_name = (
                "'{}'".format(head_info["HEAD"])  # type: ignore
                if head_is_on_a_branch
                else "HEAD"
            )
            get_lists_from_info: Callable[[ListItems], List[str]] = partial(get_list, info)
            good_reset_target = next(
                chain(
                    get_lists_from_info("local_branches"),
                    get_lists_from_info("branches"),
                ),
                good_commit_name
            )
            actions += [
                (
                    "Reset {} to '{}'".format(good_head_name, good_reset_target),
                    partial(self.reset_to, good_reset_target)
                )
            ]

        if head_info and not head_is_on_a_branch and cursor_is_not_on_head:
            get_lists_from_head: Callable[[ListItems], List[str]] = partial(get_list, head_info)
            good_move_target = next(
                (
                    "'{}'".format(name)
                    for name in chain(
                        get_lists_from_head("local_branches"),
                        get_lists_from_head("branches"),
                        get_lists_from_head("tags"),
                    )
                ),
                "HEAD"
            )
            actions += [
                (
                    "Move '{}' to {}".format(branch_name, good_move_target),
                    partial(self.checkout_b, branch_name)
                )
                for branch_name in info.get("local_branches", [])
            ]

        actions += [
            ("Delete branch '{}'".format(branch_name), partial(self.delete_branch, branch_name))
            for branch_name in info.get("local_branches", [])
        ]

        if "HEAD" not in info:
            actions += [
                ("Cherry-pick commit", partial(self.cherry_pick, commit_hash)),
            ]

        actions += [
            ("Revert commit", partial(self.revert_commit, commit_hash)),
        ]
        if not head_info or cursor_is_not_on_head:
            good_head_name = (
                "'{}'".format(head_info["HEAD"])  # type: ignore[index]
                if head_is_on_a_branch
                else "HEAD"
            )
            get_lists_from_cursor = partial(get_list, info)
            good_target_name = next(
                chain(
                    get_lists_from_cursor("local_branches"),
                    get_lists_from_cursor("branches"),
                ),
                good_commit_name
            )
            actions += [
                (
                    "Compare '{}' with {}".format(good_target_name, good_head_name),
                    partial(
                        self.compare_commits,
                        head_info["HEAD"] if head_is_on_a_branch else commit_hash,  # type: ignore[index]
                        good_target_name,
                        file_path=file_path,
                    )
                )
            ]
        else:
            interesting_candidates = branches.keys() & {"main", "master", "dev"}
            target_hints = sorted(interesting_candidates, key=lambda branch: -branches[branch].committerdate)
            actions += [
                (
                    "Compare {}against ...".format("file " if file_path else ""),
                    partial(
                        self.compare_against,
                        info["HEAD"] if on_checked_out_branch else commit_hash,
                        file_path=file_path,
                        target_hints=target_hints
                    )
                ),
            ]

        if file_path:
            actions += [
                (
                    "Diff file against workdir",
                    partial(self.diff_commit, commit_hash, file_path=file_path)
                ),
            ]
        elif "HEAD" in info:
            actions += [
                ("Diff against workdir", self.diff),
            ]
        else:
            actions += [
                (
                    "Diff '{}' against HEAD".format(good_commit_name),
                    partial(self.diff_commit, commit_hash, target_commit="HEAD")
                ),
            ]
        return actions

    def pull(self) -> None:  # type: ignore[override]
        self.window.run_command("gs_pull")

    def push(self, current_branch):  # type: ignore[override]
        self.window.run_command("gs_push", {"local_branch_name": current_branch})

    def fetch(self, current_branch):  # type: ignore[override]
        remote = self.get_remote_for_branch(current_branch)
        self.window.run_command("gs_fetch", {"remote": remote} if remote else None)

    def rebase_on(self) -> None:
        self.window.run_command("gs_rebase_on_branch")

    def update_from_tracking(self, remote: str, remote_name: str, local_name: str) -> None:
        self.window.run_command("gs_fetch", {
            "remote": remote,
            "refspec": "{}:{}".format(remote_name, local_name)
        })

    def checkout(self, commit_hash):
        self.window.run_command("gs_checkout_branch", {"branch": commit_hash})

    def checkout_b(self, branch_name, start_point=None):
        self.window.run_command("gs_checkout_new_branch", {
            "branch_name": branch_name,
            "start_point": start_point,
            "force": True,
        })

    def move_branch(self, branch_name, target):
        self.git("branch", "-f", branch_name, target)
        util.view.refresh_gitsavvy_interfaces(self.window)

    def delete_branch(self, branch_name):
        self.window.run_command("gs_delete_branch", {"branch": branch_name})

    def show_commit(self, commit_hash):
        self.window.run_command("gs_show_commit", {"commit_hash": commit_hash})

    def create_branch(self, commit_hash):
        self.window.run_command("gs_create_branch", {"start_point": commit_hash})

    def create_tag(self, commit_hash):
        self.window.run_command("gs_tag_create", {"target_commit": commit_hash})

    def delete_tag(self, tag_name):
        self.git("tag", "-d", tag_name)
        util.view.refresh_gitsavvy_interfaces(self.window)

    def reset_to(self, commitish):
        self.window.run_command("gs_reset", {"commit_hash": commitish})

    def cherry_pick(self, *commit_hash):
        try:
            self.git("cherry-pick", *commit_hash)
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def revert_commit(self, *commit_hash):
        self.window.run_command("gs_revert_commit", {"commit_hash": commit_hash})

    def compare_commits(self, base_commit, target_commit, file_path=None):
        self.window.run_command("gs_compare_commit", {
            "base_commit": base_commit,
            "target_commit": target_commit,
            "file_path": file_path
        })

    def compare_against(self, base_commit, file_path=None, target_hints=None):
        nearest_tag = self.git("describe", "--abbrev=0").strip()
        if nearest_tag:
            if target_hints is None:
                target_hints = []
            target_hints += [nearest_tag]
        self.window.run_command("gs_compare_against", {
            "base_commit": base_commit,
            "file_path": file_path,
            "target_hints": target_hints
        })

    def copy_sha(self, commit_hash):
        sublime.set_clipboard(self.git("rev-parse", commit_hash).strip())

    def diff(self):
        self.window.run_command("gs_diff", {"in_cached_mode": False})

    def diff_commit(self, base_commit, target_commit=None, file_path=None):
        self.window.run_command("gs_diff", {
            "in_cached_mode": False,
            "file_path": file_path,
            "base_commit": base_commit,
            "target_commit": target_commit,
            "disable_stage": True
        })

    def show_file_at_commit(self, commit_hash, file_path):
        self.window.run_command("gs_show_file_at_commit", {
            "commit_hash": commit_hash,
            "filepath": file_path
        })

    def blame_file_atcommit(self, commit_hash, file_path):
        self.window.run_command("gs_blame", {
            "commit_hash": commit_hash,
            "file_path": file_path
        })

    def checkout_file_at_commit(self, commit_hash, file_path):
        self.checkout_ref(commit_hash, fpath=file_path)
        util.view.refresh_gitsavvy_interfaces(self.window)
