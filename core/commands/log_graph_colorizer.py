import sublime

MYPY = False
if MYPY:
    from typing import Callable, Dict, Iterator, Tuple, TypeVar

    T = TypeVar('T')

    Point = int
    RowCol = Tuple[int, int]
    View = sublime.View
    Region = sublime.Region
    NextFn = Callable[['GraphChar'], Iterator['GraphChar']]

COMMIT_NODE_CHAR = '●'


registered_handlers = {}  # type: Dict[str, NextFn]


def handles(ch):
    # type: (str) -> Callable[[T], T]
    def decorator(fn):
        if ch in registered_handlers:
            raise RuntimeError('{} already has a handler registered'.format(ch))
        registered_handlers[ch] = fn
        return fn

    return decorator


class GraphChar:
    def __init__(self, view, pt):
        # type: (View, Point) -> None
        self.view = view
        self.pt = pt

    def go(self, rel_rowcol):
        # type: (RowCol) -> GraphChar
        row, col = self.view.rowcol(self.pt)
        drow, dcol = rel_rowcol
        next_row, next_col = row + drow, col + dcol
        next_pt = self.view.text_point(next_row, next_col)
        if self.view.rowcol(next_pt) != (next_row, next_col):
            return NullChar

        return GraphChar(self.view, next_pt)

    def region(self):
        # type: () -> Region
        return sublime.Region(self.pt, self.pt + 1)

    def char(self):
        # type: () -> str
        return self.view.substr(self.region())

    def __str__(self):
        # type: () -> str
        return self.char()

    def __hash__(self):
        # type: () -> int
        return hash((self.view.id(), self.pt))

    def __eq__(self, rhs):
        # type: (object) -> bool
        if isinstance(rhs, str):
            return self.char() == rhs
        if isinstance(rhs, self.__class__):
            return hash(self) == hash(rhs)
        return NotImplemented

    @property
    def e(self):
        # type: () -> GraphChar
        return self.go((0, 1))

    @property
    def se(self):
        # type: () -> GraphChar
        return self.go((1, 1))

    @property
    def s(self):
        # type: () -> GraphChar
        return self.go((1, 0))

    @property
    def sw(self):
        # type: () -> GraphChar
        return self.go((1, -1))

    @property
    def w(self):
        # type: () -> GraphChar
        return self.go((0, -1))


class NullChar_(GraphChar):
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


def contains(next_char, test):
    # type: (GraphChar, str) -> Iterator[GraphChar]
    if str(next_char) in test:
        yield next_char


def follow_path(dot):
    # type: (GraphChar) -> Iterator[GraphChar]
    for c in follow_char(dot):
        # print('{} -> {}'.format(dot, c))
        yield c
        if c != COMMIT_NODE_CHAR:
            yield from follow_path(c)


def follow_char(char):
    # type: (GraphChar) -> Iterator[GraphChar]
    fn = registered_handlers.get(char.char(), follow_none)
    yield from fn(char)


@handles(COMMIT_NODE_CHAR)
def follow_dot(char):
    # type: (GraphChar) -> Iterator[GraphChar]
    yield from contains(char.e, '-')
    yield from contains(char.s, '|' + COMMIT_NODE_CHAR)
    yield from contains(char.sw, '/')
    yield from contains(char.se, '\\')


@handles('|')
def follow_vertical_bar(char):
    yield from contains(char.s, '|' + COMMIT_NODE_CHAR)
    if char.e != '/' and char.e != '_':
        yield from contains(char.sw, '/')
    yield from contains(char.se, '\\')


@handles('\\')
def follow_backslash(char):
    yield from contains(char.s, '/')
    yield from contains(char.se, '\\|' + COMMIT_NODE_CHAR)


@handles('/')
def follow_forwardslash(char):
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


@handles('_')
def follow_underscore(char):
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


@handles('-')
def follow_horizontal_bar(char):
    # Multi merge octopoi
    # *---.
    # | \  \
    yield from contains(char.e, '-.')
    yield from contains(char.se, '\\')


@handles('.')
def follow_point(char):
    yield from contains(char.se, '\\')


@handles(' ')
def follow_none(char):
    # type: (GraphChar) -> Iterator[GraphChar]
    return iter([])
