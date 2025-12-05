import difflib
from functools import lru_cache, partial
from itertools import groupby, zip_longest
import re

import sublime
from ..fns import accumulate, filter_, flatten
from ..parse_diff import Hunk, SplittedDiff
from ..text_helper import Region
from ..utils import eat_but_log_errors, line_indentation
from ..runtime import cooperative_thread_hopper, AWAIT_WORKER, HopperR


from typing import Callable, List, Tuple, Sequence
from ..parse_diff import HunkLine
Chunk = List[HunkLine]


@eat_but_log_errors()
def annotate_intra_line_differences(view, diff_text=None, offset=0):
    # type: (sublime.View, str, int) -> None
    # import profile
    # profile.runctx('compute_intra_line_diffs(view)', globals(), locals(), sort='cumtime')
    if diff_text is None:
        diff = SplittedDiff.from_view(view)
    else:
        diff = SplittedDiff.from_string(diff_text, offset)
    compute_intra_line_diffs(view, diff)


def view_has_changed_factory(view):
    # type: (sublime.View) -> Callable[[], bool]
    cc = view.change_count()

    def view_has_changed():
        # type: () -> bool
        return not view.is_valid() or view.change_count() != cc

    return view_has_changed


@cooperative_thread_hopper
def compute_intra_line_diffs(view, diff):
    # type: (sublime.View, SplittedDiff) -> HopperR
    viewport = view.visible_region()
    view_has_changed = view_has_changed_factory(view)

    chunks = filter(is_modification_group, flatten(map(group_non_context_lines, diff.hunks)))
    above_viewport, in_viewport, below_viewport = [], [], []  # type: Tuple[List[Chunk], List[Chunk], List[Chunk]]
    for chunk in chunks:
        chunk_region = compute_chunk_region(chunk)
        container = (
            in_viewport if chunk_region.intersects(viewport)
            else above_viewport if chunk_region < viewport
            else below_viewport
        )
        container.append(chunk)

    from_regions = []
    to_regions = []

    for chunk in in_viewport:
        new_from_regions, new_to_regions = intra_line_diff_for_chunk(chunk)
        from_regions.extend(new_from_regions)
        to_regions.extend(new_to_regions)

    _draw_intra_diff_regions(view, to_regions, from_regions)

    timer = yield AWAIT_WORKER
    if view_has_changed():
        return

    # Consider some chunks [1, 2, 3, 4] where 3 was *in* the viewport and thus
    # rendered immediately. Now, [1, 2] + [4] await their render. The following
    # `zip_longest(reversed` dance generates [2, 4, 1] as the unit of work, t.i.
    # we move from the viewport to the edges (inside-out).
    for chunk in filter_(flatten(zip_longest(reversed(above_viewport), below_viewport))):
        new_from_regions, new_to_regions = intra_line_diff_for_chunk(chunk)
        from_regions.extend(new_from_regions)
        to_regions.extend(new_to_regions)

        if timer.exhausted_ui_budget():
            if view_has_changed():
                return
            _draw_intra_diff_regions(view, to_regions, from_regions)
            timer = yield AWAIT_WORKER
            if view_has_changed():
                return

    if view_has_changed():
        return
    _draw_intra_diff_regions(view, to_regions, from_regions)


def _draw_intra_diff_regions(view, added_regions, removed_regions):
    view.add_regions(
        "git-savvy-added-bold",
        added_regions,
        scope="diff.inserted.char.git-savvy.diff",
        flags=sublime.RegionFlags.NO_UNDO
    )
    view.add_regions(
        "git-savvy-removed-bold",
        removed_regions,
        scope="diff.deleted.char.git-savvy.diff",
        flags=sublime.RegionFlags.NO_UNDO
    )


