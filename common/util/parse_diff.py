"""
Parse and process output from `git diff` command.
"""

from collections import namedtuple
from itertools import islice
import re

Hunk = namedtuple("Hunk", ("raw_lines", "changes", "head_start", "head_length", "saved_start", "saved_length"))
Change = namedtuple("Change", ("raw", "type", "head_pos", "saved_pos", "text"))

re_metadata = re.compile("^@@ -(\d+)(,(\d+))? \+(\d+)(,(\d+))? @@")


def parse_diff(diff_str):
    """
    Given the string output from a `git diff` command, parse the string into
    hunks and, more granularly, meta-data and change information for each of
    those hunks.
    """
    hunks = []

    raw_hunks = _split_into_hunks(diff_str.splitlines())

    for raw_hunk in raw_hunks:
        hunk_lines = list(raw_hunk)
        head_start, head_length, saved_start, saved_length = _get_metadata(hunk_lines[0])
        changes = _get_changes(hunk_lines[1:], head_start, saved_start)
        # Remove lines warning about "No newline at end of file"; change[1] will == `\`.
        changes_filtered = tuple(change for change in changes if change[1] != "\\")
        hunks.append(Hunk(hunk_lines, changes_filtered, head_start, head_length, saved_start, saved_length))

    return hunks


def _split_into_hunks(lines):
    """
    Given an array of lines from the output of a `git-diff` command, yield
    slices of the lines that correspond to the hunks in the diff.
    """
    # Throw away the first four lines of the git output.
    #   diff --git a/c9d70aa928a3670bc2b879b4a596f10d3e81ba7c b/d95427d30480b29b99b407981c8e048b6e0c902d
    #   index c9d70aa..d95427d 100644
    #   --- a/c9d70aa928a3670bc2b879b4a596f10d3e81ba7c
    #   +++ b/d95427d30480b29b99b407981c8e048b6e0c902d
    lines = lines[4:]
    lines_iter = enumerate(lines)
    start = 0

    if len(lines) < 1:
        return

    for line_number, line in lines_iter:
        if not line.startswith(("+", "-", "\\")) and line_number != 0:
            yield islice(lines, start, line_number)
        if line.startswith("@@"):
            start = line_number

    yield islice(lines, start, line_number + 1)


def _get_metadata(meta_str):
    """
    Given the `@@ ... @@` header from the beginning of a hunk, return
    the start and length values from that header.
    """
    match = re_metadata.match(meta_str)
    head_start, _, head_length, saved_start, _, saved_length = match.groups()
    return (int(head_start),
            int(head_length or "1"),
            int(saved_start),
            int(saved_length or "1"))


def _get_changes(hunk_lines, head_start, saved_start):
    """
    Transform a list of `+` or `-` lines from a `git diff` output
    into tuples with the original raw line, the type of the change,
    the position of the HEAD- and saved- versions at that line, and
    the text of the line with the `+` or `-` removed.
    """
    changes = []
    head_pos = head_start
    saved_pos = saved_start

    for raw_line in hunk_lines:
        change_type = raw_line[0]
        text = raw_line[1:]
        changes.append((raw_line, change_type, head_pos, saved_pos, text))
        if change_type == "-":
            head_pos += 1
        elif change_type == "+":
            saved_pos += 1

    return changes
