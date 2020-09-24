from functools import lru_cache

import sublime

MYPY = False
if MYPY:
    from typing import Callable, Dict, Final, Iterator, List, Literal, Tuple, TypeVar

    T = TypeVar('T')

    Point = int
    RowCol = Tuple[int, int]
    View = sublime.View
    Region = sublime.Region
    NextFn = Callable[['Char'], Iterator['Char']]
    Direction = Literal["down", "up"]


COMMIT_NODE_CHAR = '●'


class Char:
    """Represents the char on the right of a cursor.

    Given a `view` and a `pt` (cursor offset) `Char` represents the
    character at the right. This is also known as a block cursor.
    You can extract the 'block' via `self.region()`, and read the
    actual char using `self.char()`.

    From a char you can peek around in all directions: south, east etc.
    but we use short abbreviations `s`, `e` ...
    Note that this is not the same as moving left or right. The
    intuition of moving left is e.g. that if you're at the beginning of
    a line (bol), you end up on the eol of the previous line etc.
    In contrast, looking `w`(est) returns a `NullChar`.

    We use `NullChar` for plain `None` for easy traversal. E.g.

        self.w.w.sw

    should just work without throwing.


    """
    def __init__(self, view, pt):
        # type: (View, Point) -> None
        self.view = view  # type: Final[View]
        self.pt = pt  # type: Final[Point]
        self._hash_val = hash((view.id(), view.change_count(), pt))  # type: Final[int]

    def go(self, rel_rowcol):
        # type: (RowCol) -> Char
        row, col = self.view.rowcol(self.pt)
        drow, dcol = rel_rowcol
        next_row, next_col = row + drow, col + dcol
        next_pt = self.view.text_point(next_row, next_col)
        if self.view.rowcol(next_pt) != (next_row, next_col):
            return NullChar

        return Char(self.view, next_pt)

    def region(self):
        # type: () -> Region
        return sublime.Region(self.pt, self.pt + 1)

    def char(self):
        # type: () -> str
        return self.view.substr(self.region())

    def __str__(self):
        # type: () -> str
        return self.char()

    def __repr__(self):
        return "Char({})".format(self.pt)

    def __hash__(self):
        # type: () -> int
        return self._hash_val

    def __eq__(self, rhs):
        # type: (object) -> bool
        if isinstance(rhs, self.__class__):
            return hash(self) == hash(rhs)
        if isinstance(rhs, str):
            return self.char() == rhs
        return NotImplemented

    @property
    def n(self):
        # type: () -> Char
        return self.go((-1, 0))

    @property
    def e(self):
        # type: () -> Char
        return self.go((0, 1))

    @property
    def se(self):
        # type: () -> Char
        return self.go((1, 1))

    @property
    def s(self):
        # type: () -> Char
        return self.go((1, 0))

    @property
    def sw(self):
        # type: () -> Char
        return self.go((1, -1))

    @property
    def w(self):
        # type: () -> Char
        return self.go((0, -1))


class NullChar_(Char):
    def __init__(self):
        # type: () -> None
        ...

    def __hash__(self):
        # type: () -> int
        return hash(None)

    def char(self):
        # type: () -> str
        return ' '

    def go(self, rowcol):
        # type: (RowCol) -> NullChar_
        return self


NullChar = NullChar_()
down_handlers = {}  # type: Dict[str, NextFn]
up_handlers = {}  # type: Dict[str, NextFn]


# Notes:
# - We want `follow_char` to be a polymorphic fn. For that we register
# a handler for each valid graph char using `follow`.
#
# - The following implementation for traversing a drawn graph uses
# `Iterables` (and e.g. `yield from`) to avoid `None` checks everywhere
# or extensive list concats.
#
# - Following a drawing of a graph doesn't follow an algorithm. The
# code is straight forward, and you basically have to look at some
# graphs (turn block cursor on in Sublime!) and peek around.

def follow(ch, direction):
    # type: (str, Direction) -> Callable[[NextFn], NextFn]
    def decorator(fn):
        # type: (NextFn) -> NextFn
        registry = down_handlers if direction == "down" else up_handlers
        if ch in registry:
            raise RuntimeError('{} already has a handler registered'.format(ch))
        registry[ch] = fn
        return fn

    return decorator


@lru_cache(maxsize=64)
def follow_path_down(dot):
    # type: (Char) -> List[Char]
    return list(_follow_path(dot, "down"))


@lru_cache(maxsize=64)
def follow_path_up(dot):
    # type: (Char) -> List[Char]
    return list(_follow_path(dot, "up"))


def _follow_path(dot, direction):
    # type: (Char, Direction) -> Iterator[Char]
    for c in follow_char(dot, direction):
        # print('{} -> {}'.format(dot, c))
        yield c
        if c != COMMIT_NODE_CHAR:
            yield from _follow_path(c, direction)


