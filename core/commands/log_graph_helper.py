from __future__ import annotations

import re
from typing import Dict, List, Literal, Optional, Sequence, TypedDict

import sublime

from ..git_mixins.branches import Branch
from . import log_graph_colorizer as colorizer


GRAPH_CHAR_OPTIONS = r" /_\|\-\\."
COMMIT_LINE = re.compile(
    r"^[{graph_chars}]*(?P<dot>[{node_chars}])[{graph_chars}]* "
    r"(?P<commit_hash>[a-f0-9]{{5,40}}) +"
    r"(\((?P<decoration>.+?)\))?"
    .format(graph_chars=GRAPH_CHAR_OPTIONS, node_chars=colorizer.COMMIT_NODE_CHARS)
)
FIND_COMMIT_HASH = "^[{graph_chars}]*[{node_chars}][{graph_chars}]* ".format(
    graph_chars=GRAPH_CHAR_OPTIONS, node_chars=colorizer.COMMIT_NODE_CHARS
)


class LineInfo(TypedDict, total=False):
    commit: str
    HEAD: str
    branches: List[str]
    local_branches: List[str]
    tags: List[str]


ListItems = Literal["branches", "local_branches", "tags"]


def describe_graph_line(line: str, known_branches: Dict[str, Branch]) -> Optional[LineInfo]:
    match = COMMIT_LINE.match(line)
    if match is None:
        return None

    commit_hash = match.group("commit_hash")
    decoration = match.group("decoration")

    rv: LineInfo = {"commit": commit_hash}
    if decoration:
        names = decoration.split(", ")
        if names[0].startswith("HEAD"):
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
                branch = known_branches.get(name)
                if branch and branch.is_local:
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
        region = view.find_by_selector(
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
