import sublime
import sublime_plugin

from . import log_graph
from ..fns import pairwise, peek
from ..parse_diff import Region, TextRange
from ..utils import flash, flash_regions
from ..view import find_by_selector


__all__ = (
    "CopyIntercepterForGraph",
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
            # type: () -> Iterator[Tuple[str, List[Region]]]
            cursor = frozen_sel[0].a
            line = log_graph.line_from_pt(view, cursor)
            line_span = line.region()

            commit_hash = read_commit_hash(view, line)
            if commit_hash:
                yield commit_hash.text, [commit_hash.region()]
            for d in read_commit_decoration(view, line_span):
                yield d.text, [d.region()]
            commit_msg = read_commit_message(view, line_span)
            if commit_msg:
                yield commit_msg.text, [commit_msg.region()]
            if commit_hash and commit_msg:
                yield (
                    "{} ({})".format(commit_hash.text, commit_msg.text),
                    [commit_hash.region(), commit_msg.region()]
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


def set_clipboard_and_flash(view, text, regions):
    # type: (sublime.View, str, List[Region]) -> None
    sublime.set_clipboard(text)
    flash_regions(view, regions)
    flash(view, "Copied '{}' to the clipboard".format(text))


def read_commit_hash(view, line):
    # type: (sublime.View, TextRange) -> Optional[TextRange]
    commit_region = log_graph.extract_comit_hash_span(view, line)
    if not commit_region:
        return None

    return TextRange(view.substr(commit_region), commit_region.a, commit_region.b)


def read_commit_decoration(view, line_span):
    # type: (sublime.View, sublime.Region) -> Iterator[TextRange]
    for r in find_by_selector(view, "constant.other.git.branch, entity.name.tag.branch-name"):
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
