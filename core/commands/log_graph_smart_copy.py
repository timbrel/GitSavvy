import sublime
import sublime_plugin

from . import log_graph
from ..fns import pairwise, peek
from ..text_helper import Region, TextRange, line_from_pt
from ..utils import flash, flash_regions
from ..view import find_by_selector


__all__ = (
    "CopyIntercepterForGraph",
    "gs_log_graph_smart_paste",
)


from typing import Dict, Iterator, List, Optional, Tuple, Union


class CopyIntercepterForGraph(sublime_plugin.EventListener):
    def on_text_command(self, view, command_name, args):
        # type: (sublime.View, str, Dict) -> Union[None, str]
        if command_name != "copy":
            return None

        if not view.settings().get("git_savvy.log_graph_view"):
            return None

        frozen_sel = [r for r in view.sel()]
        if len(frozen_sel) != 1:
            return None

        if not frozen_sel[0].empty():
            return None

        def candidates():
            # type: () -> Iterator[Tuple[str, List[Region], str]]
            cursor = frozen_sel[0].a
            line = line_from_pt(view, cursor)
            line_span = line.region()

            commit_hash = read_commit_hash(view, line)
            if commit_hash:
                yield commit_hash.text, [commit_hash.region()], "commit"

            for d in read_tags_in_region(view, line_span):
                yield d.text, [d.region()], "tag"

            for d in read_branches_in_region(view, line_span):
                yield d.text, [d.region()], "branch"

            commit_msg = read_commit_message(view, line_span)
            if commit_msg:
                yield commit_msg.text, [commit_msg.region()], "message"
            if commit_hash and commit_msg:
                yield (
                    "{} ({})".format(commit_hash.text, commit_msg.text),
                    [commit_hash.region(), commit_msg.region()],
                    "combo",
                )

        clip_content = sublime.get_clipboard(128)
        try:
            first, candidates_ = peek(candidates())
        except StopIteration:
            return None

        if not clip_content:
            set_clipboard_and_flash(view, *first)
            return "noop"

        for left, right in pairwise(candidates_):
            if left[0] == clip_content:
                set_clipboard_and_flash(view, *right)
                return "noop"
        else:
            set_clipboard_and_flash(view, *first)
            return "noop"


def set_clipboard_and_flash(view, text, regions, kind):
    # type: (sublime.View, str, List[Region], str) -> None
    sublime.set_clipboard(text)
    flash_regions(view, regions)
    ext = ". Paste elsewhere to recreate the branch." if kind == "branch" else ""
    flash(view, f"Copied '{text}' to the clipboard{ext}")
    view.settings().set("git_savvy.log_graph_view.clipboard", [text, kind])


class gs_log_graph_smart_paste(sublime_plugin.TextCommand):
    def run(self, edit):
        view = self.view
        window = view.window()
        if not window:
            return

        frozen_sel = [r for r in view.sel()]
        if len(frozen_sel) != 1:
            return

        if not frozen_sel[0].empty():
            return

        clip = view.settings().get("git_savvy.log_graph_view.clipboard")
        if not isinstance(clip, list):
            return

        if len(clip) != 2:
            return

        ref, kind = clip
        if not ref or kind not in ("branch", "tag"):
            return

        cursor = frozen_sel[0].a
        line = line_from_pt(view, cursor)
        commit_hash = read_commit_hash(view, line)
        if not commit_hash:
            return

        if kind == "branch":
            window.run_command("gs_create_branch", {
                "start_point": commit_hash.text,
                "branch_name": ref,
            })
        elif kind == "tag":
            window.run_command("gs_tag_create", {
                "target_commit": commit_hash.text,
                "tag_name": ref,
            })


def read_commit_hash(view, line):
    # type: (sublime.View, TextRange) -> Optional[TextRange]
    commit_region = log_graph.extract_comit_hash_span(view, line)
    if not commit_region:
        return None

    return TextRange(view.substr(commit_region), commit_region.a, commit_region.b)


def read_tags_in_region(view, line_span):
    # type: (sublime.View, sublime.Region) -> Iterator[TextRange]
    yield from read_commit_decoration(view, line_span, "entity.name.tag.branch-name")


def read_branches_in_region(view, line_span):
    # type: (sublime.View, sublime.Region) -> Iterator[TextRange]
    yield from read_commit_decoration(view, line_span, "constant.other.git.branch")


def read_commit_decoration(view, line_span, selector):
    # type: (sublime.View, sublime.Region, str) -> Iterator[TextRange]
    for r in find_by_selector(view, selector):
        if r.a > line_span.b:
            break
        if line_span.contains(r):
            yield TextRange(view.substr(r), r.a, r.b)


def read_commit_message(view, line_span):
    # type: (sublime.View, sublime.Region) -> Optional[TextRange]
    for r in find_by_selector(view, "meta.graph.message.git-savvy"):
        if line_span.contains(r):
            return TextRange(view.substr(r), r.a, r.b)
    else:
        return None
