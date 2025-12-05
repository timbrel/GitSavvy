from functools import partial
from itertools import chain, dropwhile, takewhile
import re

import sublime
from .fns import flatten, pairwise, tail
from .text_helper import TextRange


from typing import Final, Iterator, List, NamedTuple, Optional, Tuple
from .types import LineNo


class SplittedDiff(NamedTuple):
    commits: Tuple['CommitHeader', ...]
    headers: Tuple['FileHeader', ...]
    hunks: Tuple['Hunk', ...]

    @classmethod
    def from_string(cls, text, offset=0):
        # type: (str, int) -> SplittedDiff
        commits, headers, hunks = [], [], []
        sections = (
            (match.group(1), match.start())
            for match in re.finditer(r'^(commit|diff|@@)', text, re.M)
        )
        for (id, start), (_, end) in pairwise(chain(sections, [('END', len(text) + 1)])):
            if id == "commit":
                commits.append(CommitHeader(text[start:end], start + offset, end + offset))
            elif id == "diff":
                headers.append(FileHeader(text[start:end], start + offset, end + offset))
            elif id == "@@":
                hunks.append(Hunk(text[start:end], start + offset, end + offset))
        return cls(
            tuple(commits),
            tuple(headers),
            tuple(hunks)
        )

    @classmethod
    def from_view(cls, view):
        # type: (sublime.View) -> SplittedDiff
        return cls.from_string(view.substr(sublime.Region(0, view.size())))

    def is_combined_diff(self):
        # type: () -> bool
        for head in self.headers:
            if head.first_line().startswith("diff --cc "):
                return True
        return False

    def head_and_hunk_for_pt(self, pt):
        # type: (int) -> Optional[Tuple[FileHeader, Hunk]]
        hunk = self.hunk_for_pt(pt)
        if hunk:
            return self.head_for_hunk(hunk), hunk
        else:
            return None

    def hunk_for_pt(self, pt):
        # type: (int) -> Optional[Hunk]
        for hunk in self.hunks:
            if hunk.a <= pt < hunk.b:
                return hunk
        else:
            return None

    def first_hunk_after_pt(self, pt):
        # type: (int) -> Optional[Hunk]
        for hunk in self.hunks:
            if hunk.a > pt:
                return hunk
        else:
            return None

    def head_for_pt(self, pt):
        # type: (int) -> Optional[FileHeader]
        for header in reversed(self.headers):
            if header.a <= pt:
                return header
        else:
            return None

    def head_for_hunk(self, hunk):
        # type: (Hunk) -> FileHeader
        return max(
            (header for header in self.headers if header.a < hunk.a),
            key=lambda h: h.a
        )

    def hunks_for_head(self, head):
        # type: (FileHeader) -> Iterator[Hunk]
        return takewhile(
            lambda x: isinstance(x, Hunk),
            tail(dropwhile(
                lambda x: x != head,
                sorted(self.headers + self.hunks, key=lambda x: x.a)  # type: ignore[arg-type]
            ))
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

    def commit_before_pt(self, pt):
        # type: (int) -> Optional[CommitHeader]
        for commit_header in reversed(self.commits):
            if commit_header.a <= pt:
                return commit_header
        else:
            return None

    def commit_hash_before_pt(self, pt):
        # type: (int) -> Optional[str]
        commit_header = self.commit_before_pt(pt)
        return commit_header.commit_hash() if commit_header else None


HEADER_TO_FILE_RE = re.compile(r'\+\+\+ b/(.+?)\t?$')


class CommitHeader(TextRange):
    def commit_hash(self):
        # type: () -> Optional[str]
        first_line = self.text[:self.text.index('\n')]
        if first_line.startswith('commit '):
            return first_line.split(' ')[1]
        return None


class FileHeader(TextRange):
    def to_filename(self):
        # type: () -> Optional[str]
        match = HEADER_TO_FILE_RE.search(self.text)
        if not match:
            return None

        return match.group(1)

    def first_line(self):
        # type: () -> str
        return self.text[:self.text.index('\n')]


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


SAFE_PARSE_HUNK_HEADER = re.compile(r"[-+](\d+)(?:,(\d+))?")


class UnsupportedCombinedDiff(RuntimeError):
    pass


class HunkHeader(TextRange):
    def to_line_start(self):
        # type: () -> LineNo
        """Extract the starting line at "b" encoded in the hunk header

        T.i. for "@@ -685,8 +686,14 @@ ..." extract the "686".
        """
        metadata = self.safely_parse_metadata()
        return metadata[-1][0]

    def parse(self):
        # type: () -> Tuple[LineNo, int, LineNo, int]
        """Extract the line start and length data for a normal patch.

        T.i. for "@@ -685,8 +686,14 @@ ..." extract `(685, 8, 686, 14)`.

        Raises `UnsupportedCombinedDiff` for cc diffs.
        """
        metadata = self.safely_parse_metadata()
        if len(metadata) > 2:
            raise UnsupportedCombinedDiff(self.text)
        assert len(metadata) == 2
        return tuple(flatten(metadata))  # type: ignore[return-value]

    def safely_parse_metadata(self):
        # type: () -> List[Tuple[LineNo, int]]
        """Extract all line start/length pairs from the hunk header

        T.i. for "@@ -685,8 +686,14 @@ ..." extract `[(685, 8), (686, 14)]`.

        We do not extract the `-+` signs.  All leading segments have a
        `-` sign, and the last segment has a `+`.
        """
        return [
            (int(start), int(length or "1"))
            for start, length in SAFE_PARSE_HUNK_HEADER.findall(
                self.text.lstrip("@").split("@", 1)[0]
            )
        ]


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
        return super().lines(factory=factory)  # type: ignore