def follow_char(char, direction):
    # type: (Char, Direction) -> Iterator[Char]
    registry = down_handlers if direction == "down" else up_handlers
    fn = registry.get(char.char(), follow_none)
    yield from fn(char)


def contains(next_char, test):
    # type: (Char, str) -> Iterator[Char]
    if str(next_char) in test:
        yield next_char


@follow(COMMIT_NODE_CHAR, "down")
def after_dot(char):
    # type: (Char) -> Iterator[Char]
    yield from contains(char.sw, '/')
    yield from contains(char.s, '|' + COMMIT_NODE_CHAR)
    yield from contains(char.se, '\\')
    yield from contains(char.e, '-')


@follow(COMMIT_NODE_CHAR, "up")
def before_dot(char):
    # type: (Char) -> Iterator[Char]
    yield from contains(char.w, '-')
    yield from contains(char.n, '|' + COMMIT_NODE_CHAR)
    yield from contains(char.n.e, '/')
    yield from contains(char.n.w, '\\')


@follow('|', "down")
def after_vertical_bar(char):
    # type: (Char) -> Iterator[Char]
    yield from contains(char.s, '|/' + COMMIT_NODE_CHAR)

    # Check crossing line before following '/'
    # | |/ / /
    # |/| | |
    #   ^
    # Or:
    # | |_|/
    # |/| |
    if char.e != '/' and char.e != '_':
        yield from contains(char.sw, '/')
    yield from contains(char.se, '\\')


@follow('|', "up")
def before_vertical_bar(char):
    # type: (Char) -> Iterator[Char]
    yield from contains(char.n, '|' + COMMIT_NODE_CHAR)
    if char.w != '/' and char.n.w != '_':
        yield from contains(char.n.e, '/')
    yield from contains(char.n.w, '\\')


@follow('\\', "down")
def after_backslash(char):
    # type: (Char) -> Iterator[Char]
    yield from contains(char.s, '/')
    yield from contains(char.se, '\\|' + COMMIT_NODE_CHAR)


@follow('\\', "up")
def before_backslash(char):
    # type: (Char) -> Iterator[Char]
    # Don't forget multi merge octopoi
    # *---.
    # | \  \
    yield from contains(char.n.w, '\\.|-' + COMMIT_NODE_CHAR)


@follow('/', "down")
def after_forwardslash(char):
    # type: (Char) -> Iterator[Char]
    yield from contains(char.w, '_')

    # Crossing lines
    #  |_|_|/
    # /| | |
    #       ^
    if char.w == '|' and char.w.w == '_':
        yield from contains(char.w.w, '_')
    elif char.w == '|' and char.w.sw == '/':
        # Crossing lines
        #  |/
        # /|
        #   ^
        yield from contains(char.w.sw, '/')
    else:
        yield from contains(char.sw, '/|' + COMMIT_NODE_CHAR)


@follow('/', "up")
def before_forwardslash(char):
    # type: (Char) -> Iterator[Char]
    yield from contains(char.n.e.e, '_')
    yield from contains(char.n.e.e, '/')
    if char.n.e.e != '_' and char.n.e.e != '/':
        yield from contains(char.n.e, '/|' + COMMIT_NODE_CHAR)
    yield from contains(char.n, '|\\')


@follow('_', "down")
def after_underscore(char):
    # type: (Char) -> Iterator[Char]
    yield from contains(char.w, '_')
    if char.w == '|':
        # Crossing lines
        #  |_|_|/
        # /| | |
        #     ^
        yield from contains(char.w.w, '_')
        # Crossing lines
        # | |_|_|/
        # |/| | |
        #    ^
        yield from contains(char.sw.w, '/')
    elif char.sw == '/':
        # Crossing lines
        # | _|_|/
        # |/ | |
        #   ^
        yield from contains(char.sw, '/')


@follow('_', "up")
def before_underscore(char):
    # type: (Char) -> Iterator[Char]
    yield from contains(char.e.e, '_')
    yield from contains(char.e.e, '/')


@follow('-', "down")
def after_horizontal_bar(char):
    # type: (Char) -> Iterator[Char]
    # Multi merge octopoi
    # *---.
    # | \  \
    yield from contains(char.e, '-.')
    yield from contains(char.se, '\\')


@follow('-', "up")
def before_horizontal_bar(char):
    yield from contains(char.w, '-' + COMMIT_NODE_CHAR)


@follow('.', "down")
def after_point(char):
    # type: (Char) -> Iterator[Char]
    yield from contains(char.se, '\\')


@follow('.', "up")
def before_point(char):
    # type: (Char) -> Iterator[Char]
    yield from contains(char.w, '-')


@follow(' ', "down")
@follow(' ', "up")
def follow_none(char):
    # type: (Char) -> Iterator[Char]
    return iter([])
