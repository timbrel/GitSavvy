"""
Side-by-side ("split") presentation of the diff view.

The diff view is a view full of commands that read the unified patch straight
out of the buffer.  The split presentation therefore never becomes the source
of truth: the unified text is kept here, the buffer shows a two column
rendering of it, and for the commands that need the patch the buffer is put
back to the unified text for as long as the command runs.

    `render`           unified buffer -> split buffer
    `restore_unified`  split buffer -> unified buffer

Both translate the cursors and the scroll position, so that switching is not
a jump.
"""
from __future__ import annotations
from bisect import bisect_right
from contextlib import contextmanager
from functools import wraps
from itertools import zip_longest
import time
import unicodedata

import sublime
from sublime_plugin import EventListener, TextCommand

from .. import app_state
from .. import intra_line_colorizer
from ..parse_diff import SplittedDiff
from ..utils import flash
from ..view import replace_view_content


__all__ = (
    "gs_diff_toggle_split",
    "GsDiffSplitListener",
)


from typing import Dict, Iterator, List, NamedTuple, Optional, Tuple
from ..parse_diff import Hunk, HunkLine


SPLIT_SETTING = "git_savvy.diff_view.split"
RENDERED_SETTING = "git_savvy.diff_view.split_rendered"
APP_STATE_KEY = "diff_view_split"

# The unified prelude ends in "--", which is what hands the rest of the buffer
# to the diff syntax.  The split prelude ends in "==" instead, see
# `syntax/diff_view.sublime-syntax`.
UNIFIED_TERMINATOR = "\n--\n"
SPLIT_TERMINATOR = "\n==\n"

DIVIDER = " │ "
MIN_CELL_WIDTH = 20
FALLBACK_COLUMNS = 160
INTRA_LINE_BUDGET = 0.25  # [s]

DELETED_KEY = "git-savvy-split-deleted"
INSERTED_KEY = "git-savvy-split-inserted"
DELETED_CHAR_KEY = "git-savvy-removed-bold"  # shared with the unified view
INSERTED_CHAR_KEY = "git-savvy-added-bold"
PHANTOM_KEY = "git-savvy-split-toggle"
HUNK_BUTTONS_KEY = "git-savvy-hunk-buttons"

class LineInfo(NamedTuple):
    """Where a line of a hunk went."""
    row: int               # the first split row of the line
    content_col: int       # the column at which its text begins, in chars
    seg_starts: List[int]  # for every row it takes: the offset into `text`
    text: str              # its text as shown, t.i. with tabs expanded
    index_map: List[int]   # offset into the raw text -> offset into `text`
    a: int                 # the offset of the line in the unified text
    mode_len: int


class Cell(NamedTuple):
    urow: int
    seg_start: int
    content_col: int


class SplitState:
    def __init__(self, prelude: str, diff_text: str, columns: int) -> None:
        self.prelude = prelude
        self.diff_text = diff_text
        self.columns = columns
        self.text = ""
        self.lines: Dict[int, LineInfo] = {}
        # unified row -> split row
        self.u2s: List[int] = []
        # split row -> what is in its left and in its right half; a row that
        # was taken over as it is has a left half only, with `content_col` -1
        self.s2u: List[Tuple[Optional[Cell], Optional[Cell]]] = []
        self.right_col: Dict[int, int] = {}  # split row -> where the right half begins
        self.row_starts: List[int] = []
        self.hunk_regions: List[Tuple[int, int]] = []
        self.chunk_regions: List[Tuple[int, int]] = []
        self.header_points: List[int] = []
        self.deleted: List[Tuple[int, int]] = []
        self.inserted: List[Tuple[int, int]] = []
        self.deleted_chars: List[Tuple[int, int]] = []
        self.inserted_chars: List[Tuple[int, int]] = []


# What `_draw` put into the view last, t.i. what a toggle has to render.
last_drawn: Dict[sublime.ViewId, Tuple[str, str]] = {}
states: Dict[sublime.ViewId, SplitState] = {}
# Views a command is working on that needs the unified text.  As long as a
# view is in here a refresh leaves it unified.
holds: Dict[sublime.ViewId, int] = {}
# Unified rows of context lines whose cursor came from the right half.
right_side_hints: Dict[sublime.ViewId, set] = {}


