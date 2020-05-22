from collections import namedtuple
from itertools import chain
import re

import sublime
from sublime_plugin import TextCommand

from ..fns import accumulate, filter_, unique
from ..git_command import GitCommand
from ..parse_diff import SplittedDiff


__all__ = (
    "gs_inline_stage_hunk",
)


MYPY = False
if MYPY:
    from typing import Iterator, List, NamedTuple, Optional, Tuple
    from ..parse_diff import Hunk as HunkText


if MYPY:
    Hunk = NamedTuple("Hunk", [
        ("a_start", int),
        ("a_length", int),
        ("b_start", int),
        ("b_length", int),
        ("content", str)
    ])
else:
    Hunk = namedtuple("Hunk", "a_start a_length b_start b_length content")


class UnsupportedCombinedDiff(RuntimeError):
    pass


def flash(view, message):
    # type: (sublime.View, str) -> None
    window = view.window()
    if window:
        window.status_message(message)


class gs_inline_stage_hunk(TextCommand, GitCommand):
    def run(self, edit):
        view = self.view
        fpath = view.file_name()
        if not fpath:
            flash(view, "Cannot stage on unnnamed buffers.")
            return

        if view.is_dirty():
            flash(view, "Cannot stage on unsaved files.")
            return

        raw_diff = self.git("diff", "-U0", fpath)
        if not raw_diff:
            not_tracked_file = self.git("ls-files", fpath).strip() == ""
            if not_tracked_file:
                self.git("add", fpath)
                flash(view, "Staged whole file.")
            else:
                flash(view, "The file is clean.")
            return

        diff = SplittedDiff.from_string(raw_diff)
        assert len(diff.headers) == 1

        try:
            hunks = hunks_touching_selection(diff, view)
        except UnsupportedCombinedDiff:
            flash(view, "Files with merge conflicts are not supported.")
            return

        if not hunks:
            flash(view, "Not on a hunk.")
            return

        patch = format_patch(diff.headers[0].text, hunks)
        self.git("apply", "--cached", "--unidiff-zero", "-", stdin=patch)

        hunk_count = len(hunks)
        flash(view, "Staged {} {}.".format(hunk_count, pluralize("hunk", hunk_count)))


def hunks_touching_selection(diff, view):
    # type: (SplittedDiff, sublime.View) -> List[Hunk]
    rows = unique(
        view.rowcol(line.begin())[0] + 1
        for region in view.sel()
        for line in view.lines(region)
    )
    hunks = list(map(parse_hunk, diff.hunks))
    return list(unique(filter_(hunk_containing_row(hunks, row) for row in rows)))


def parse_hunk(hunk):
    # type: (HunkText) -> Hunk
    return Hunk(*parse_metadata(hunk.header().text), content=hunk.content().text)


def hunk_containing_row(hunks, row):
    # type: (List[Hunk], int) -> Optional[Hunk]
    # Assumes `hunks` are sorted
    for hunk in hunks:
        if row < hunk.b_start:
            break
        # Assume a length of "1" for removal only hunks so the
        # user can actually grab them exactly on the line above the
        # removal gutter mark.
        b_end = hunk.b_start + max(hunk.b_length, 1)
        if hunk_with_no_newline_marker(hunk):
            # Make the hit area one line longer so that the user
            # can stage being on the last line of the view (if the
            # newline gets *added* in this hunk). This is technially
            # wrong if the newline gets *removed* but doesn't do any
            # harm because there can't be any line after that anyway.
            b_end += 1
        if hunk.b_start <= row < b_end:
            return hunk
    return None


def hunk_with_no_newline_marker(hunk):
    # type: (Hunk) -> bool
    # Avoid looking for "No newline..." which depends on the locale setting
    return "\n\\ " in hunk.content


def format_patch(header, hunks):
    # type: (str, List[Hunk]) -> str
    return ''.join(chain(
        [header],
        map(format_hunk, rewrite_hunks(hunks))
    ))


def format_hunk(hunk):
    # type: (Hunk) -> str
    return "@@ -{},{} +{},{} @@\n{}".format(*hunk)


def rewrite_hunks(hunks):
    # type: (List[Hunk]) -> Iterator[Hunk]
    # Assumes `hunks` are sorted, and from the same file
    deltas = (hunk.b_length - hunk.a_length for hunk in hunks)
    offsets = accumulate(deltas, initial=0)
    for hunk, offset in zip(hunks, offsets):
        new_b = hunk.a_start + offset
        if hunk_of_additions_only(hunk):
            new_b += 1
        elif hunk_of_removals_only(hunk):
            new_b -= 1
        yield hunk._replace(b_start=new_b)


def hunk_of_additions_only(hunk):
    # type: (Hunk) -> bool
    # Note that this can only ever be true for zero context diffs
    return hunk.a_length == 0 and hunk.b_length > 0


def hunk_of_removals_only(hunk):
    # type: (Hunk) -> bool
    # Note that this can only ever be true for zero context diffs
    return hunk.b_length == 0 and hunk.a_length > 0


def rewrite_hunks_for_reset(hunks):
    # type: (List[Hunk]) -> Iterator[Hunk]
    # Assumes `hunks` are sorted, and from the same file
    deltas = (hunk.b_length - hunk.a_length for hunk in hunks)
    offsets = accumulate(deltas, initial=0)
    for hunk, offset in zip(hunks, offsets):
        new_a, new_b = hunk.b_start - offset, hunk.a_start
        if hunk_of_additions_only(hunk):
            new_a -= 1
            new_b += 1
        elif hunk_of_removals_only(hunk):
            new_a += 1
            new_b -= 1
        yield hunk._replace(a_start=new_a, b_start=new_b)


LINE_METADATA = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_metadata(line):
    # type: (str) -> Tuple[int, int, int, int]
    match = LINE_METADATA.match(line)
    if match is None:
        raise UnsupportedCombinedDiff(line)
    a_start, a_length, b_start, b_length = match.groups()
    return int(a_start), int(a_length or "1"), int(b_start), int(b_length or "1")


def pluralize(word, count):
    # type: (str, int) -> str
    return word if count == 1 else word + "s"
