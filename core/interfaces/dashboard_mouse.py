from __future__ import annotations

import sublime
from sublime_plugin import EventListener, TextCommand


__all__ = ("gs_dashboard_mouse", "GsDashboardMouseListener")

TARGET_KEY = "git_savvy.dashboard_mouse_target"


class gs_dashboard_mouse(TextCommand):
    def run(self, edit: sublime.Edit, event: dict, release: bool = False) -> None:
        view = self.view
        view.settings().erase(TARGET_KEY)
        if release:
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
        coordinates = view.settings().get(TARGET_KEY)
        if coordinates is None:
            return
        target = sublime.Region(*coordinates)
        if len(view.sel()) != 1:
            view.settings().erase(TARGET_KEY)
            return
        selection = view.sel()[0]
        expanded = target.cover(selection)
        if selection.a > selection.b:
            expanded = sublime.Region(expanded.end(), expanded.begin())
        if selection != expanded:
            view.sel().clear()
            view.sel().add(expanded)
