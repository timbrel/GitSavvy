from __future__ import annotations
from itertools import starmap

from ..parse_diff import SplittedDiff

from typing import Iterable
from typing_extensions import TypeAlias

import sublime
from sublime_plugin import EventListener, TextCommand


__all__ = (
    "gs_log_graph_multiselect",
    "gs_diff_multiselect",
    "gs_dashboard_multiselect",
    "gs_clear_multiselect",
    "MultiSelectionContext",
)


# Should be `tuple[int, int]` but Sublime serializes tuples to a list
Region: TypeAlias = "list[int]"


def get_selection(view: sublime.View) -> Iterable[sublime.Region]:
    multi_selection: list[Region] = view.settings().get("git_savvy.multi_selection", [])
    return (
        sorted(starmap(sublime.Region, multi_selection))
        if multi_selection
        else view.sel()
    )


def get_multi_selection_if_multi(view: sublime.View) -> Iterable[sublime.Region]:
    multi_selection: list[Region] = view.settings().get("git_savvy.multi_selection", [])
    return (
        sorted(starmap(sublime.Region, multi_selection))
        if len(multi_selection) > 1
        else view.sel()
    )


class gs_log_graph_multiselect(TextCommand):
    def run(self, edit, event: dict | None = None) -> None:
        from .log_graph import COMMIT_LINE

        view = self.view
        prepare_mouse_selection(view, event)
        frozen_sel = list(view.sel())

        multi_selection: list[Region] = view.settings().get("git_savvy.multi_selection", [])

        regions = multi_selection[:]
        for s in frozen_sel:
            line_spans = view.lines(s)
            for line_span in line_spans:
                line_text = view.substr(line_span)
                match = COMMIT_LINE.search(line_text)
                if match:
                    # a, _ = match.span('dot')
                    b, c = match.span('commit_hash')
                    wanted = [line_span.a + b, line_span.a + c]
                    if wanted in multi_selection:
                        if wanted in regions:
                            regions.remove(wanted)
                    else:
                        regions.append(wanted)

        view.settings().set("git_savvy.multi_selection", regions)
        set_multiselect_markers(view, list(starmap(sublime.Region, regions)))

        view.sel().clear()
        view.sel().add_all([sublime.Region(frozen_sel[-1].b)])
        if (
            event is None
            and len(frozen_sel) == 1
            and frozen_sel[0].empty()
            and any(r not in multi_selection for r in regions)
        ):
            view.run_command("gs_log_graph_navigate", {"natural_movement": True})

    def want_event(self) -> bool:
        return True


