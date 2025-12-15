from __future__ import annotations

from typing import Final, Iterator, List, Tuple, Type, Union, TYPE_CHECKING

import sublime

from .fns import accumulate

if TYPE_CHECKING:
    from typing_extensions import Self


class TextRange:
    def __init__(self, text, a=0, b=None):
        # type: (str, int, int) -> None
        if b is None:
            b = a + len(text)
        self.text = text  # type: Final[str]
        self.a = a  # type: Final[int]
        self.b = b  # type: Final[int]

    def __repr__(self):
        return '{}(text="{}", a={}, b={})'.format(
            self.__class__.__name__,
            self.text[:20] + ("..." if len(self.text) > 20 else ""),
            self.a,
            self.b
        )

    def _as_tuple(self):
        # type: () -> Tuple[str, int, int]
        return (self.text, self.a, self.b)

    def __hash__(self):
        # type: () -> int
        return hash(self._as_tuple())

    def __eq__(self, other):
        # type: (object) -> bool
        if isinstance(other, TextRange):
            return self._as_tuple() == other._as_tuple()
        return False

    def __add__(self, other):
        # type: (int) -> TextRange
        return self.__class__(self.text, self.a + other, self.b + other)

    def __sub__(self, other):
        # type: (int) -> TextRange
        return self.__class__(self.text, self.a - other, self.b - other)

    def __getitem__(self, i):
        # type: (Union[int, slice]) -> Self
        return self.__class__(self.text[i], *self.region()[i])

    def __len__(self):
        return len(self.text)

    def region(self):
        # type: () -> Region
        return Region(self.a, self.b)

    def lines(self, factory=None, keepends=True):
        # type: (Type[TextRange], bool) -> List[TextRange]
        factory_ = factory or TextRange
        lines = self.text.splitlines(keepends=True)
        return [
            factory_(line if keepends else line.rstrip("\n"), a)
            for line, a in zip(lines, accumulate(map(len, lines), initial=self.a))
        ]


def line_from_pt(view, pt):
    # type: (sublime.View, Union[sublime.Point, sublime.Region]) -> TextRange
    line_span = view.line(pt)
    line_text = view.substr(line_span)
    return TextRange(line_text, line_span.a, line_span.b)


class Region(sublime.Region):
    def __hash__(self):
        # type: () -> int
        return hash((self.a, self.b))

    def __iter__(self):
        # type: () -> Iterator[int]
        return iter((self.a, self.b))

    def __add__(self, other):
        # type: (int) -> Region
        return self.transpose(other)

    def __sub__(self, other):
        # type: (int) -> Region
        return self.transpose(-other)

    def __getitem__(self, i):
        # type: (Union[int, slice]) -> Self
        if isinstance(i, int):
            i = slice(i, i + 1)
        new_range = range(self.a, self.b)[i]
        return self.__class__(new_range.start, new_range.stop)

    def transpose(self, n):
        # type: (int) -> Region
        return Region(self.a + n, self.b + n)

    def as_slice(self):
        # type: () -> slice
        return slice(self.a, self.b)
