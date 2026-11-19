from __future__ import annotations

from functools import partial
from itertools import starmap

import sublime
from sublime_plugin import EventListener

from ..base_commands import GsTextCommand
from ..git_command import GitCommand
from ..fns import unique
from ..git_mixins.branches import Branch
from ..ui__quick_panel import ActionType
from ..text_helper import line_from_pt
from . import log_graph_colorize as colorize
from .log_graph_branch_drag import get_drag_state
from .log_graph_helper import (
    COMMIT_LINE, Commit, commit_message_from_line, describe_head, find_base_commit_for_fixup,
    format_revision_list, is_fixup, is_fixup_or_squash_message
)
from .log_graph_prelude import PreludeControls
from .multi_selector import GRAPH_MOUSE_TARGET_KEY, normalize_mouse_selection


__all__ = ("gs_log_graph_mouse", "GsLogGraphMouseListener")


POPUP_SEPARATOR: ActionType = ("-", lambda: None)


class gs_log_graph_mouse(GsTextCommand):
    def run(
        self, edit: sublime.Edit, event: dict, release: bool = False,
        context_menu: bool = False, text_selection: str | None = None,
        fallback_selection: str | None = None, toggle_branch_choice: bool = False
    ) -> None:
        view = self.view
        view.settings().erase(GRAPH_MOUSE_TARGET_KEY)

        if release:
            view.run_command("gs_log_graph_branch_drag", {"action": "finish", "event": event})
            return

        window = view.window()
        if window is not None:
            window.focus_view(view)

        if text_selection is not None:
            select_text(view, event, text_selection)
            return

        pt = view.window_to_text((event["x"], event["y"]))
        if not context_menu and activate_prelude_control(view, pt, remove_choice=toggle_branch_choice):
            return

        if toggle_branch_choice:
            ref = view.expand_to_scope(
                pt, "meta.graph.branch.git-savvy constant.other.git.branch - meta.graph.branch-move"
            )
            view.settings().erase("git_savvy.mouse_selected_target")
            if ref is not None:
                view.run_command("gs_log_graph_edit_branches", {"branch": view.substr(ref), "keep_all_mode": True})
            else:
                args = {"event": event, "extend": True}
                if fallback_selection is not None:
                    args["by"] = fallback_selection
                view.run_command("drag_select", args)
            return

        if fallback_selection is not None:
            select_text(view, event, fallback_selection)
            return

        GraphMouse(view).press(pt, event, context_menu=context_menu)

    def want_event(self) -> bool:
        return True


class GsLogGraphMouseListener(EventListener):
    def on_selection_modified(self, view: sublime.View) -> None:
        settings = view.settings()
        if not settings.get("git_savvy.log_graph_view") and settings.get(GRAPH_MOUSE_TARGET_KEY) is None:
            return
        # Armed drags still own native word-selection normalization. Moving
        # within that gesture must not invalidate the clicked-label identity.
        if get_drag_state(view) is None:
            normalize_mouse_selection(view, GRAPH_MOUSE_TARGET_KEY)


def activate_prelude_control(view: sublime.View, pt: int, *, remove_choice: bool = False) -> bool:
    settings = view.settings()
    controls = PreludeControls.from_dict(settings.get("git_savvy.log_graph_view.prelude_controls", {}))

    if not remove_choice and controls.all is not None and controls.all.contains(pt):
        settings.erase("git_savvy.mouse_selected_target")
        view.run_command("gs_log_graph_toggle_all_setting")
        return True

    for toggle in controls.refs:
        if toggle.contains(pt):
            settings.erase("git_savvy.mouse_selected_target")
            view.run_command("gs_log_graph_edit_branches", {
                "branch": toggle.ref,
                "action": "remove" if remove_choice else "toggle_visibility",
                "keep_all_mode": remove_choice
            })
            return True

    return False


def select_text(view: sublime.View, event: dict, by: str) -> None:
    view.settings().erase("git_savvy.mouse_selected_target")
    args: dict = {"event": event}
    if by != "characters":
        args["by"] = by

    view.run_command("drag_select", args)