def default_mode() -> bool:
    return bool(app_state.get(APP_STATE_KEY, False))


def wants_split(view: sublime.View) -> bool:
    return bool(view.settings().get(SPLIT_SETTING))


def is_rendered(view: sublime.View) -> bool:
    return bool(view.settings().get(RENDERED_SETTING))


def is_held(view: sublime.View) -> bool:
    return view.id() in holds


def remember(view: sublime.View, prelude: str, diff_text: str) -> None:
    last_drawn[view.id()] = (prelude, diff_text)


def forget(view: sublime.View) -> None:
    vid = view.id()
    last_drawn.pop(vid, None)
    states.pop(vid, None)
    holds.pop(vid, None)
    right_side_hints.pop(vid, None)


# --- layout -----------------------------------------------------------------


def available_columns(view: sublime.View) -> int:
    width, _ = view.viewport_extent()
    em_width = view.em_width()
    if width <= 0 or em_width <= 0:
        return FALLBACK_COLUMNS
    return max(int(width / em_width) - 2, 2 * MIN_CELL_WIDTH)


def char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def display_width(text: str) -> int:
    if text.isascii():
        return len(text)
    return sum(map(char_width, text))


def expand_tabs(text: str, tab_size: int) -> Tuple[str, List[int]]:
    """Return `text` with its tabs as spaces, and for every offset into
    `text` the offset into the result."""
    if "\t" not in text:
        return text, list(range(len(text) + 1))

    out: List[str] = []
    index_map: List[int] = []
    col = 0
    for ch in text:
        index_map.append(col)
        if ch == "\t":
            n = tab_size - (col % tab_size)
            out.append(" " * n)
            col += n
        else:
            out.append(ch)
            col += 1
    index_map.append(col)
    return "".join(out), index_map


def wrap(text: str, width: int) -> List[Tuple[int, str]]:
    """Cut `text` into pieces no wider than `width`, between words if there
    is a gap in the last third of a piece."""
    if len(text) <= width and text.isascii():
        return [(0, text)]

    pieces: List[Tuple[int, str]] = []
    start, used = 0, 0
    i = 0
    while i < len(text):
        w = char_width(text[i])
        if used + w > width and i > start:
            gap = text.rfind(" ", start, i)
            if text[i] != " " and gap - start >= (i - start) * 2 // 3:
                i = gap + 1
            pieces.append((start, text[start:i]))
            start, used = i, 0
            continue
        used += w
        i += 1
    pieces.append((start, text[start:]))
    return pieces


def pad(text: str, width: int) -> str:
    return text + " " * max(0, width - display_width(text))


class _Side(NamedTuple):
    urow: int
    line_no: int
    line: HunkLine
    text: str
    index_map: List[int]
    changed: bool


