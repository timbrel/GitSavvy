from itertools import chain

import sublime
from sublime_plugin import TextCommand

from ..fns import accumulate, filter_, unique
from ..git_command import GitCommand
from ..parse_diff import SplittedDiff, UnsupportedCombinedDiff
from ..utils import flash


__all__ = (
    "gs_stage_hunk",
)


from typing import Iterator, List, NamedTuple, Optional
from ..parse_diff import Hunk as HunkText
from ..types import LineNo


class Hunk(NamedTuple):
    a_start: LineNo
    a_length: int
    b_start: LineNo
    b_length: int
    content: str


class gs_stage_hunk(TextCommand, GitCommand):
    def run(self, edit):
        # type: (sublime.Edit) -> None
        view = self.view
        file_path = view.file_name()
        if not file_path:
            flash(view, "Cannot stage on unnnamed buffers.")
            return

        if view.is_dirty():
            flash(view, "Cannot stage on unsaved files.")
            return

        raw_diff = self.git("diff", "-U0", file_path)
        if not raw_diff:
            not_tracked_file = self.git("ls-files", file_path).strip() == ""
            if not_tracked_file:
                self.git("add", file_path)
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
    lines = unique(
        view.rowcol(line.begin())[0] + 1
        for region in view.sel()
        for line in view.lines(region)
    )
    hunks = list(map(parse_hunk, diff.hunks))
    return list(unique(filter_(hunk_containing_line(hunks, line) for line in lines)))


def parse_hunk(hunk):
    # type: (HunkText) -> Hunk
    return Hunk(*hunk.header().parse(), content=hunk.content().text)


def hunk_containing_line(hunks, line):
    # type: (List[Hunk], LineNo) -> Optional[Hunk]
    # Assumes `hunks` are sorted
    for hunk in hunks:
        if line < hunk.b_start:
            break
        # Assume a length of "2" for removal only hunks so the
        # user can actually grab them exactly on the line above
        # *or* below the removal gutter mark which is a triangle
        # between two lines.
        if hunk_of_removals_only(hunk):
            b_end = hunk.b_start + 2
        else:
            b_end = hunk.b_start + max(hunk.b_length, 1)
        if hunk_with_no_newline_marker(hunk):
            # Make the hit area one line longer so that the user
            # can stage being on the last line of the view (if the
            # newline gets *added* in this hunk). This is technially
            # wrong if the newline gets *removed* but doesn't do any
            # harm because there can't be any line after that anyway.
            b_end += 1
        if hunk.b_start <= line < b_end:
            return hunk
    return None


def hunk_with_no_newline_marker(hunk):
    # type: (Hunk) -> bool
    # Avoid looking for "No newline..." which depends on the locale setting
    return "\n\\ " in hunk.content


def format_patch(header, hunks, reverse=False):
    # type: (str, List[Hunk], bool) -> str
    rewrite = rewrite_hunks_for_reverse_apply if reverse else rewrite_hunks
    return ''.join(chain(
        [header],
        map(format_hunk, rewrite(hunks))
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


def rewrite_hunks_for_reverse_apply(hunks):
    # type: (List[Hunk]) -> Iterator[Hunk]
    # Assumes `hunks` are sorted, and from the same file
    deltas = (hunk.b_length - hunk.a_length for hunk in hunks)
    offsets = accumulate(deltas, initial=0)
    for hunk, offset in zip(hunks, offsets):
        new_a = hunk.b_start - offset
        if hunk_of_additions_only(hunk):
            new_a -= 1
        elif hunk_of_removals_only(hunk):
            new_a += 1
        yield hunk._replace(a_start=new_a)


def hunk_of_additions_only(hunk):
    # type: (Hunk) -> bool
    # Note that this can only ever be true for zero context diffs
    return hunk.a_length == 0 and hunk.b_length > 0


def hunk_of_removals_only(hunk):
    # type: (Hunk) -> bool
    # Note that this can only ever be true for zero context diffs
    return hunk.b_length == 0 and hunk.a_length > 0


def pluralize(word, count):
    # type: (str, int) -> str
    return word if count == 1 else word + "s"
