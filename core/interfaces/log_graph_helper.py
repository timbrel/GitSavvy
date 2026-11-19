from __future__ import annotations

import re
from typing import Dict, Iterable, Iterator, List, Literal, NamedTuple, Optional, Sequence, TypedDict

import sublime

from ..fns import filter_, take
from ..git_mixins.branches import Branch
from ..text_helper import TextRange, line_from_pt
from ..types import ShortHash
from ..view import find_by_selector
from .log_graph_walk import COMMIT_NODE_CHARS, Char, dot_from_line, follow_dots


GRAPH_CHAR_OPTIONS = r" /_\|\-\\."
DEFAULT_NODE_CHAR = "●"
ROOT_NODE_CHAR = "⌂"
WORKTREE_MARKER = "■"
GRAPH_HEIGHT = 5000
COMMIT_LINE = re.compile(
    r"^[{graph_chars}]*(?P<dot>[{node_chars}])[{graph_chars}]* "
    r"(?P<commit_hash>[a-f0-9]{{5,64}}) +"
    r"(\((?P<decoration>.+?)\))?"
    .format(graph_chars=GRAPH_CHAR_OPTIONS, node_chars=COMMIT_NODE_CHARS)
)
FIND_COMMIT_HASH = "^[{graph_chars}]*[{node_chars}][{graph_chars}]* ".format(
    graph_chars=GRAPH_CHAR_OPTIONS, node_chars=COMMIT_NODE_CHARS
)


class Commit(NamedTuple):
    commit_hash: str
    commit_message: str


class LineInfo(TypedDict, total=False):
    commit: ShortHash
    HEAD: str
    branches: List[str]
    local_branches: List[str]
    tags: List[str]


ListItems = Literal["branches", "local_branches", "tags"]


def describe_graph_line(line: str, known_branches: Dict[str, Branch]) -> Optional[LineInfo]:
    match = COMMIT_LINE.match(line)
    if match is None:
        return None

    commit_hash = ShortHash(match.group("commit_hash"))
    decoration = match.group("decoration")

    rv: LineInfo = {"commit": commit_hash}
    if decoration:
        # Worktree markers are display-only, never revision names.
        suffix = f" {WORKTREE_MARKER}"
        names = [
            name[:-len(suffix)] if name.endswith(suffix) else name
            for name in decoration.split(", ")
            if name != WORKTREE_MARKER
        ]
        if names and names[0].startswith("HEAD"):
            head, *names = names
            if head == "HEAD" or head == "HEAD*":
                rv["HEAD"] = commit_hash
            else:
                branch = head[head.index("-> ") + 3:]
                rv["HEAD"] = branch
                names = [branch] + names
        branches, local_branches, tags = [], [], []
        for name in names:
            if name.startswith("tag: "):
                tags.append(name[len("tag: "):])
            else:
                branches.append(name)
                known_branch = known_branches.get(name)
                if known_branch and known_branch.is_local:
                    local_branches.append(name)
        if branches:
            rv["branches"] = branches
        if local_branches:
            rv["local_branches"] = local_branches
        if tags:
            rv["tags"] = tags

    return rv


def describe_head(view: sublime.View, branches: Dict[str, Branch]) -> Optional[LineInfo]:
    try:
        region = find_by_selector(
            view,
            'meta.graph.graph-line.head.git-savvy '
            'constant.numeric.graph.commit-hash.git-savvy'
        )[0]
    except IndexError:
        return None

    cursor = region.b
    line_span = view.line(cursor)
    line_text = view.substr(line_span)
    return describe_graph_line(line_text, branches)


def format_revision_list(revisions: Sequence[str]) -> str:
    return (
        "{}".format(*revisions)
        if len(revisions) == 1
        else "{} and {}".format(*revisions)
        if len(revisions) == 2
        else "{}, {}, and {}".format(revisions[0], revisions[1], revisions[-1])
        if len(revisions) == 3
        else "{}, {} ... {}".format(revisions[0], revisions[1], revisions[-1])
    )


