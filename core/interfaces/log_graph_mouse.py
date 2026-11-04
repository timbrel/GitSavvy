from __future__ import annotations

from time import monotonic

import sublime
from sublime_plugin import EventListener, TextCommand

from ..git_command import GitCommand


__all__ = ("gs_log_graph_mouse", "GsLogGraphMouseListener")

PRESS_KEY = "git_savvy.log_graph_mouse_press"
HOLD_MS = 400


class gs_log_graph_mouse(TextCommand, GitCommand):
    def run(self, edit: sublime.Edit, event: dict, release: bool = False) -> None:
        from .log_graph import COMMIT_LINE, colorize_dots, colorize_fixups

        view = self.view
        settings = view.settings()
        settings.erase(PRESS_KEY)
        if release:
            return

        pt = view.window_to_text((event["x"], event["y"]))
        target = self.target_at(pt)
        view.sel().clear()
        view.sel().add(target if target is not None else sublime.Region(pt))
        settings.set("git_savvy.mouse_selected_target", [target.a, target.b] if target else None)
        colorize_dots(view)
        colorize_fixups(view)
        match = COMMIT_LINE.search(view.substr(view.line(pt)))
        if match and target is not None:
            token = monotonic()
            settings.set(PRESS_KEY, [token, pt, target.a, target.b])
            commit_hash = match.group("commit_hash")
            change_count = view.change_count()
            sublime.set_timeout(
                lambda: self.show_menu(token, pt, commit_hash, change_count), HOLD_MS
            )
        args: dict = {"event": event}
        if target is not None:
            args["by"] = "words"
        view.run_command("drag_select", args)

    def want_event(self) -> bool:
        return True

    def show_menu(self, token: float, pt: int, commit_hash: str, change_count: int) -> None:
        view = self.view
        if not view.is_valid():
            return
        press = view.settings().get(PRESS_KEY)
        if press is None or press[:2] != [token, pt]:
            return
        view.settings().erase(PRESS_KEY)
        window = view.window()
        if (
            window is None
            or window.active_view() != view
            or not view.settings().get("git_savvy.log_graph_view")
            or view.change_count() != change_count
            or len(view.sel()) != 1
            or view.sel()[0] != sublime.Region(press[2], press[3])
        ):
            return

        target = self.target_at(pt)
        if target is None:
            return

        actions: list[tuple[str, str, dict]] = [
            (
                "Create branch at '{}'".format(commit_hash),
                "gs_create_branch", {"start_point": commit_hash}
            ),
            (
                "Create tag at '{}'".format(commit_hash),
                "gs_create_tag", {"target_commit": commit_hash}
            )
        ]
        branch = self.branch_at(pt)
        if branch is not None:
            actions = [
                (
                    "Rename '{}'".format(branch),
                    "gs_rename_branch", {"branch": branch}
                ),
                (
                    "Delete '{}'".format(branch),
                    "gs_delete_branch", {"branch": branch, "undo_owner": view.id()}
                )
            ]
        elif not view.match_selector(pt, "constant.numeric.graph.commit-hash.git-savvy"):
            return
        else:
            from .log_graph_helper import format_revision_list

            selected_commits = list(dict.fromkeys(
                view.substr(sublime.Region(a, b))
                for a, b in sorted(view.settings().get("git_savvy.multi_selection", []), reverse=True)
            ))
            if len(selected_commits) >= 2 and commit_hash in selected_commits:
                actions = [
                    (
                        "Squash {}".format(format_revision_list(selected_commits)),
                        "gs_rebase_squash_commits",
                        {"base_commit": selected_commits[0], "commits": selected_commits[1:]}
                    ),
                    ("-", "", {})
                ] + actions

        view.sel().clear()
        view.sel().add(target)

        def on_done(index: int) -> None:
            if 0 <= index < len(actions):
                _, command, args = actions[index]
                if not command:
                    return
                if command == "gs_rebase_squash_commits":
                    view.run_command(command, args)
                else:
                    window.run_command(command, args)

        view.show_popup_menu([label for label, _, _ in actions], on_done, pt)

    def target_at(self, pt: int) -> sublime.Region | None:
        return next((
            region for region in self.view.find_by_selector(
                "constant.numeric.graph.commit-hash.git-savvy, "
                "meta.graph.branch.git-savvy constant.other.git.branch"
            )
            if region.begin() <= pt < region.end()
        ), None)

    def branch_at(self, pt: int) -> str | None:
        for region in self.view.find_by_selector(
            "meta.graph.branch.git-savvy constant.other.git.branch"
        ):
            if region.begin() <= pt < region.end():
                name = self.view.substr(region)
                return next((
                    branch.name for branch in self.get_branches()
                    if not branch.is_remote and branch.name == name
                ), None)
        return None


class GsLogGraphMouseListener(EventListener):
    def on_selection_modified(self, view: sublime.View) -> None:
        press = view.settings().get(PRESS_KEY)
        if press is None:
            return
        _, pt, a, b = press
        target = sublime.Region(a, b)
        if len(view.sel()) != 1 or not target.contains(view.sel()[0]):
            view.settings().erase(PRESS_KEY)
        elif view.sel()[0] != target:
            view.sel().clear()
            view.sel().add(target)
