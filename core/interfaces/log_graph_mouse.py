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
        from .log_graph import COMMIT_LINE, colorize_dots

        view = self.view
        settings = view.settings()
        settings.erase(PRESS_KEY)
        if release:
            return

        pt = view.window_to_text((event["x"], event["y"]))
        view.sel().clear()
        view.sel().add(sublime.Region(pt))
        colorize_dots(view)
        match = COMMIT_LINE.search(view.substr(view.line(pt)))
        if match:
            token = monotonic()
            settings.set(PRESS_KEY, [token, pt])
            commit_hash = match.group("commit_hash")
            change_count = view.change_count()
            sublime.set_timeout(
                lambda: self.show_menu(token, pt, commit_hash, change_count), HOLD_MS
            )
        view.run_command("drag_select", {"event": event})

    def want_event(self) -> bool:
        return True

    def show_menu(self, token: float, pt: int, commit_hash: str, change_count: int) -> None:
        view = self.view
        if not view.is_valid() or view.settings().get(PRESS_KEY) != [token, pt]:
            return
        view.settings().erase(PRESS_KEY)
        window = view.window()
        if (
            window is None
            or window.active_view() != view
            or not view.settings().get("git_savvy.log_graph_view")
            or view.change_count() != change_count
            or len(view.sel()) != 1
            or view.sel()[0] != sublime.Region(pt)
        ):
            return

        actions = [
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

        def on_done(index: int) -> None:
            if 0 <= index < len(actions):
                _, command, args = actions[index]
                window.run_command(command, args)

        view.show_popup_menu([label for label, _, _ in actions], on_done, pt)

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
        _, pt = press
        if len(view.sel()) != 1 or view.sel()[0] != sublime.Region(pt):
            view.settings().erase(PRESS_KEY)
