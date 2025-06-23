from __future__ import annotations
from itertools import starmap

from . import log_graph
from ..parse_diff import SplittedDiff

from typing import Iterable
from typing_extensions import TypeAlias

import sublime
from sublime_plugin import TextCommand


__all__ = (
    "gs_log_graph_multiselect",
    "gs_diff_multiselect",
    "gs_dashboard_multiselect",
    "gs_clear_multiselect",
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


class gs_log_graph_multiselect(TextCommand):
    def run(self, edit) -> None:
        view = self.view
        frozen_sel = list(view.sel())

        multi_selection: list[Region] = view.settings().get("git_savvy.multi_selection", [])

        regions = multi_selection[:]
        for s in frozen_sel:
            line_spans = view.lines(s)
            for line_span in line_spans:
                line_text = view.substr(line_span)
                match = log_graph.COMMIT_LINE.search(line_text)
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
            len(frozen_sel) == 1
            and frozen_sel[0].empty()
            and any(r not in multi_selection for r in regions)
        ):
            view.run_command("gs_log_graph_navigate", {"natural_movement": True})


class gs_diff_multiselect(TextCommand):
    def run(self, edit) -> None:
        view = self.view
        frozen_sel = list(view.sel())

        multi_selection: list[Region] = view.settings().get("git_savvy.multi_selection", [])
        regions = multi_selection[:]
        for s in frozen_sel:
            # `hunk_selected` tracks if the last `s` selects a hunk
            # which determines how we jump, see below
            hunk_selected = False
            if s.empty() and view.match_selector(s.a, "meta.diff.range.unified"):
                diff = SplittedDiff.from_view(view)
                if hunk := diff.hunk_for_pt(s.a):
                    s = hunk.region()
                    hunk_selected = True
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
        view.sel().add_all([sublime.Region(s.b)])
        if hunk_selected:
            # if a hunk is toggled out, don't move but stay on the starting pos
            if all(r in multi_selection for r in regions):
                view.sel().clear()
                view.sel().add_all([sublime.Region(s.a)])
            # if a hunk is toggled on, likely `s.b` is on the next hunk.
            # only move if that's not the case
            elif not view.match_selector(s.b, "meta.diff.range.unified"):
                view.run_command("gs_diff_navigate")

        elif (
            len(frozen_sel) == 1
            and frozen_sel[0].empty()
            # don't move if the selection is toggled out
            and any(r not in multi_selection for r in regions)
        ):
            view.run_command("move", {"by": "lines", "forward": True})


class gs_dashboard_multiselect(TextCommand):
    def run(self, edit) -> None:
        view = self.view
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
            len(frozen_sel) == 1
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


class gs_clear_multiselect(TextCommand):
    def run(self, edit) -> None:
        view = self.view
        view.settings().set("git_savvy.multi_selection", [])
        set_multiselect_markers(view, [])


MULTISELECT_SCOPE = 'git_savvy.multiselect'
DEFAULT_STYLE = {"scope": MULTISELECT_SCOPE, "flags": "fill"}
BASE_FLAGS = sublime.DRAW_EMPTY | sublime.PERSISTENT | sublime.RegionFlags.NO_UNDO
STYLES = {
    "fill": BASE_FLAGS,
    "outline": BASE_FLAGS | sublime.DRAW_NO_FILL,
    "hidden": sublime.HIDDEN | sublime.PERSISTENT | sublime.RegionFlags.NO_UNDO,
}
REGION_KEY = "git_savvy.multiselect"


def set_multiselect_markers(view, regions: list[sublime.Region], styles=DEFAULT_STYLE):
    if regions:
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