def build(prelude: str, diff_text: str, columns: int, tab_size: int) -> Optional[SplitState]:
    """Lay the unified text out in two columns.

    Return `None` for what can't be shown that way.
    """
    assert prelude.endswith(UNIFIED_TERMINATOR)
    unified = prelude + diff_text
    diff = SplittedDiff.from_string(diff_text, len(prelude))
    if diff.is_combined_diff():
        return None

    ulines = unified.split("\n")
    ustarts: List[int] = []
    pos = 0
    for line in ulines:
        ustarts.append(pos)
        pos += len(line) + 1

    def urow_of(pt: int) -> int:
        return bisect_right(ustarts, pt) - 1

    try:
        hunks = {urow_of(hunk.a): (hunk, hunk.header().parse()) for hunk in diff.hunks}
    except Exception:
        return None

    number_width = max(
        [3] + [
            len(str(max(m.from_start + m.from_length, m.to_start + m.to_length)))
            for _, m in hunks.values()
        ]
    )
    gutter = number_width + 1
    cell_width = max((columns - 2 * gutter - len(DIVIDER)) // 2, MIN_CELL_WIDTH)
    blank_cell = " " * (gutter + cell_width)
    terminator_row = urow_of(len(prelude) - 2)

    state = SplitState(prelude, diff_text, columns)
    state.u2s = [0] * len(ulines)
    rows: List[str] = []
    pos = 0

    def emit(row: str) -> int:
        nonlocal pos
        rows.append(row)
        state.row_starts.append(pos)
        pos += len(row) + 1
        return len(rows) - 1

    def emit_verbatim(urow: int) -> None:
        text = ulines[urow]
        srow = emit("==" if urow == terminator_row else text)
        state.u2s[urow] = srow
        state.s2u.append((Cell(urow, 0, -1), None))

    def emit_pair(left: Optional[_Side], right: Optional[_Side]) -> None:
        lpieces = wrap(left.text, cell_width) if left else []
        rpieces = wrap(right.text, cell_width) if right else []
        first_row = len(rows)
        for k, (lpiece, rpiece) in enumerate(zip_longest(lpieces, rpieces)):
            if lpiece is None:
                lcell = blank_cell
            else:
                number = str(left.line_no) if k == 0 else ""  # type: ignore[union-attr]
                lcell = number.rjust(number_width) + " " + pad(lpiece[1], cell_width)
            right_col = len(lcell) + len(DIVIDER)
            if rpiece is None:
                rcell = ""
            else:
                number = str(right.line_no) if k == 0 else ""  # type: ignore[union-attr]
                rcell = number.rjust(number_width) + " " + (
                    pad(rpiece[1], cell_width)
                    if right.changed  # type: ignore[union-attr]
                    else rpiece[1]
                )
            row = lcell + DIVIDER + rcell
            start = pos
            srow = emit(row)
            state.right_col[srow] = right_col
            state.s2u.append((
                Cell(left.urow, lpiece[0], gutter) if left and lpiece else None,
                Cell(right.urow, rpiece[0], right_col + gutter) if right and rpiece else None,
            ))
            if left and left.changed and lpiece:
                state.deleted.append((start, start + len(lcell)))
            if right and right.changed and rpiece:
                state.inserted.append((start + right_col, start + len(row)))

        # A context line is in both halves; it is the left one it maps to.
        for side, pieces, is_right in ((right, rpieces, True), (left, lpieces, False)):
            if side is None:
                continue
            if is_right and left is not None and side.urow == left.urow:
                continue
            state.u2s[side.urow] = first_row
            state.lines[side.urow] = LineInfo(
                first_row,
                -1 if is_right else gutter,  # the right column differs per row
                [start_ for start_, _ in pieces],
                side.text,
                side.index_map,
                side.line.a,
                side.line.mode_len,
            )

    def emit_hunk(urow: int, hunk: Hunk, from_no: int, to_no: int) -> int:
        """Emit the hunk at `urow` and return the row after it."""
        hunk_start = pos
        emit_verbatim(urow)
        urow += 1

        def side(line: HunkLine, urow: int, line_no: int, changed: bool) -> _Side:
            text, index_map = expand_tabs(line.content.rstrip("\r\n"), tab_size)
            return _Side(urow, line_no, line, text, index_map, changed)

        deleted: List[_Side] = []
        added: List[_Side] = []
        chunk_start: Optional[int] = None

        def flush() -> None:
            nonlocal chunk_start
            if not deleted and not added:
                return
            chunk_start = pos
            for left, right in zip_longest(deleted, added):
                emit_pair(left, right)
            state.chunk_regions.append((chunk_start, pos - 1))
            del deleted[:]
            del added[:]

        # Not `hunk.content().lines()`: that splits at more than "\n", and
        # the rows here must be the rows of the view.
        last_row = urow_of(min(hunk.b, len(unified)) - 1)
        for urow in range(urow, last_row + 1):
            line = HunkLine(ulines[urow] + "\n", ustarts[urow])
            if line.is_no_newline_marker():
                state.u2s[urow] = max(len(rows) - 1, 0)
            elif line.is_from_line():
                if added:
                    flush()
                deleted.append(side(line, urow, from_no, True))
                from_no += 1
            elif line.is_to_line():
                added.append(side(line, urow, to_no, True))
                to_no += 1
            else:
                flush()
                emit_pair(
                    side(line, urow, from_no, False),
                    side(line, urow, to_no, False),
                )
                from_no += 1
                to_no += 1
        flush()
        state.hunk_regions.append((hunk_start, pos - 1))
        return last_row + 1

    urow = 0
    while urow < len(ulines):
        if urow in hunks:
            hunk, metadata = hunks[urow]
            urow = emit_hunk(urow, hunk, metadata.from_start, metadata.to_start)
        else:
            emit_verbatim(urow)
            urow += 1

    state.text = "\n".join(rows)
    state.header_points = [
        state.row_starts[state.u2s[urow_of(header.a)]]
        for header in diff.headers
    ]

    deadline = time.monotonic() + INTRA_LINE_BUDGET
    for hunk in diff.hunks:
        if time.monotonic() > deadline:
            break
        from_regions, to_regions = intra_line_colorizer.intra_line_diffs_for_hunk(hunk)
        for regions, container in (
            (from_regions, state.deleted_chars),
            (to_regions, state.inserted_chars),
        ):
            for region in regions:
                container.extend(_translate_span(state, urow_of(region.a), region.a, region.b))

    return state


def _translate_span(state: SplitState, urow: int, a: int, b: int) -> List[Tuple[int, int]]:
    """Translate the span `a..b` of one unified line into split regions."""
    info = state.lines.get(urow)
    if info is None:
        return []

    def to_shown(pt: int) -> int:
        raw = min(max(pt - info.a - info.mode_len, 0), len(info.index_map) - 1)
        return info.index_map[raw]

    start, end = to_shown(a), to_shown(b)
    spans = []
    for k, seg_start in enumerate(info.seg_starts):
        seg_end = (
            info.seg_starts[k + 1]
            if k + 1 < len(info.seg_starts)
            else len(info.text)
        )
        lo, hi = max(start, seg_start), min(end, seg_end)
        if lo < hi:
            base = state.row_starts[info.row + k] + _content_col(state, info, k)
            spans.append((base + lo - seg_start, base + hi - seg_start))
    return spans


def _content_col(state: SplitState, info: LineInfo, k: int) -> int:
    if info.content_col >= 0:
        return info.content_col
    _, right = state.s2u[info.row + k]
    assert right
    return right.content_col


# --- translating positions --------------------------------------------------


def unified_to_split(
    state: SplitState, urow: int, ucol: int, prefer_right: bool = False
) -> Tuple[int, int]:
    """Translate a (row, col) in the unified text to one in the split text."""
    if urow >= len(state.u2s):
        return max(len(state.s2u) - 1, 0), 0

    info = state.lines.get(urow)
    if info is None:
        return state.u2s[urow], ucol

    raw = min(max(ucol - info.mode_len, 0), len(info.index_map) - 1)
    shown = info.index_map[raw]
    k = max(bisect_right(info.seg_starts, shown) - 1, 0)
    srow = info.row + k
    if prefer_right:
        _, right = state.s2u[srow]
        if right and right.urow == urow:
            return srow, right.content_col + shown - info.seg_starts[k]
    return srow, _content_col(state, info, k) + shown - info.seg_starts[k]


def split_to_unified(state: SplitState, srow: int, scol: int) -> Tuple[int, int, bool]:
    """Translate a (row, col) in the split text to one in the unified text.

    The third value tells if the position was in the right half.
    """
    if srow >= len(state.s2u):
        return max(len(state.u2s) - 1, 0), 0, False

    left, right = state.s2u[srow]
    in_right_half = scol >= state.right_col.get(srow, 1 << 30)
    cell = (right or left) if in_right_half else (left or right)
    if cell is None:
        return state.u2s.index(srow) if srow in state.u2s else 0, 0, False
    if cell.content_col < 0:
        return cell.urow, scol, False

    info = state.lines.get(cell.urow)
    if info is None:  # the right half of a context line
        info = state.lines[left.urow]  # type: ignore[union-attr]
    shown = min(cell.seg_start + max(scol - cell.content_col, 0), len(info.text))
    raw = max(bisect_right(info.index_map, shown) - 1, 0)
    return cell.urow, info.mode_len + raw, cell is right


def _clamped_point(view: sublime.View, row: int, col: int) -> int:
    line = view.line(view.text_point(row, 0))
    return min(line.a + max(col, 0), line.b)


class _Anchor(NamedTuple):
    row: int
    y_offset: float


def _capture_anchor(view: sublime.View) -> Optional[_Anchor]:
    """Take the line the eye is on: the cursor if it is in sight, else the
    first line in sight."""
    _, vy = view.viewport_position()
    _, vh = view.viewport_extent()
    for s in view.sel():
        _, cy = view.text_to_layout(s.b)
        if 0 <= cy - vy < vh:
            return _Anchor(view.rowcol(s.b)[0], cy - vy)
        break

    top = view.layout_to_text((0, vy))
    _, ty = view.text_to_layout(top)
    return _Anchor(view.rowcol(top)[0], ty - vy)


def _apply_anchor(view: sublime.View, row: int, y_offset: float) -> None:
    _, y = view.text_to_layout(view.text_point(row, 0))
    view.set_viewport_position((0, max(y - y_offset, 0)), animate=False)


# --- swapping the buffer ----------------------------------------------------


def render(view: sublime.View) -> bool:
    """Show what was drawn last side by side.  Expects a unified buffer."""
    vid = view.id()
    drawn = last_drawn.get(vid)
    if drawn is None:
        return False

    prelude, diff_text = drawn
    columns = available_columns(view)
    tab_size = view.settings().get("tab_size") or 4
    state = build(prelude, diff_text, columns, tab_size)
    if state is None:
        if is_rendered(view):
            restore_unified(view)
        return False

    hints = right_side_hints.pop(vid, set())
    anchor = _capture_anchor(view)
    cursors = [
        (view.rowcol(s.a), view.rowcol(s.b))
        for s in view.sel()
    ]

    replace_view_content(view, state.text)
    states[vid] = state
    view.settings().set(RENDERED_SETTING, True)

    sel = view.sel()
    sel.clear()
    for a, b in cursors:
        sel.add(sublime.Region(*(
            _clamped_point(view, *unified_to_split(state, row, col, row in hints))
            for row, col in (a, b)
        )))
    if anchor:
        srow, _ = unified_to_split(state, anchor.row, 0)
        _apply_anchor(view, srow, anchor.y_offset)

    _draw_regions(view, state)
    # The mini diff in the gutter compares with what the view held before;
    # that is the unified text here, which makes every row a changed one.
    view.set_reference_document(state.text)
    update_toggle(view)
    _watch_width(view)
    return True


def restore_unified(view: sublime.View) -> Optional[SplitState]:
    """Put the unified text back.  Return what the view showed."""
    vid = view.id()
    if not is_rendered(view):
        return None

    view.settings().set(RENDERED_SETTING, False)
    for key in (
        DELETED_KEY, INSERTED_KEY, DELETED_CHAR_KEY, INSERTED_CHAR_KEY, HUNK_BUTTONS_KEY
    ):
        view.erase_regions(key)

    state = states.pop(vid, None)
    if state is None or state.text != view.substr(sublime.Region(0, view.size())):
        # E.g. after a restart: the buffer is there, what it was made from
        # is not.  The caller draws anew.
        return None

    anchor = _capture_anchor(view)
    hints = set()
    cursors = []
    for s in view.sel():
        ends = []
        for pt in (s.a, s.b):
            urow, ucol, in_right_half = split_to_unified(state, *view.rowcol(pt))
            if in_right_half:
                hints.add(urow)
            ends.append((urow, ucol))
        cursors.append(ends)
    right_side_hints[vid] = hints

    replace_view_content(view, state.prelude + state.diff_text)
    view.reset_reference_document()

    sel = view.sel()
    sel.clear()
    for a, b in cursors:
        sel.add(sublime.Region(_clamped_point(view, *a), _clamped_point(view, *b)))
    if anchor:
        urow, _, _ = split_to_unified(state, anchor.row, 0)
        _apply_anchor(view, urow, anchor.y_offset)
    return state


def ensure_unified(view: sublime.View) -> None:
    if not is_rendered(view):
        return
    if restore_unified(view) is None:
        view.run_command("gs_diff_refresh", {"sync": True})


@contextmanager
def unified_buffer(view: sublime.View) -> Iterator[None]:
    """Hand out the unified text for as long as the block runs.

    A refresh from within the block leaves the view unified, the split view
    is made from what is there when the block is left.
    """
    vid = view.id()
    if not is_rendered(view) and vid not in holds:
        yield
        return

    holds[vid] = holds.get(vid, 0) + 1
    try:
        ensure_unified(view)
        yield
    finally:
        holds[vid] -= 1
        if holds[vid] <= 0:
            del holds[vid]
            if view.is_valid() and wants_split(view) and not is_rendered(view):
                if not render(view):
                    update_toggle(view)


def reads_unified_patch(run):
    """Decorate the `run` of a command that reads the patch from the buffer."""
    @wraps(run)
    def wrapper(self, *args, **kwargs):
        with unified_buffer(self.view):
            return run(self, *args, **kwargs)
    return wrapper


def _draw_regions(view: sublime.View, state: SplitState) -> None:
    flags = sublime.RegionFlags.NO_UNDO
    for key, spans, scope in (
        (DELETED_KEY, state.deleted, "markup.deleted.git-savvy.diff-split"),
        (INSERTED_KEY, state.inserted, "markup.inserted.git-savvy.diff-split"),
        (DELETED_CHAR_KEY, state.deleted_chars, "diff.deleted.char.git-savvy.diff"),
        (INSERTED_CHAR_KEY, state.inserted_chars, "diff.inserted.char.git-savvy.diff"),
    ):
        view.add_regions(
            key,
            [sublime.Region(a, b) for a, b in spans],
            scope=scope,
            flags=flags
        )


def _watch_width(view: sublime.View) -> None:
    """Lay out again if the room the view has is not what was laid out for.

    That is the case for a view that was just created, which has no size yet.
    """
    def check() -> None:
        state = states.get(view.id())
        if (
            state
            and view.is_valid()
            and is_rendered(view)
            and not is_held(view)
            and available_columns(view) != state.columns
        ):
            restore_unified(view)
            render(view)

    sublime.set_timeout(check, 100)


def navigation_regions(view: sublime.View, in_chunks: bool) -> Optional[List[sublime.Region]]:
    """What `gs_diff_navigate` travels between, or `None` for a unified buffer."""
    if not is_rendered(view):
        return None
    state = states.get(view.id())
    if state is None:
        return None

    if not state.hunk_regions:
        return [sublime.Region(pt) for pt in state.header_points]
    spans = state.chunk_regions if in_chunks else state.hunk_regions
    return [sublime.Region(a, b) for a, b in spans]


# --- the switch on top ------------------------------------------------------


TOGGLE_TEMPLATE = """
<body id="git-savvy-diff-split-toggle">
<style>
    body {{
        padding: 2px 4px;
    }}
    a {{
        text-decoration: none;
        padding: 3px 10px;
        border-radius: 6px;
        font-size: 0.9rem;
        border-style: solid;
        border-width: 1px;
        border-color: color(var(--foreground) alpha(0.14));
        background-color: color(var(--foreground) alpha(0.07));
        color: var(--foreground);
    }}
    a.active {{
        font-weight: bold;
        border-color: var(--foreground);
        background-color: var(--foreground);
        color: var(--background);
    }}
</style>
<a class="{unified}" href="unified">Unified</a>&nbsp;<a class="{split}" href="split">Split</a>
</body>
"""


def update_toggle(view: sublime.View) -> None:
    """Draw the two buttons on the first line of the view."""
    split = is_rendered(view)
    view.erase_phantoms(PHANTOM_KEY)
    view.add_phantom(
        PHANTOM_KEY,
        sublime.Region(0),
        TOGGLE_TEMPLATE.format(
            unified="" if split else "active",
            split="active" if split else "",
        ),
        sublime.LAYOUT_INLINE,
        lambda href: view.run_command("gs_diff_toggle_split", {"mode": href})
    )
    update_hunk_buttons(view)


# --- the buttons on every hunk ----------------------------------------------


HUNK_BUTTONS_TEMPLATE = """
<body id="git-savvy-hunk-buttons">
<style>
    a {{
        text-decoration: none;
        /* An annotation draws the text of a link 10px to the right of
           its box: what looks like 10px on either side is this. */
        padding: 1px 20px 1px 0;
        border-radius: 6px;
        font-size: 0.9rem;
        border-style: solid;
        border-width: 1px;
        border-color: color(var(--foreground) alpha(0.14));
        background-color: color(var(--foreground) alpha(0.07));
        color: var(--foreground);
    }}
    a.restore {{
        color: var(--redish);
    }}
</style>
{links}
</body>
"""
HUNK_ACTIONS = {
    # label, reset
    "stage": ("Stage", False),
    "restore": ("Restore", True),
    "unstage": ("Unstage", False),
}


def shows_hunk_buttons(view: sublime.View) -> bool:
    """Only the views of the working dir and of the index can stage."""
    settings = view.settings()
    return bool(
        settings.get("git_savvy.diff_view")
        and not settings.get("git_savvy.diff_view.disable_stage")
        and not settings.get("git_savvy.diff_view.base_commit")
        and not settings.get("git_savvy.diff_view.target_commit")
    )


def hunk_header_points(view: sublime.View) -> List[int]:
    """Where the hunk headers begin, in the order of the hunks."""
    if is_rendered(view):
        state = states.get(view.id())
        return [a for a, _ in state.hunk_regions] if state else []
    return [hunk.a for hunk in SplittedDiff.from_view(view).hunks]


def update_hunk_buttons(view: sublime.View) -> None:
    """Put the buttons at the right edge of every hunk header.

    Stage and Restore for the working dir, Unstage for the index.  For
    a combined diff only Stage: there the staging command stages the
    whole file, whatever it is asked to do.
    """
    if not shows_hunk_buttons(view):
        view.erase_regions(HUNK_BUTTONS_KEY)
        return

    if view.settings().get("git_savvy.diff_view.in_cached_mode"):
        actions = ["unstage"]
    elif not is_rendered(view) and SplittedDiff.from_view(view).is_combined_diff():
        actions = ["stage"]
    else:
        actions = ["stage", "restore"]

    points = hunk_header_points(view)
    annotations = [
        HUNK_BUTTONS_TEMPLATE.format(links=(
            # The width of an annotation leaves out the left edge of its
            # first link and the right edge of its last one; the characters
            # around the links keep both edges in.
            "&nbsp;"
            + "&nbsp;".join(
                '<a class="{0}" href="{0}:{1}">{2}</a>'.format(
                    action, idx, HUNK_ACTIONS[action][0]
                )
                for action in actions
            )
            + "&#8203;"
        ))
        for idx in range(len(points))
    ]
    # The bar Sublime draws before an annotation takes this color; make it
    # the background so that only the buttons show.
    background = view.style().get("background", "#00000000")
    view.add_regions(
        HUNK_BUTTONS_KEY,
        [view.line(pt) for pt in points],
        scope="",
        flags=(
            sublime.RegionFlags.DRAW_NO_FILL
            | sublime.RegionFlags.DRAW_NO_OUTLINE
            | sublime.RegionFlags.NO_UNDO
        ),
        annotations=annotations,
        annotation_color=background,
        on_navigate=lambda href: on_hunk_button(view, href),
    )


def on_hunk_button(view: sublime.View, href: str) -> None:
    """Run the staging command for the hunk whose button was clicked."""
    action, _, idx = href.partition(":")
    if action not in HUNK_ACTIONS or not idx.isdigit():
        return
    points = hunk_header_points(view)
    if int(idx) >= len(points):
        return

    view.sel().clear()
    view.sel().add(points[int(idx)])
    _, reset = HUNK_ACTIONS[action]
    view.run_command("gs_diff_stage_or_reset_hunk", {"reset": reset})


class gs_diff_toggle_split(TextCommand):

    """
    Toggle between the unified and the side by side view of the diff, or
    choose one of them with `mode`: "unified" or "split".
    """

    def run(self, edit: sublime.Edit, mode: Optional[str] = None) -> None:
        view = self.view
        settings = view.settings()
        if not settings.get("git_savvy.diff_view"):
            return

        split = (mode == "split") if mode else not wants_split(view)
        settings.set(SPLIT_SETTING, split)
        app_state.set(APP_STATE_KEY, split)
        holds.pop(view.id(), None)

        if view.id() not in last_drawn:
            view.run_command("gs_diff_refresh", {"sync": True})
            return

        if split:
            if not is_rendered(view) and not render(view):
                flash(view, "This diff can't be shown side by side.")
        elif is_rendered(view):
            if restore_unified(view) is None:
                view.run_command("gs_diff_refresh", {"sync": True})
                return
            prelude, diff_text = last_drawn[view.id()]
            intra_line_colorizer.annotate_intra_line_differences(view, diff_text, len(prelude))
        update_toggle(view)


class GsDiffSplitListener(EventListener):
    def on_close(self, view: sublime.View) -> None:
        forget(view)
