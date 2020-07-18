import sublime
import sublime_plugin

from . import log_graph
from ..parse_diff import TextRange
from ..runtime import throttled
from ..utils import flash


__all__ = (
    "CopyIntercepterForGraph",
)


MYPY = False
if MYPY:
    from typing import Dict, List, Optional, Union


HIGHLIGHT_REGION_KEY = "GS.flashs.{}"
DURATION = 0.4
STYLE = {"scope": "git_savvy.graph.dot", "flags": 0}


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

        cursor = frozen_sel[0].a
        line_span = view.line(cursor)

        commit_hash = read_commit_hash(view, line_span)
        if not commit_hash:
            return None

        clip_content = sublime.get_clipboard(128)
        if not clip_content:
            set_clipboard_and_flash(view, commit_hash.text, [commit_hash.region()])
            return "noop"

        commit_msg = read_commit_message(view, line_span)
        if not commit_msg:
            return None

        if clip_content == commit_hash.text:
            set_clipboard_and_flash(view, commit_msg.text, [commit_msg.region()])
        elif clip_content == commit_msg.text:
            set_clipboard_and_flash(
                view,
                "{} ({})".format(commit_hash.text, commit_msg.text),
                [commit_hash.region(), commit_msg.region()]
            )
        else:
            set_clipboard_and_flash(view, commit_hash.text, [commit_hash.region()])
        return "noop"


def set_clipboard_and_flash(view, text, regions):
    # type: (sublime.View, str, List[sublime.Region]) -> None
    sublime.set_clipboard(text)
    flash_copied_regions(view, regions)
    flash(view, "Copied '{}' to the clipboard".format(text))


def read_commit_hash(view, line_span):
    # type: (sublime.View, sublime.Region) -> Optional[TextRange]
    commit_region = log_graph.extract_comit_hash_span(view, line_span)
    if not commit_region:
        return None

    return TextRange(view.substr(commit_region), commit_region.a, commit_region.b)


def read_commit_message(view, line_span):
    # type: (sublime.View, sublime.Region) -> Optional[TextRange]
    for r in view.find_by_selector("meta.graph.message.git-savvy"):
        if line_span.contains(r):
            return TextRange(view.substr(r), r.a, r.b)
    else:
        return None


def flash_copied_regions(view, regions):
    # type: (sublime.View, List[sublime.Region]) -> None
    region_key = HIGHLIGHT_REGION_KEY.format("flash_copied_regions")
    view.add_regions(region_key, regions, **STYLE)  # type: ignore[arg-type]

    sublime.set_timeout(
        throttled(erase_regions, view, region_key),
        int(DURATION * 1000)
    )


def erase_regions(view, region_key):
    # type: (sublime.View, str) -> None
    view.erase_regions(region_key)