def group_non_context_lines(hunk):
    # type: (Hunk) -> List[Chunk]
    """Return groups of chunks(?) (without context) from a hunk."""
    # A hunk can contain many modifications interleaved
    # with context lines. Return just these modification
    # lines grouped as units. Note that we process per
    # column here which enables basic support for combined
    # diffs.
    # Note: No newline marker lines are just ignored t.i.
    # skipped.
    mode_len = hunk.mode_len()
    content_lines = list(
        line
        for line in hunk.content().lines()
        if not line.is_no_newline_marker()  # <==
    )
    chunks = [
        list(lines)
        for is_context, lines in groupby(
            content_lines,
            key=lambda line: line.is_context()
        )
        if not is_context
    ]
    if mode_len < 2:
        return chunks

    # For combined diffs, go over all chunks again, now column by column,
    # first removing "local" context lines. After that filter out empty
    # chunks.
    return list(filter_(  # <== remove now empty chunks
        [
            line for line in chunk
            if line.mode[n] != ' '  # <== remove context lines
        ]
        for n in range(mode_len)
        for chunk in chunks
    ))


def is_modification_group(lines):
    # type: (Chunk) -> bool
    """Mark groups which have both + and - modes."""
    # Since these groups are always sorted in git, from a to b,
    # such a group starts with a "-" and ends with a "+".
    return lines[0].is_from_line() and lines[-1].is_to_line()


def compute_chunk_region(lines):
    # type: (Chunk) -> sublime.Region
    return sublime.Region(lines[0].a, lines[-1].b)


def intra_line_diff_for_chunk(group):
    # type: (Chunk) -> Tuple[List[Region], List[Region]]
    from_lines, to_lines = [
        list(lines)
        for mode, lines in groupby(group, key=lambda line: line.is_from_line())
    ]

    algo = (
        intra_diff_line_by_line
        if len(from_lines) == len(to_lines)
        else intra_diff_general_algorithm
    )
    return algo(from_lines, to_lines)


@lru_cache(maxsize=512)
def match_sequences(a, b, is_junk=difflib.IS_CHARACTER_JUNK, max_token_length=250):
    # type: (Sequence, Sequence, Callable[[str], bool], int) -> difflib.SequenceMatcher
    if (len(a) + len(b)) > max_token_length:
        return NullSequenceMatcher(is_junk, a='', b='')
    matches = difflib.SequenceMatcher(is_junk, a=a, b=b)
    matches.ratio()  # for the side-effect of doing the computational work and caching it
    return matches


class NullSequenceMatcher(difflib.SequenceMatcher):
    def ratio(self):
        return 0

    def get_opcodes(self):
        return []


def intra_diff_general_algorithm(from_lines, to_lines):
    # type: (List[HunkLine], List[HunkLine]) -> Tuple[List[Region], List[Region]]
    # Generally, if the two line chunks have different size, try to fit
    # the smaller one into the bigger, or try to find how and where the
    # smaller could be placed in the bigger one.
    #
    # E.g. you have 3 "+" lines, but 5 "-" lines. For now take the *first*
    # "+" line, and match it against "-" lines. The highest match ratio wins,
    # and we take it as the starting point for the intra-diff line-by-line
    # algorithm.
    if len(from_lines) < len(to_lines):
        base_line = from_lines[0].content
        matcher = partial(match_sequences, base_line)
        # We want that the smaller list fits completely into the bigger,
        # so we can't compare against *all* lines but have to stop
        # somewhere earlier.
        #
        # E.g. a = [1, 2, 3, 4, 5]; b = [1, 2].  Then, the candidates are
        #          [1, 2, 3, 4] otherwise b won't fit anymore
        #                   [1, 2]
        match_candidates = to_lines[:len(to_lines) - len(from_lines) + 1]
        to_lines = to_lines[find_best_slice(matcher, match_candidates)]
    else:
        base_line = to_lines[0].content
        matcher = partial(match_sequences, b=base_line)
        match_candidates = from_lines[:len(from_lines) - len(to_lines) + 1]
        from_lines = from_lines[find_best_slice(matcher, match_candidates)]

    if to_lines and from_lines:
        return intra_diff_line_by_line(from_lines, to_lines)
    else:
        return [], []


