from __future__ import annotations

import sublime
from sublime_plugin import EventListener, TextCommand

from .multi_selector import normalize_mouse_selection


__all__ = ("gs_dashboard_mouse", "GsDashboardMouseListener")

TARGET_KEY = "git_savvy.dashboard_mouse_target"


class gs_dashboard_mouse(TextCommand):
    def run(
        self, edit: sublime.Edit, event: dict, release: bool = False,
        text_selection: str | None = None
    ) -> None:
        view = self.view
        view.settings().erase(TARGET_KEY)
        if release:
            return

        window = view.window()
        if window is not None:
            window.focus_view(view)

        if text_selection is not None:
            view.settings().erase("git_savvy.mouse_selected_target")
            selection_args: dict = {"event": event}
            if text_selection != "characters":
                selection_args["by"] = text_selection
            view.run_command("drag_select", selection_args)
            return

        pt = view.window_to_text((event["x"], event["y"]))
        if view.settings().get("git_savvy.branch_view"):
            selector = (
                "meta.git-savvy.branches.branch.name, "
                "constant.other.git-savvy.branches.branch.sha1"
            )
        elif view.settings().get("git_savvy.tags_view"):
            selector = "meta.git-savvy.tag.name, constant.other.git-savvy.tags.sha1"
        else:
            selector = "meta.git-savvy.entity.filename meta.git-savvy.status.subject"
        target = next((
            region for region in view.find_by_selector(selector)
            if region.begin() <= pt < region.end()
        ), None)
        view.sel().clear()
        view.sel().add(target if target is not None else sublime.Region(pt))
        view.settings().set("git_savvy.mouse_selected_target", [target.a, target.b] if target else None)
        args: dict = {"event": event}
        if target is not None:
            view.settings().set(TARGET_KEY, [target.a, target.b])
            args["by"] = "words"
        # Let this TextCommand finish so Sublime draws the selection before
        # native drag tracking takes over until mouse release.
        sublime.set_timeout(lambda: view.run_command("drag_select", args), 0)

    def want_event(self) -> bool:
        return True


class GsDashboardMouseListener(EventListener):
    def on_selection_modified(self, view: sublime.View) -> None:
        settings = view.settings()
        if settings.get(TARGET_KEY) is not None or any(
            settings.get(key) for key in (
                "git_savvy.status_view", "git_savvy.branch_view", "git_savvy.tags_view"
            )
        ):
            normalize_mouse_selection(view, TARGET_KEY)