def headline_cursorline(view: sublime.View) -> tuple[int, int]:
    try:
        head_region = find_by_selector(
            view,
            'meta.graph.graph-line.head.git-savvy '
            'constant.numeric.graph.commit-hash.git-savvy'
        )[0]
    except IndexError:
        head_line = 0
    else:
        head_line, _ = view.rowcol(head_region.b)

    try:
        cursor_line, _ = view.rowcol(view.sel()[0].b)
    except IndexError:
        cursor_line = 0

    return head_line, cursor_line


def extract_commit_hash(line: str) -> str:
    match = COMMIT_LINE.search(line)
    return match.group('commit_hash') if match else ""


def extract_commit_hash_span(line: TextRange) -> sublime.Region | None:
    match = COMMIT_LINE.search(line.text)
    if match:
        a, b = match.span('commit_hash')
        return sublime.Region(a + line.a, b + line.a)
    return None


def commit_message_from_line(view: sublime.View, line: TextRange) -> str | None:
    line_span = line.region()
    for r in extract_message_regions(view):
        if line_span.contains(r):
            return line.text[(r.a - line_span.a):(r.b - line_span.a)]
    else:
        return None


def commit_hash_from_dot(view: sublime.View, dot: Char) -> str:
    line = line_from_pt(view, dot.pt)
    return extract_commit_hash(line.text)


def find_base_commit_for_fixup(view: sublime.View, commit_line: TextRange, commit_message: str) -> str | None:
    dot = dot_from_line(view, commit_line)
    if not dot:
        return None

    original_message = strip_fixup_or_squash_prefix(commit_message)
    target_dot = next(find_matching_commit(dot, original_message), None)
    if not target_dot:
        return None

    return commit_hash_from_dot(view, target_dot)


def is_fixup(commit: Commit) -> bool:
    return commit.commit_message.startswith("fixup")


def extract_message_regions(view: sublime.View) -> list[sublime.Region]:
    return find_by_selector(view, "meta.graph.message.git-savvy")


def is_fixup_or_squash_message(commit_message: str) -> bool:
    return (
        commit_message.startswith("fixup! ")
        or commit_message.startswith("squash! ")
    )


def strip_fixup_or_squash_prefix(commit_message: str) -> str:
    # As long as we process "visually", we must deal with
    # truncated messages which end with one or multiple dots
    # we have to strip.
    if commit_message.startswith('fixup! '):
        return commit_message[7:].rstrip('.').strip()
    if commit_message.startswith('squash! '):
        return commit_message[8:].rstrip('.').strip()
    return commit_message


def add_fixup_or_squash_prefixes(commit_message: str) -> list[str]:
    return [
        "fixup! " + commit_message,
        "squash! " + commit_message
    ]


def commit_message_from_point(view: sublime.View, pt: int) -> str | None:
    line_span = view.line(pt)
    for r in extract_message_regions(view):
        if line_span.a <= r.a <= line_span.b:  # optimized `line_span.contains(r)`
            return view.substr(r)
    else:
        return None


def find_matching_commit(dot: Char, message: str, forward: bool = True) -> Iterator[Char]:
    dots = follow_dots(dot, forward=forward)
    for dot, this_message in _with_message(take(100, dots)):
        this_message = this_message.rstrip(".").strip()
        shorter, longer = sorted((message, this_message), key=len)
        if longer.startswith(shorter):
            yield dot


def find_fixups_upwards(dot: Char, message: str) -> Iterator[tuple[Char, str]]:
    messages = add_fixup_or_squash_prefixes(message.rstrip(".").strip())

    previous_dots = follow_dots(dot, forward=False)
    for dot, this_message in _with_message(take(50, previous_dots)):
        this_message = this_message.rstrip(".").strip()
        if is_fixup_or_squash_message(this_message):
            for message in messages:
                shorter, longer = sorted((message, this_message), key=len)
                if longer.startswith(shorter):
                    yield dot, this_message


def _with_message(dots: Iterable[Char]) -> Iterator[tuple[Char, str]]:
    return filter_(map(with_message, dots))


def with_message(dot: Char) -> tuple[Char, str] | None:
    commit_message = commit_message_from_point(dot.view, dot.pt)
    if commit_message:
        return dot, commit_message
    return None