class GraphMouse(GitCommand):
    def __init__(self, view: sublime.View) -> None:
        self.view = view

    def press(self, pt: int, event: dict, *, context_menu: bool = False) -> None:

        view = self.view
        settings = view.settings()

        if context_menu:
            selected: list[list[int]] = settings.get("git_savvy.multi_selection", [])
            if selected and not any(a <= pt < b for a, b in selected):
                view.run_command("gs_clear_multiselect")
                return

        target = self.target_at(pt)
        if context_menu and target is None:
            view.run_command("context_menu", {"event": event})
            return

        view.sel().clear()
        view.sel().add(target if target is not None else sublime.Region(pt))
        settings.set("git_savvy.mouse_selected_target", [target.a, target.b] if target else None)
        colorize.colorize_dots(view)
        colorize.colorize_fixups(view)

        if context_menu:
            match = COMMIT_LINE.search(view.substr(view.line(pt)))
            if match and target is not None:
                commit_hash = match.group("commit_hash")
                if self.show_menu(pt, target, commit_hash):
                    return
            view.run_command("context_menu", {"event": event})
            return

        if target is not None and view.match_selector(
            target.a, "meta.graph.branch.git-savvy constant.other.git.branch"
        ):

            view.run_command("gs_log_graph_branch_drag", {"event": event})
            if get_drag_state(view) is not None:
                return

        args: dict = {"event": event}
        if target is not None:
            settings.set(GRAPH_MOUSE_TARGET_KEY, [target.a, target.b])
            args["by"] = "words"
        # Let this TextCommand finish so Sublime draws the selection before
        # native drag tracking takes over until mouse release.
        sublime.set_timeout(lambda: view.run_command("drag_select", args), 0)

    def show_menu(self, pt: int, target: sublime.Region, commit_hash: str) -> bool:
        """Open actions for the clicked label; return False for the native menu."""
        view = self.view
        window = view.window()
        if window is None:
            return False

        actions: list[ActionType]
        branch = self.branch_at(pt)
        if branch is not None:
            actions = self.branch_actions(window, branch)
        elif view.match_selector(target.a, "string.other.rebase-hint.git-savvy"):
            actions = self.fixup_actions(pt, commit_hash)
            if not actions:
                return False
        elif not view.match_selector(pt, "constant.numeric.graph.commit-hash.git-savvy"):
            return False
        else:
            actions = self.commit_actions(window, pt, commit_hash)

        view.sel().clear()
        view.sel().add(target)

        def on_done(index: int) -> None:
            if 0 <= index < len(actions):
                _, action = actions[index]
                action()

        # The popup takes over the gesture and can swallow mouse-up. Do not
        # keep anchoring later keyboard selections to the pressed label.
        view.settings().erase(GRAPH_MOUSE_TARGET_KEY)
        view.show_popup_menu([description for description, _ in actions], on_done, pt)
        return True

    def branch_actions(self, window: sublime.Window, branch: Branch) -> list[ActionType]:
        name = branch.name
        worktrees = self.current_state().get("worktree_locations", [])
        worktree = next((wt for wt in worktrees if wt.branch_name == name), None)
        checked_out = branch.active or worktree is not None

        actions: list[ActionType] = []
        if not checked_out:
            actions.append((
                "Checkout here",
                partial(window.run_command, "gs_checkout_branch", {"branch": name})
            ))
        elif worktree and not worktree.is_current:
            actions.append((
                "Open worktree in a new window",
                partial(window.run_command, "gs_open_worktree_in_new_window", {
                    "worktree": worktree.path
                })
            ))

        actions += [
            (
                "Checkout in a new worktree" if checked_out else "Checkout in a worktree",
                partial(window.run_command, "gs_checkout_in_worktree", {
                    "start_point": branch.commit_hash if checked_out else name
                })
            ),
            POPUP_SEPARATOR,
            (
                "Push" if branch.upstream else "Push...",
                partial(window.run_command, "gs_push", {"local_branch_name": name})
            )
        ]

        if branch.active and not self.in_bisect():
            actions.append((
                "Merge into...",
                partial(window.run_command, "gs_merge_current_branch_into", {
                    "source": name
                })
            ))

        if branch.upstream:
            if branch.active:
                actions.append((
                    "Pull",
                    partial(window.run_command, "gs_pull", {})
                ))
            elif not checked_out and "behind" in branch.upstream.status and "ahead" not in branch.upstream.status:
                actions.append((
                    "Move {} to {}".format(name, branch.upstream.canonical_name),
                    partial(window.run_command, "gs_move_branch", {
                        "branch": name,
                        "target": branch.upstream.canonical_name,
                        "previous_tip": branch.commit_hash
                    })
                ))
            elif not checked_out:
                actions.append((
                    "Update {} from {} (ff-only)".format(name, branch.upstream.canonical_name),
                    partial(window.run_command, "gs_fetch", {
                        "remote": branch.upstream.remote,
                        "refspec": "{}:{}".format(branch.upstream.branch, name)
                    })
                ))
            actions.append((
                "Fetch {}".format(branch.upstream.canonical_name),
                partial(window.run_command, "gs_fetch", {
                    "remote": branch.upstream.remote,
                    "refspec": branch.upstream.branch
                })
            ))

        actions += [
            POPUP_SEPARATOR,
            (
                "Rename '{}'".format(name),
                partial(window.run_command, "gs_rename_branch", {"branch": name})
            ),
            (
                "Delete '{}'".format(name),
                partial(window.run_command, "gs_delete_branch", {
                    "branch": name,
                    "undo_owner": self.view.id()
                })
            )
        ]

        return actions

    def commit_actions(self, window: sublime.Window, pt: int, commit_hash: str) -> list[ActionType]:

        selected_regions: list[list[int]] = self.view.settings().get("git_savvy.multi_selection", [])
        selected_commits = list(unique(
            self.view.substr(r)
            for r in sorted(
                starmap(sublime.Region, selected_regions),
                reverse=True
            )
        ))

        if len(selected_commits) >= 2 and commit_hash in selected_commits:
            diff_actions: list[ActionType] = []
            if len(selected_commits) == 2:
                base_commit, target_commit = selected_commits
                diff_actions = [
                    (
                        "Diff {}..{}".format(base_commit, target_commit),
                        partial(window.run_command, "gs_diff", {
                            "base_commit": base_commit,
                            "target_commit": target_commit,
                            "in_cached_mode": False,
                            "disable_stage": True
                        })
                    ),
                    POPUP_SEPARATOR
                ]

            actions: list[ActionType] = [
                *diff_actions,
                (
                    "Squash {}".format(format_revision_list(selected_commits)),
                    partial(self.view.run_command, "gs_rebase_squash_commits", {
                        "base_commit": selected_commits[0],
                        "commits": selected_commits[1:]
                    })
                ),
                *self.cherry_pick_and_revert_actions(window, selected_commits)
            ]
        else:
            actions = [
                (
                    "Create branch at {}".format(commit_hash),
                    partial(window.run_command, "gs_create_branch", {
                        "start_point": commit_hash
                    })
                ),
                (
                    "Create tag...",
                    partial(window.run_command, "gs_create_tag", {
                        "target_commit": commit_hash
                    })
                ),
                POPUP_SEPARATOR,
                (
                    "Checkout here",
                    partial(window.run_command, "gs_checkout_branch", {
                        "branch": commit_hash,
                        "detach": True
                    })
                ),
                (
                    "Checkout in a worktree",
                    partial(window.run_command, "gs_checkout_in_worktree", {
                        "start_point": commit_hash
                    })
                ),
                POPUP_SEPARATOR,
                *self.fixup_actions(pt, commit_hash),
                (
                    "Reword",
                    partial(self.view.run_command, "gs_rebase_reword_commit", {
                        "commit_hash": commit_hash
                    })
                ),
                (
                    "Edit",
                    partial(self.view.run_command, "gs_rebase_edit_commit", {
                        "commit_hash": commit_hash
                    })
                ),
                (
                    "Drop",
                    partial(self.view.run_command, "gs_rebase_drop_commits", {
                        "base_commit": commit_hash,
                        "commits": [commit_hash]
                    })
                ),
                *self.cherry_pick_and_revert_actions(window, [commit_hash])
            ]

        return actions

    def fixup_actions(self, pt: int, commit_hash: str) -> list[ActionType]:

        line = line_from_pt(self.view, pt)
        message = commit_message_from_line(self.view, line)
        if not message or not is_fixup_or_squash_message(message):
            return []
        base_commit = find_base_commit_for_fixup(self.view, line, message)
        if base_commit is None:
            return []

        fix = Commit(commit_hash, message)
        return [(
            "Apply fix to {}".format(base_commit) if is_fixup(fix) else
            "Squash with {}".format(base_commit),
            partial(self.view.run_command, "gs_rebase_apply_fixup", {
                "base_commit": base_commit,
                "fixes": [fix]
            })
        )]

    def cherry_pick_and_revert_actions(self, window: sublime.Window, commits: list[str]) -> list[ActionType]:

        head = describe_head(self.view, {})
        pickable = [commit for commit in commits if head is None or commit != head["commit"]]

        actions: list[ActionType] = []
        if pickable:
            actions.append((
                "Cherry-pick" if len(commits) == 1 else
                "Cherry-pick {}".format(format_revision_list(pickable)),
                partial(window.run_command, "gs_cherry_pick", {"commit_hash": pickable})
            ))

        actions.append((
            "Revert this commit" if len(commits) == 1 else
            "Revert {}".format(format_revision_list(commits)),
            partial(window.run_command, "gs_revert_commit", {"commit_hash": commits})
        ))

        return actions

    def target_at(self, pt: int) -> sublime.Region | None:
        return next((
            region for region in self.view.find_by_selector(
                "constant.numeric.graph.commit-hash.git-savvy, "
                "meta.graph.branch.git-savvy constant.other.git.branch, "
                "string.other.rebase-hint.git-savvy"
            )
            if region.begin() <= pt < region.end()
        ), None)

    def branch_at(self, pt: int) -> Branch | None:
        for region in self.view.find_by_selector(
            "meta.graph.branch.git-savvy constant.other.git.branch"
        ):
            if region.begin() <= pt < region.end():
                name = self.view.substr(region)
                return next((
                    branch for branch in self.get_branches()
                    if not branch.is_remote and branch.name == name
                ), None)
        return None
