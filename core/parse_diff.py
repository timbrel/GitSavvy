from collections import namedtuple
from functools import partial
from itertools import chain, takewhile
import re

import sublime
from .fns import accumulate, pairwise


MYPY = False
if MYPY:
    from typing import Final, Iterator, List, NamedTuple, Optional, Tuple, Type


if MYPY:
    SplittedDiffBase = NamedTuple(
        'SplittedDiff', [
            ('commits', Tuple['CommitHeader', ...]),
            ('headers', Tuple['FileHeader', ...]),
            ('hunks', Tuple['Hunk', ...])
        ]
    )
else:
    SplittedDiffBase = namedtuple('SplittedDiff', 'commits headers hunks')


class SplittedDiff(SplittedDiffBase):
    @classmethod
    def from_string(cls, text, offset=0):
        # type: (str, int) -> SplittedDiff
        factories = {'commit': CommitHeader, 'diff': FileHeader, '@@': Hunk}
        containers = {'commit': [], 'diff': [], '@@': []}
        sections = (
            (match.group(1), match.start())
            for match in re.finditer(r'^(commit|diff|@@)', text, re.M)
        )
        for (id, start), (_, end) in pairwise(chain(sections, [('END', len(text) + 1)])):
            containers[id].append(factories[id](text[start:end], start + offset, end + offset))

        return cls(
            tuple(containers['commit']),
            tuple(containers['diff']),
            tuple(containers['@@'])
        )

    @classmethod
    def from_view(cls, view):
        # type: (sublime.View) -> SplittedDiff
        return cls.from_string(view.substr(sublime.Region(0, view.size())))

    def head_and_hunk_for_pt(self, pt):
        # type: (int) -> Optional[Tuple[FileHeader, Hunk]]
        for hunk in self.hunks:
            if hunk.a <= pt < hunk.b:
                break
        else:
            return None

        return self.head_for_hunk(hunk), hunk

    def head_for_hunk(self, hunk):
        # type: (Hunk) -> FileHeader
        return max(
            (header for header in self.headers if header.a < hunk.a),
            key=lambda h: h.a
        )

    def commit_for_hunk(self, hunk):
        # type: (Hunk) -> Optional[CommitHeader]
        try:
            return max(
                (commit for commit in self.commits if commit.a < hunk.a),
                key=lambda c: c.a
            )
        except ValueError:
            return None


HEADER_TO_FILE_RE = re.compile(r'\+\+\+ b/(.+)$')
HUNKS_LINES_RE = re.compile(r'@@*.+\+(\d+)(?:,\d+)? ')


class TextRange:
    def __init__(self, text, a=0, b=None):
        # type: (str, int, int) -> None
        if b is None:
            b = a + len(text)
        self.text = text  # type: Final[str]
        self.a = a  # type: Final[int]
        self.b = b  # type: Final[int]

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

    def region(self):
        # type: () -> Region
        return Region(self.a, self.b)

    def lines(self, _factory=None):
        # type: (Type[TextRange]) -> List[TextRange]
        factory = _factory or TextRange
        lines = self.text.splitlines(keepends=True)
        return [
            factory(line, *a_b)
            for line, a_b in zip(lines, pairwise(accumulate(map(len, lines), initial=self.a)))
        ]


class CommitHeader(TextRange):
    def commit_hash(self):
        # type: () -> Optional[str]
        first_line = self.text[:self.text.index('\n')]
        if first_line.startswith('commit '):
            return first_line[7:]
        return None


class FileHeader(TextRange):
    def from_filename(self):
        # type: () -> Optional[str]
        match = HEADER_TO_FILE_RE.search(self.text)
        if not match:
            return None

        return match.group(1)


class Hunk(TextRange):
    def mode_len(self):
        # type: () -> int
        return len(list(takewhile(lambda x: x == '@', self.text))) - 1

    def header(self):
        # type: () -> HunkHeader
        content_start = self.text.index('\n') + 1
        return HunkHeader(self.text[:content_start], self.a, self.a + content_start)

    def content(self):
        # type: () -> HunkContent
        content_start = self.text.index('\n') + 1
        return HunkContent(
            self.text[content_start:],
            self.a + content_start,
            self.b,
            self.mode_len()
        )


class HunkHeader(TextRange):
    def from_line_start(self):
        # type: () -> Optional[int]
        """Extract the starting line at "b" encoded in the hunk header

        T.i. for "@@ -685,8 +686,14 @@ ..." extract the "686".
        """
        match = HUNKS_LINES_RE.search(self.text)
        if not match:
            return None

        return int(match.group(1))


class HunkLine(TextRange):
    def __init__(self, text, a=0, b=None, mode_len=1):
        # type: (str, int, int, int) -> None
        super().__init__(text, a, b)
        self.mode_len = mode_len  # type: Final[int]

    def is_from_line(self):
        # type: () -> bool
        return '-' in self.mode

    def is_to_line(self):
        # type: () -> bool
        return '+' in self.mode

    @property
    def mode(self):
        # type: () -> str
        return self.text[:self.mode_len]

    @property
    def content(self):
        # type: () -> str
        return self.text[self.mode_len:]

    def is_context(self):
        return self.mode.strip() == ''

    def is_no_newline_marker(self):
        return self.text.strip() == "\\ No newline at end of file"


class HunkContent(TextRange):
    def __init__(self, text, a=0, b=None, mode_len=1):
        # type: (str, int, int, int) -> None
        super().__init__(text, a, b)
        self.mode_len = mode_len  # type: Final[int]

    def lines(self):  # type: ignore
        # type: () -> List[HunkLine]
        factory = partial(HunkLine, mode_len=self.mode_len)
        return super().lines(_factory=factory)  # type: ignore


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

    def transpose(self, n):
        # type: (int) -> Region
        return Region(self.a + n, self.b + n)

    def as_slice(self):
        # type: () -> slice
        return slice(self.a, self.b)