def find_best_slice(matcher, lines):
    # type: (Callable[[str], difflib.SequenceMatcher], List[HunkLine]) -> slice
    scores = []
    for n, line in enumerate(lines):
        matches = matcher(line.content)
        ratio = matches.ratio()
        if ratio >= 0.85:
            break
        scores.append((ratio, n))
    else:
        ratio, n = max(scores)
        if ratio < 0.75:
            return slice(0)

    return slice(n, None)


def intra_diff_line_by_line(from_lines, to_lines):
    # type: (List[HunkLine], List[HunkLine]) -> Tuple[List[Region], List[Region]]
    # Note: We have no guarantees here that from_lines and to_lines
    # have the same length, we use `zip` currently which produces
    # iterables of the shortest length of both!
    from_regions = []
    to_regions = []

    for from_line, to_line in zip(from_lines, to_lines):  # zip! see comment above
        # Compare without the leading mode char using ".content", but
        # also dedent both lines because leading common spaces will produce
        # higher ratios and produce slightly more ugly diffs.
        indentation = min(
            line_indentation(from_line.content),
            line_indentation(to_line.content)
        )
        a_input, b_input = from_line.content[indentation:], to_line.content[indentation:]
        matches = match_sequences(a_input, b_input)
        if matches.ratio() < 0.5:
            # We just continue, so it is possible that for a given chunk
            # *some* lines have markers, others not.
            # A different implementation could be: if *any* line within a hunk
            # is really low, like here 0.5, drop the hunk altogether.
            continue

        # Use tokenize strategy when there are "nearby" or fragmented splits
        # because it produces more calm output.
        if is_fragmented_match(matches):
            a_input = tokenize_string(a_input)  # type: ignore
            b_input = tokenize_string(b_input)  # type: ignore
            matches = match_sequences(a_input, b_input)

        a_offset = from_line.a + from_line.mode_len + indentation
        b_offset = to_line.a + to_line.mode_len + indentation
        from_offsets = tuple(accumulate(map(len, a_input), initial=a_offset))
        to_offsets = tuple(accumulate(map(len, b_input), initial=b_offset))
        for op, a_start, a_end, b_start, b_end in matches.get_opcodes():
            if op == 'equal':
                continue

            if a_start != a_end:
                from_regions.append(Region(from_offsets[a_start], from_offsets[a_end]))

            if b_start != b_end:
                to_regions.append(Region(to_offsets[b_start], to_offsets[b_end]))

    return from_regions, to_regions


boundary = re.compile(r'(\W)')
OPERATOR_CHARS = '=!<>'
COMPARISON_SENTINEL = object()


def tokenize_string(input_str):
    # type: (str) -> Sequence[str]
    # Usually a simple split on "\W" suffices, but here we join some
    # "operator" chars again.
    # About the "operator" chars: we want to treat e.g. "==" and "!="
    # as one token.  It is not important to treat e.g. "&&" as one token
    # because there is probably no "|&".  That is to say, if all chars
    # change we get a clean diff anyway, for the comparison operators
    # often only *one* of the characters changes ("<" to "<=" or "=="
    # to "!=") and then it looks better esp. with ligatures (!) if we
    # treat them as one token.
    return tuple(
        flatten(
            [''.join(chars)]
            if ch is COMPARISON_SENTINEL
            else list(chars)
            for ch, chars in groupby(
                filter(None, boundary.split(input_str)),
                key=lambda x: COMPARISON_SENTINEL if x in OPERATOR_CHARS else x
            )
        )
    )


def is_fragmented_match(matches):
    # type: (difflib.SequenceMatcher) -> bool
    a_input, b_input = matches.a, matches.b  # type: ignore  # stub bug?
    return any(
        (
            op == 'equal'
            and a_end - a_start == 1
            and not a_input[a_start:a_end] == '\n'
        )
        or (
            op == 'equal'
            and b_end - b_start == 1
            and not b_input[b_start:b_end] == '\n'
        )
        or (
            op != 'equal'
            and boundary.search(a_input[a_start:a_end] + b_input[b_start:b_end])
        )
        for op, a_start, a_end, b_start, b_end in matches.get_opcodes()
    )