class gs_diff_multiselect(TextCommand):
    def run(self, edit, event: dict | None = None) -> None:
        view = self.view
        if event is not None and not any(not s.empty() for s in view.sel()):
            pt = view.window_to_text((event["x"], event["y"]))
            view.sel().clear()
            view.sel().add(sublime.Region(pt))
        frozen_sel = list(view.sel())

        multi_selection: list[Region] = view.settings().get("git_savvy.multi_selection", [])
        regions = multi_selection[:]
        diff: SplittedDiff | None = None
        for s in frozen_sel:
            # `hunk_selected`/`chunk_selected` track if the last `s` selects
            # a full hunk/chunk which determines how we jump, see below
            hunk_selected = False
            chunk_selected = False

            if s.empty() and view.match_selector(s.a, "meta.diff.range.unified"):
                if diff is None:
                    diff = SplittedDiff.from_view(view)
                if hunk := diff.hunk_for_pt(s.a):
                    s = hunk.region()
                    hunk_selected = True

            elif not s.empty():
                if diff is None:
                    diff = SplittedDiff.from_view(view)
                if is_exactly_selected_chunk(diff, s):
                    chunk_selected = True

            line_spans = view.lines(s)
            for line_span in line_spans:
                if view.match_selector(line_span.a, "markup.inserted.diff | markup.deleted.diff"):
                    wanted = [line_span.a, line_span.b]
                    if wanted in multi_selection:
                        if wanted in regions:
                            regions.remove(wanted)
                    else:
                        regions.append(wanted)

        view.settings().set("git_savvy.multi_selection", regions)
        set_multiselect_markers(view, list(starmap(sublime.Region, regions)))

        view.sel().clear()
        view.sel().add_all([sublime.Region(frozen_sel[-1].b if event is not None else s.b)])
        if event is not None:
            return

        if hunk_selected:
            # if a hunk is toggled out, don't move but stay on the starting pos
            if all(r in multi_selection for r in regions):
                view.sel().clear()
                view.sel().add_all([sublime.Region(s.a)])
            # if a hunk is toggled on, likely `s.b` is on the next hunk.
            # only move if that's not the case
            elif not view.match_selector(s.b, "meta.diff.range.unified"):
                view.run_command("gs_diff_navigate")

        elif chunk_selected and len(frozen_sel) == 1:
            if all(r in multi_selection for r in regions):
                view.sel().clear()
                view.sel().add_all(frozen_sel)
            else:
                view.run_command("gs_diff_navigate", {"in_chunks": True})

        elif (
            len(frozen_sel) == 1
            and frozen_sel[0].empty()
            # don't move if the selection is toggled out
            and any(r not in multi_selection for r in regions)
        ):
            view.run_command("move", {"by": "lines", "forward": True})

    def want_event(self) -> bool:
        return True


class gs_dashboard_multiselect(TextCommand):
    def run(self, edit, event: dict | None = None) -> None:
        view = self.view
        prepare_mouse_selection(view, event)
        frozen_sel = list(view.sel())

        multi_selection: list[Region] = view.settings().get("git_savvy.multi_selection", [])

        entities = view.find_by_selector(
            "meta.git-savvy.entity, "
            "meta.git-savvy.branches.branch.name, "
            "meta.git-savvy.tag.name, "
            "constant.other.git-savvy.sha1"
        )
        regions = multi_selection[:]
        for s in frozen_sel:
            line_spans = view.lines(s)
            for line_span in line_spans:
                for r in entities:
                    if r in line_span:
                        wanted = [r.a, r.b]
                        if wanted in multi_selection:
                            if wanted in regions:
                                regions.remove(wanted)
                        else:
                            regions.append(wanted)

        view.settings().set("git_savvy.multi_selection", regions)
        set_multiselect_markers(view, list(starmap(sublime.Region, regions)))

        view.sel().clear()
        view.sel().add_all([sublime.Region(frozen_sel[-1].b)])
        if (
            event is None
            and len(frozen_sel) == 1
            and frozen_sel[0].empty()
            and any(r not in multi_selection for r in regions)
        ):
            navigation_command = {
                "tags": "gs_tags_navigate_tag",
                "branch": "gs_branches_navigate_branch",
                "status": "gs_status_navigate_file",
            }.get(view.settings().get("git_savvy.interface"))
            if navigation_command:
                view.run_command(navigation_command)

    def want_event(self) -> bool:
        return True


class gs_clear_multiselect(TextCommand):
    def run(self, edit) -> None:
        clear_multiselect(self.view)


def reset_multiselections() -> None:
    for window in sublime.windows():
        views = list(window.views())
        for panel in window.panels():
            if panel.startswith("output."):
                view = window.find_output_panel(panel[len("output."):])
                if view is not None:
                    views.append(view)
        for view in views:
            clear_multiselect(view)


def clear_multiselect(view: sublime.View) -> None:
    view.settings().erase("git_savvy.mouse_selected_target")
    view.settings().set("git_savvy.multi_selection", [])
    set_multiselect_markers(view, [])


