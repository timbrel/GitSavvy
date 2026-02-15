from __future__ import annotations

from itertools import starmap

import sublime
import sublime_plugin

from . import log_graph
from . import multi_selector
from ..fns import pairwise, peek, take
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

        sels = list(multi_selector.get_multi_selection_if_multi(view))
        if len(sels) == 1:
            return self.single_item(view, sels[0])
        if len(sels) > 1:
            return self.multiple_items(view, sels)

        return None

    def single_item(self, view: sublime.View, sel: sublime.Region) -> str | None:
        if not sel.empty():
            return None

        def candidates():
            # type: () -> Iterator[Tuple[str, List[Region], str]]
            cursor = sel.a
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

    def multiple_items(self, view: sublime.View, selections: list[sublime.Region]) -> str | None:

        def candidates():
            # type: () -> Iterator[Tuple[str, List[Region], str]]
            lines = [line_from_pt(view, sel.a) for sel in selections]
            commit_hashes = [read_commit_hash(view, line) for line in lines]
            if any(commit_hash is None for commit_hash in commit_hashes):
                return

            commit_texts = [commit_hash.text for commit_hash in commit_hashes if commit_hash]
            commit_regions = [commit_hash.region() for commit_hash in commit_hashes if commit_hash]
            if commit_texts:
                yield ", ".join(commit_texts), commit_regions, "commit"

            combo_texts: list[str] = []
            combo_regions: list[Region] = []

            for line, commit_hash in zip(lines, commit_hashes):
                if not commit_hash:
                    return None

                commit_msg = read_commit_message(view, line.region())
                if commit_msg:
                    combo_texts.append(f"{commit_hash.text} ({commit_msg.text})")
                    combo_regions.extend([commit_hash.region(), commit_msg.region()])
                else:
                    return None

            if combo_texts:
                yield "\n".join(combo_texts), combo_regions, "combo"

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
    view.run_command("gs_clear_multiselect")
    flash_regions(view, regions)
    if kind == "branch":
        ext = ". Paste elsewhere to recreate the branch."
    elif kind == "tag":
        ext = ". Paste on a commit to recreate the tag."
    elif kind == "commit":
        s = "s" if "," in text else ""
        ext = f". Paste on a commit to insert the commit{s} there."
    else:
        ext = ""
    flash(view, f"Copied '{text}' to the clipboard{ext}")
    view.settings().set(
        "git_savvy.log_graph_view.clipboard",
        [text, [(r.a, r.b) for r in regions], kind]
    )


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

        if len(clip) != 3:
            return

        ref, regions, kind = clip
        if not ref or kind not in ("branch", "tag", "commit"):
            return

        cursor = frozen_sel[0].a
        line = line_from_pt(view, cursor)
        commit_hash = read_commit_hash(view, line)
        if not commit_hash:
            return

        if kind == "commit":
            commit_hashes = ref.split(", ")
            if commit_hashes:
                insert_at = commit_hash.text
                candidate_region_and_hashes = (
                    list(zip(starmap(sublime.Region, regions), commit_hashes))
                    + [(commit_hash.region(), insert_at)]
                )
                reachable_candidates = filter_to_commits_reachable_from_head(
                    view,
                    candidate_region_and_hashes
                )
                if not reachable_candidates:
                    flash(view, "This commit is not reachable from HEAD.")
                    return

                reachable_hashes = {commit_hash for _, commit_hash in reachable_candidates}
                if insert_at not in reachable_hashes:
                    flash(view, "This commit is not reachable from HEAD.")
                    return

                _, base_commit = max(reachable_candidates)
                view.run_command("gs_rebase_insert_commits", {
                    "base_commit": base_commit,
                    "insert_at": insert_at,
                    "commits": list(reversed(commit_hashes)),
                })
        elif kind == "branch":
            window.run_command("gs_create_branch", {
                "start_point": commit_hash.text,
                "branch_name": ref,
                "force": True
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


def filter_to_commits_reachable_from_head(
    view: sublime.View,
    region_and_hashes: list[tuple[sublime.Region, str]],
) -> list[tuple[sublime.Region, str]]:
    remaining = {commit_hash for _, commit_hash in region_and_hashes}
    if not remaining:
        return []

    reachable_candidate_hashes: set[str] = set()
    for commit_hash in take(100, reachable_commits_from_head(view)):
        if commit_hash not in remaining:
            continue

        reachable_candidate_hashes.add(commit_hash)
        remaining.remove(commit_hash)
        if not remaining:
            break

    return [
        (region, commit_hash)
        for region, commit_hash in region_and_hashes
        if commit_hash in reachable_candidate_hashes
    ]


def reachable_commits_from_head(view: sublime.View) -> Iterator[str]:
    head_dot = read_head_dot(view)
    if not head_dot:
        return

    head_commit_hash = read_commit_hash_from_dot(view, head_dot)
    if head_commit_hash:
        yield head_commit_hash

    for dot in log_graph.follow_dots(head_dot, forward=True):
        commit_hash = read_commit_hash_from_dot(view, dot)
        if commit_hash:
            yield commit_hash


def read_commit_hash_from_dot(view: sublime.View, dot) -> str | None:
    line = line_from_pt(view, dot.pt)
    commit_hash = read_commit_hash(view, line)
    if commit_hash:
        return commit_hash.text
    return None


def read_head_dot(view: sublime.View):
    selector = (
        "git-savvy.graph meta.graph.graph-line.head.git-savvy "
        "constant.numeric.graph.commit-hash.git-savvy"
    )
    for head_hash in find_by_selector(view, selector):
        return log_graph.dot_from_line(view, line_from_pt(view, head_hash.a))
    return None
