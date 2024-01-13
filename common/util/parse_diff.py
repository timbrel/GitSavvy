"""
Parse and process output from `git diff` command.
"""

from GitSavvy.core.parse_diff import HunkLine, SplittedDiff

from typing import Iterator, List, NamedTuple
from GitSavvy.core.types import LineNo


class Change(NamedTuple):
    raw: str
    type: str
    head_pos: LineNo
    saved_pos: LineNo
    text: str


class Hunk(NamedTuple):
    raw_lines: List[str]
    changes: List[Change]
    head_start: LineNo
    head_length: int
    saved_start: LineNo
    saved_length: int


def parse_diff(diff_str):
    # type: (str) -> List[Hunk]
    """
    Given the string output from a `git diff` command, parse the string into
    hunks and, more granularly, meta-data and change information for each of
    those hunks.
    """
    hunks = []
    for hunk in SplittedDiff.from_string(diff_str).hunks:
        head_start, head_length, saved_start, saved_length = hunk.header().parse()
        changes = _get_changes(hunk.content().lines(), head_start, saved_start)
        # Remove lines warning about "No newline at end of file"; change.type will == `\`.
        changes_filtered = [change for change in changes if change.type != "\\"]
        hunks.append(
            Hunk(
                hunk.text.splitlines(keepends=True),
                changes_filtered,
                head_start,
                head_length,
                saved_start,
                saved_length
            )
        )

    return hunks


def _get_changes(hunk_lines, head_start, saved_start):
    # type: (List[HunkLine], LineNo, LineNo) -> Iterator[Change]
    """
    Transform a list of `+` or `-` lines from a `git diff` output
    into tuples with the original raw line, the type of the change,
    the position of the HEAD- and saved- versions at that line, and
    the text of the line with the `+` or `-` removed.
    """
    head_pos = head_start
    saved_pos = saved_start

    for line in hunk_lines:
        yield Change(line.text, line.mode, head_pos, saved_pos, line.content)
        if line.is_from_line():
            head_pos += 1
        elif line.is_to_line():
            saved_pos += 1