class MultiSelectionContext(EventListener):
    def on_query_context(
        self, view: sublime.View, key: str, operator: int, operand: object, match_all: bool
    ) -> bool | None:
        if key != "git_savvy.has_multi_selection":
            return None
        has_selection = bool(view.settings().get("git_savvy.multi_selection", []))
        if operator == sublime.OP_EQUAL:
            return has_selection == operand
        if operator == sublime.OP_NOT_EQUAL:
            return has_selection != operand
        return None


def prepare_mouse_selection(view: sublime.View, event: dict | None) -> None:
    mouse_target = view.settings().get("git_savvy.mouse_selected_target")
    automatic_selection = (
        mouse_target is not None
        and list(view.sel()) == [sublime.Region(*mouse_target)]
    )
    if event is None:
        if automatic_selection:
            # A clicked label acts like a caret for Space's toggle-and-advance.
            pt = view.sel()[0].b
            view.sel().clear()
            view.sel().add(sublime.Region(pt))
    elif automatic_selection or not any(not s.empty() for s in view.sel()):
        pt = view.window_to_text((event["x"], event["y"]))
        view.sel().clear()
        view.sel().add(sublime.Region(pt))
    view.settings().erase("git_savvy.mouse_selected_target")


def is_exactly_selected_chunk(diff: SplittedDiff, s: sublime.Region) -> bool:
    if s.empty():
        return False

    begin = s.begin()
    end = s.end()

    hunk = diff.hunk_for_pt(begin)
    if not hunk:
        return False

    for chunk in hunk.content().chunks():
        region = chunk.region()
        if begin == region.begin() and end == region.end():
            return True

    return False


MULTISELECT_SCOPE = 'git_savvy.multiselect'
MULTISELECT_SCOPES = {
    "commit": "git_savvy.commit.multiselect",
    "dashboard": "git_savvy.dashboard.multiselect",
    "diff": "git_savvy.diff.multiselect",
    "graph": "git_savvy.graph.multiselect",
}
DEFAULT_STYLE = {"flags": "fill"}
BASE_FLAGS = sublime.DRAW_EMPTY | sublime.PERSISTENT | sublime.RegionFlags.NO_UNDO
STYLES = {
    "fill": BASE_FLAGS,
    "outline": BASE_FLAGS | sublime.DRAW_NO_FILL,
    "hidden": sublime.HIDDEN | sublime.PERSISTENT | sublime.RegionFlags.NO_UNDO,
}
REGION_KEY = "git_savvy.multiselect"


def set_multiselect_markers(
    view: sublime.View,
    regions: list[sublime.Region],
    styles: dict | None = None
) -> None:
    if regions:
        styles = {**DEFAULT_STYLE, **(styles or {})}
        styles.setdefault("scope", multiselect_scope(view))

        # Combine adjacent `regions` to `regions_`
        regions_: list[sublime.Region] = []
        for r in sorted(regions):
            if not regions_:
                regions_.append(r)
            elif (r.a - regions_[-1].b) in (0, 1):
                regions_[-1].b = r.b
            else:
                regions_.append(r)

        if isinstance(styles["flags"], str):
            try:
                styles["flags"] = STYLES[styles["flags"]]
            except LookupError:
                styles["flags"] = STYLES["hidden"]

        view.add_regions(REGION_KEY, regions_, **styles)
        regions_count = len(regions_)
        s = "" if regions_count == 1 else "s"
        view.set_status("gs_multiselect_info", f"{regions_count} saved selection{s}")
    else:
        view.erase_regions(REGION_KEY)
        view.erase_status("gs_multiselect_info")


def multiselect_scope(view: sublime.View) -> str:
    settings = view.settings()
    if settings.get("git_savvy.log_graph_view"):
        return MULTISELECT_SCOPES["graph"]
    if settings.get("git_savvy.commit_view"):
        return MULTISELECT_SCOPES["commit"]
    if settings.get("git_savvy.diff_view"):
        return MULTISELECT_SCOPES["diff"]
    if settings.get("git_savvy.interface"):
        return MULTISELECT_SCOPES["dashboard"]
    return MULTISELECT_SCOPE
