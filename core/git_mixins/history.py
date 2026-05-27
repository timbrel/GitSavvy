from __future__ import annotations
from dataclasses import dataclass
import email.utils
from itertools import chain
import os
from typing import Generic, Iterator, List, Literal, NamedTuple, Optional, overload, TypeVar
from typing_extensions import TypeAlias

from ..exceptions import GitSavvyError
from ...common import util
from GitSavvy.core.fns import last, pairwise, take
from GitSavvy.core.git_command import mixin_base
from GitSavvy.core.caches import Cache, cached
from GitSavvy.core.types import FullHash, FullPath, ShortHash, ShortPath


class LogEntry(NamedTuple):
    short_hash: ShortHash
    long_hash: FullHash
    ref: str
    summary: str
    raw_body: str
    author: str
    email: str
    datetime: str


class RefLogEntry(NamedTuple):
    short_hash: ShortHash
    long_hash: FullHash
    summary: str
    reflog_name: str
    reflog_selector: str
    author: str
    datetime: str


class CommitInfo(NamedTuple):
    short_hash: ShortHash
    subject: str
    date: str


@dataclass(frozen=True)
class FileStatus:
    mode: str
    from_path: ShortPath
    to_path: ShortPath | None = None


class FileHistoryEntry(NamedTuple):
    short_hash: ShortHash
    date: str
    subject: str
    status: Optional[FileStatus]


T = TypeVar("T", bound=Optional[str])
FileHistoryKey: TypeAlias = "tuple[str, T]"


class FileHistoryInfo(NamedTuple, Generic[T]):
    filename_at_commit: T
    previous_commit: ShortHash | None


class CommitHistoryInfo(NamedTuple):
    subject: str
    date: str


class FileHistoryCache(Cache):
    def __getitem__(self, key: FileHistoryKey[T]) -> FileHistoryInfo[T]:
        return super().__getitem__(key)

    def __setitem__(self, key: FileHistoryKey[T], value: FileHistoryInfo[T]) -> None:
        super().__setitem__(key, value)


CommitInfoCache: TypeAlias = "dict[str, CommitHistoryInfo]"
file_history_cache = FileHistoryCache(maxsize=8192)
commit_info_cache: CommitInfoCache = Cache(maxsize=8192)


def is_dynamic_ref(ref: Optional[str]) -> bool:
    return (
        not ref
        or ref == "HEAD"
        or ref.startswith(":")
        or ref.startswith("HEAD:")
    )


class HistoryMixin(mixin_base):

    def log(self, author=None, branch=None, file_path=None, start_end=None, cherry=None,
            limit=6000, skip=None, reverse=False, all_branches=False, msg_regexp=None,
            diff_regexp=None, first_parent=False, merges=False, no_merges=False, topo_order=False,
            follow=False, show_panel_on_error=True) -> List[LogEntry]:
        if follow and not file_path:
            raise RuntimeError("follow=True requires file_path")

        log_output = self.git(
            "log",
            "--max-count={}".format(limit) if limit else None,
            "--skip={}".format(skip) if skip else None,
            "--reverse" if reverse else None,
            r"--format=%h%n%H%n%D%n%s%n%an%n%ae%n%at%x00%B%x00%x00%n",  # `r"` to disable Sublime string highlighting
            "--author={}".format(author) if author else None,
            "--grep={}".format(msg_regexp) if msg_regexp else None,
            "--cherry" if cherry else None,
            "--G" if diff_regexp else None,
            diff_regexp if diff_regexp else None,
            "--first-parent" if first_parent else None,
            "--no-merges" if no_merges else None,
            "--merges" if merges else None,
            "--topo-order" if topo_order else None,
            "--follow" if follow else None,
            "--all" if all_branches else None,
            "{}..{}".format(*start_end) if start_end else None,
            branch if branch else None,
            "--" if file_path else None,
            file_path if file_path else None,
            show_panel_on_error=show_panel_on_error
        ).strip("\x00")

        entries = []
        for entry in log_output.split("\x00\x00\n"):
            entry = entry.strip()
            if not entry:
                continue
            entry, raw_body = entry.split("\x00")

            short_hash, long_hash, ref, summary, author, email, datetime = entry.split("\n")
            entries.append(LogEntry(short_hash, long_hash, ref, summary, raw_body, author, email, datetime))

        return entries

    def log_generator(self, limit=6000, **kwargs) -> Iterator[LogEntry]:
        # Generator for show_log_panel
        skip = 0
        while True:
            logs = self.log(limit=limit, skip=skip, **kwargs)
            yield from logs
            if len(logs) < limit:
                break
            skip += limit

    def reflog(self, limit=6000, skip=None, all_branches=False):
        log_output = self.git(
            "reflog",
            "-{}".format(limit),
            "--skip={}".format(skip) if skip else None,
            '--format=%h%n%H%n%s%n%gs%n%gd%n%an%n%at%x00%x00%n',
            "--all" if all_branches else None,
        ).strip("\x00")

        entries = []
        for entry in log_output.split("\x00\x00\n"):
            entry = entry.strip()
            if not entry:
                continue
            short_hash, long_hash, summary, reflog_name, reflog_selector, author, datetime = \
                entry.split("\n")
            entries.append(RefLogEntry(
                short_hash, long_hash, summary, reflog_name, reflog_selector, author, datetime))

        return entries

    def reflog_generator(self, limit=6000, skip=None):
        skip = 0
        while True:
            logs = self.reflog(limit=limit, skip=skip)
            if not logs:
                break
            for entry in logs:
                yield (["{} {}".format(entry.reflog_selector, entry.reflog_name),
                        "{} {}".format(entry.short_hash, entry.summary),
                        "{}, {}".format(entry.author, util.dates.fuzzy(entry.datetime))],
                       entry.long_hash)
            skip = skip + limit

    def log1(self, commit_hash):
        """
        Return a single LogEntry of a commit.
        """
        return self.log(start_end=("{0}~1".format(commit_hash), commit_hash), limit=1)[0]

    def log_merge(self, merge_hash):
        """
        Return all LogEntry of a merge.
        """
        ret = self.log(
            start_end=("{0}~1".format(merge_hash), merge_hash), topo_order=True, reverse=True)
        return ret[0:(len(ret) - 1)]

    def commits_of_merge(self, merge_hash):
        """
        Return commits of a merge.
        """
        return self.git(
            "rev-list", "--topo-order", "{0}~1..{0}".format(merge_hash)).strip().split("\n")[1:]

    def commit_parents(self, commit_hash):
        """
        Return parents of a commit.
        """
        return self.git("rev-list", "-1", "--parents", commit_hash).strip().split(" ")[1:]

    def commit_is_merge(self, commit_hash):
        sha = self.git("rev-list", "--merges", "-1", "{0}~1..{0}".format(commit_hash)).strip()
        return sha != ""

    def commit_is_ancestor_of_head(self, commit_hash):
        # type: (str) -> bool
        try:
            self.git_throwing_silently(
                "merge-base",
                "--is-ancestor",
                commit_hash,
                "HEAD",
            )
        except GitSavvyError:
            return False
        else:
            return True

    @overload
    def resolve(
        self,
        commitish: str,
        *,
        short: Literal[True],
        on_error: Literal["show_panel", "suppress_panel"] = "show_panel"
    ) -> ShortHash: ...

    @overload
    def resolve(
        self,
        commitish: str,
        *,
        short: Literal[True],
        on_error: Literal["ignore"]
    ) -> ShortHash | None: ...

    @overload
    def resolve(
        self,
        commitish: str,
        *,
        short: Literal[False] = False,
        on_error: Literal["show_panel", "suppress_panel"] = "show_panel"
    ) -> FullHash: ...

    @overload
    def resolve(
        self,
        commitish: str,
        *,
        short: Literal[False] = False,
        on_error: Literal["ignore"]
    ) -> FullHash | None: ...

    def resolve(self, commitish, *, short=False, on_error="show_panel"):
        resolved = self.git(
            "rev-parse",
            "--verify",
            "--short" if short else None,
            commitish,
            throw_on_error=on_error != "ignore",
            show_panel_on_error=on_error == "show_panel"
        ).strip()
        if on_error == "ignore":
            return resolved or None
        return resolved

    def resolve_commitish(self, ref: str) -> ShortHash:
        return self.resolve(ref, short=True)

    def get_short_hash(self, commit_hash):
        # type: (FullHash | ShortHash) -> ShortHash
        short_hash_length = self.current_state().get("short_hash_length")
        if short_hash_length:
            return commit_hash[:short_hash_length]

        short_hash = self.resolve(commit_hash, short=True)
        self.update_store({"short_hash_length": len(short_hash)})
        return short_hash

    def filename_at_commit(self, filename: FullPath, commit_hash: str) -> FullPath:
        if is_dynamic_ref(commit_hash):
            return self._filename_at_commit(filename, commit_hash)

        key = (commit_hash, filename)
        try:
            return file_history_cache[key].filename_at_commit
        except KeyError:
            self._fetch_info_for_commit_file_path_pairs(filename, commit_hash)
            return file_history_cache[key].filename_at_commit

    @cached(not_if={"commit_hash": is_dynamic_ref})
    def _filename_at_commit(self, filename: FullPath, commit_hash: str) -> FullPath:
        lines = self.git(
            "log",
            "--format=",  # we don't need any commit info beside the name status
            "--follow",
            "--name-status",
            "{}..".format(commit_hash),
            "--", filename
        ).strip().splitlines()

        try:
            return self.to_full_path(lines[-1].split("\t")[1])
        except IndexError:
            return filename

    def filename_at_head(self, filename: FullPath, commit_hash: str) -> FullPath:
        if os.path.exists(filename):
            return filename

        if not self.commit_is_ancestor_of_head(commit_hash):
            return filename

        current_filename = self.to_short_path(filename)
        seen = set()
        while current_filename not in seen:
            seen.add(current_filename)
            next_filename = self._next_filename_after_commit(current_filename, commit_hash)
            if not next_filename or next_filename == current_filename:
                break
            current_filename = next_filename

        return self.to_full_path(current_filename)

    def _next_filename_after_commit(self, filename: ShortPath, commit_hash: str) -> ShortPath | None:
        commit = self.git(
            "log",
            "--follow",
            "--format=%H",
            "--name-status",
            "-1",
            "-z",
            "{}..HEAD".format(commit_hash),
            "--",
            filename
        ).split("\0", 1)[0].strip()
        if not commit:
            return None

        return self._renamed_filename_at_commit(filename, commit)

    @cached(not_if={"commit_hash": is_dynamic_ref})
    def _renamed_filename_at_commit(self, filename: ShortPath, commit_hash: FullHash) -> ShortPath | None:
        name_status = self.git("show", "--name-status", "--format=", "-z", commit_hash)
        for file_status in parse_name_status_z(name_status):
            if file_status.mode.startswith("R") and file_status.from_path == filename:
                return file_status.to_path

        return None

    @cached(not_if={"base_commit": is_dynamic_ref, "target_commit": is_dynamic_ref})
    def list_touched_filenames(
        self,
        base_commit: Optional[str],
        target_commit: Optional[str],
        cached: Optional[bool] = None
    ) -> list[ShortPath]:
        return self.git(
            "diff",
            "--name-only",
            "--cached" if cached else None,
            base_commit,
            target_commit
        ).strip().splitlines()

    @cached(not_if={"commit_hash": is_dynamic_ref})
    def get_file_content_at_commit(self, filename, commit_hash):
        # type: (str, Optional[str]) -> str
        filename = self.to_short_path(filename)
        return self.git("show", "{}:{}".format(commit_hash or "", filename))

    def find_matching_lineno_in_file_history(
        self,
        base_commit: Optional[str],
        target_commit: Optional[str],
        line: int,
        file_path: FullPath
    ) -> int:
        """
        Return the target line while following renames for a HEAD-anchored file.

        `file_path` is the current/HEAD filename for the logical file history.
        `line` is in that file at `base_commit`, or in the working tree if
        `base_commit` is None.  The result is the corresponding line at
        `target_commit`, or in the working tree if `target_commit` is None.
        """
        target: tuple[Optional[str], str]
        if base_commit:
            base = (base_commit, self.filename_at_commit(file_path, base_commit))
            if target_commit:
                target = (target_commit, self.filename_at_commit(file_path, target_commit))
            else:
                target = (None, file_path)
            return self.find_matching_lineno_between_files(base, target, line)

        if target_commit:
            target = (target_commit, self.filename_at_commit(file_path, target_commit))
            return self.reverse_find_matching_lineno_between_files(
                target,
                (None, file_path),
                line
            )

        return line

    def find_matching_lineno(self, base_commit="HEAD", target_commit="HEAD", line=1, file_path=None):
        # type: (Optional[str], Optional[str], int, str) -> int
        """
        Return the matching line of the target_commit given the line number of the base_commit.
        """
        if not file_path:
            file_path = self.file_path

        diff = self.no_context_diff(base_commit, target_commit, file_path)
        return self.adjust_line_according_to_diff(diff, line)

    def find_matching_lineno_between_files(
        self,
        base: tuple[str, str],
        target: tuple[Optional[str], str],
        line: int
    ) -> int:
        """
        Return the matching line in target file for a line in base file.

        The target commit may be None to compare against the working tree.
        """
        diff = self.no_context_diff_between_files(base, target)
        return self.adjust_line_according_to_diff(diff, line)

    def reverse_find_matching_lineno(self, base_commit="HEAD", target_commit="HEAD", line=1, file_path=None):
        # type: (Optional[str], Optional[str], int, str) -> int
        """
        Return the matching line of the base_commit given the line number of the target_commit.
        """
        if not file_path:
            file_path = self.file_path

        diff = self.no_context_diff(base_commit, target_commit, file_path)
        return self.reverse_adjust_line_according_to_diff(diff, line)

    def reverse_find_matching_lineno_between_files(
        self,
        base: tuple[str, str],
        target: tuple[Optional[str], str],
        line: int
    ) -> int:
        """
        Return the matching line in base file for a line in target file.

        The target commit may be None to compare against the working tree.
        """
        diff = self.no_context_diff_between_files(base, target)
        return self.reverse_adjust_line_according_to_diff(diff, line)

    @cached(not_if={"base_commit": is_dynamic_ref, "target_commit": is_dynamic_ref})
    def no_context_diff(self, base_commit, target_commit, file_path=None):
        # type: (Optional[str], Optional[str], Optional[str]) -> str
        cmd = [
            "diff",
            "--no-color",
            "-U0",
            base_commit or "-R",
            target_commit,
        ]
        if file_path:
            cmd += ["--", file_path]

        return self.git(*cmd)

    @cached(
        not_if={
            "base": lambda ref: is_dynamic_ref(ref[0]),
            "target": lambda ref: is_dynamic_ref(ref[0])
        }
    )
    def no_context_diff_between_files(
        self,
        base: tuple[str, str],
        target: tuple[Optional[str], str]
    ) -> str:
        base_commit, base_file_path = base
        target_commit, target_file_path = target
        base_file_path = self.to_short_path(base_file_path)
        target_file_path = self.to_short_path(target_file_path)
        base_spec = "{}:{}".format(base_commit, base_file_path)
        if target_commit:
            target_spec = "{}:{}".format(target_commit, target_file_path)
            return self.git("diff", "--no-color", "-U0", base_spec, target_spec)
        return self.git("diff", "--no-color", "-U0", base_spec, "--", target_file_path)

    def adjust_line_according_to_diff(self, diff: str, line: int) -> int:
        hunks = util.parse_diff(diff)
        if not hunks:
            return line

        return self.adjust_line_according_to_hunks(hunks, line)

    def reverse_adjust_line_according_to_diff(self, diff: str, line: int) -> int:
        hunks = util.parse_diff(diff)
        if not hunks:
            return line

        return self.reverse_adjust_line_according_to_hunks(hunks, line)

    def adjust_line_according_to_hunks(self, hunks, line):
        for hunk in reversed(hunks):
            head_start = hunk.head_start if hunk.head_length else hunk.head_start + 1
            saved_start = hunk.saved_start if hunk.saved_length else hunk.saved_start + 1
            head_end = head_start + hunk.head_length
            saved_end = saved_start + hunk.saved_length

            if head_end <= line:
                return saved_end + line - head_end
            elif head_start <= line:
                return saved_start

        # fails to find matching
        return line

    def reverse_adjust_line_according_to_hunks(self, hunks, line):
        for hunk in reversed(hunks):
            head_start = hunk.head_start
            saved_start = hunk.saved_start
            if hunk.saved_length == 0:
                saved_start += 1
            elif hunk.head_length == 0:
                saved_start -= 1
            head_end = head_start + hunk.head_length
            saved_end = saved_start + hunk.saved_length

            if saved_end <= line:
                return head_end + line - saved_end
            elif saved_start <= line:
                return head_start

        # fails to find matching
        return line

    @cached(not_if={"commit_hash": is_dynamic_ref})
    def read_commit(
        self,
        commit_hash,
        file_path=None,
        show_diffstat=True,
        show_patch=True,
        ignore_whitespace=False
    ):
        # type: (str, Optional[str], bool, bool, bool) -> str
        stdout = self.git(
            "show",
            "--no-color",
            "--format=fuller",
            "--stat" if show_diffstat else None,
            "--ignore-all-space" if ignore_whitespace else None,
            "--patch" if show_patch else None,
            commit_hash,
            "--" if file_path else None,
            file_path if file_path else None,
            decode=False
        )
        try:
            rv = self.strict_decode(stdout)
        except UnicodeDecodeError:
            rv = "-- Partially decoded output; � denotes decoding errors --\n"
            rv += stdout.decode("utf-8", "replace")
        return rv

    def commit_subject_and_date(self, commit_hash: ShortHash, file_path: FullPath | None = None) -> CommitInfo:
        """

        Note: Providing `file_path` can affect the return value!
              Only use if you know that (commit_hash, file_path) is a valid pair
              to warm up the cache.
              E.g. the semantics of ("HEAD", <file_path>) is: return the CommitInfo
              of the *most recent* commit that change <file_path>.
              But ("HEAD", None) returns the CommitInfo of the HEAD commit.
        """

        def to_commit_info(info: CommitHistoryInfo) -> CommitInfo:
            return CommitInfo(
                commit_hash,
                info.subject,
                info.date
            )

        # `commit_hash` may be a ref (like "HEAD" or a branch name).
        # If so the key lookup will always fail since we cache by commit_hash,
        # resulting in a fresh fetch.
        try:
            return to_commit_info(commit_info_cache[commit_hash])
        except KeyError:
            hashes = self._fetch_info_for_commit_file_path_pairs(file_path, commit_hash)
            if not hashes:
                raise ValueError(
                    f"no history for {file_path!r} reachable from {commit_hash!r}"
                )
            return to_commit_info(commit_info_cache[hashes[0]])

    def commit_subject_and_date_from_patch(self, patch: str) -> CommitInfo:
        commit_hash, date, subject = "", "", ""
        for line in patch.splitlines():
            if line.startswith("commit "):
                # The commit line can include decorations we must split off!
                commit_hash = line[7:].split(" ", 1)[0]
            # CommitDate: Tue Dec 20 18:21:40 2022 +0100
            elif line.startswith("CommitDate: ") and (parsed_date := email.utils.parsedate(line[12:])):
                date = "-".join(map(str, parsed_date[:3]))
            elif line.startswith("    "):
                subject = line.lstrip()
                break
        return CommitInfo(self.get_short_hash(commit_hash), subject, date)

    def previous_commit(
        self,
        current_commit: str,
        file_path: str | None = None,
        follow: bool = False
    ) -> ShortHash | None:
        if file_path and not follow:
            return self._previous_commit(current_commit, file_path, follow)

        # `current_commit` may be a ref (like "HEAD" or a branch name).
        # If so the key lookup will always fail since we cache by commit_hash,
        # resulting in a fresh fetch.
        try:
            return file_history_cache[(current_commit, file_path)].previous_commit
        except KeyError:
            hashes = self._fetch_info_for_commit_file_path_pairs(file_path, current_commit)
            return (
                file_history_cache[(hashes[0], file_path)].previous_commit
                if hashes else
                None
            )

    @cached(not_if={"current_commit": is_dynamic_ref})
    def _previous_commit(self, current_commit, file_path=None, follow=False):
        # type: (str, Optional[str], bool) -> ShortHash | None
        return last(
            self._log_commits_linewise(current_commit, file_path, follow, limit=2),
            None
        )

    def recent_commit(
        self,
        current_commit: str,
        file_path: str | None = None,
        follow: bool = None,
    ) -> ShortHash | None:
        if file_path and follow is False:
            return self._recent_commit(current_commit, file_path, follow)

        return next(
            iter(self._fetch_info_for_commit_file_path_pairs(file_path, current_commit)),
            None
        )

    @cached(not_if={"current_commit": is_dynamic_ref})
    def _recent_commit(self, current_commit, file_path=None, follow=False):
        # type: (str, Optional[str], bool) -> ShortHash | None
        return last(
            self._log_commits_linewise(current_commit, file_path, follow, limit=1),
            None
        )

    def recent_commit_for_line_range(
        self,
        current_commit: str,
        file_path: FullPath,
        line_range: tuple[int, int],
        skip_current: bool = False
    ) -> ShortHash | None:
        current_commit = self.get_short_hash(current_commit)
        commits = self._log_commits_for_line_range(
            current_commit,
            file_path,
            line_range
        )
        return next(
            (commit for commit in commits if not skip_current or commit != current_commit),
            None
        )

    def next_commit(
        self,
        current_commit: str,
        file_path: str | None = None,
        follow: bool = False
    ) -> ShortHash | None:
        return last(
            self._log_commits_linewise(f"{current_commit}..", file_path, follow),
            None
        )

    def next_commits(
        self,
        current_commit: str,
        file_path: str | None = None,
        branch_hint: str | None = None,
    ) -> dict[ShortHash, ShortHash] | None:
        if current_commit != self.get_short_hash(current_commit):
            raise RuntimeError("`next_commits` must be called with a short commit hash.")

        if branch_hint is None:
            branch_hint = self.get_branch_hint_for_commit(current_commit)

        hashes = self._fetch_info_for_commit_file_path_pairs(
            file_path, start_commit=branch_hint, stop_at=current_commit
        )
        if not hashes or hashes[-1] != current_commit:
            return None

        return {
            right: left
            for left, right in pairwise(hashes)
        }

    def get_branch_hint_for_commit(self, commit_hash: str) -> str:
        try:
            return next(iter(
                self.git_throwing_silently(
                    "for-each-ref",
                    "--format=%(refname)",
                    "--contains",
                    commit_hash,
                    "--sort=-committerdate",
                    "--sort=-HEAD"
                ).strip().splitlines()
            ))
        except (GitSavvyError, StopIteration):
            if self.commit_is_ancestor_of_head(commit_hash):
                return ""
            else:
                raise ValueError(f"{commit_hash} seems orphaned")

    @cached(not_if={"current_commit": is_dynamic_ref})
    def _log_commits_for_line_range(
        self,
        current_commit: str,
        file_path: FullPath,
        line_range: tuple[int, int]
    ) -> list[ShortHash]:
        file_path_at_commit = self.filename_at_commit(file_path, current_commit)
        relative_path = self.to_short_path(file_path_at_commit)
        start_line, end_line = line_range
        output = self.git(
            "log",
            "--format=%x1e%h",
            "-2",
            f"-L{start_line},{end_line}:{relative_path}",
            current_commit
        )
        return [
            line.strip()
            for record in output.split("\x1e")
            if record and (line := record.splitlines()[0])
        ]

    def _log_commits_linewise(
        self,
        commitish: Optional[str],
        file_path: Optional[str],
        follow: bool,
        limit: Optional[int] = None
    ) -> Iterator[ShortHash]:
        if follow and not file_path:
            raise RuntimeError("follow=True requires file_path")

        return (
            line.strip()
            for line in self.git_streaming(
                "log",
                "--format=%h",
                "--topo-order",
                "--follow" if follow else None,
                None if limit is None else f"-{limit}",
                commitish,
                "--",
                file_path,
                show_panel_on_error=False
            )
        )

    def _fetch_info_for_commit_file_path_pairs(
        self,
        file_path: Optional[str] = None,
        start_commit: str = "HEAD",
        stop_at: Optional[str] = None,
        limit: int = 200,
        file_cache: FileHistoryCache = file_history_cache,
        commit_cache: CommitInfoCache = commit_info_cache,
    ) -> list[ShortHash]:
        """
        Populate file-history info for a single logical file history.

        The cache is filled with entries of this shape:

            (short_commit_hash, file_path) -> FileHistoryInfo(
                filename_at_commit,
                previous_commit,
                subject,
                date
            )

        If `file_path` is given, it must be the full name of the file at
        `start_commit`.  For the default `start_commit="HEAD"`, this means the
        full HEAD filename which is usually the checked-out filename.  The
        cache key keeps this anchor path for all entries.  In this mode,
        `filename_at_commit` is the full historical path for that same logical
        file at `short_commit_hash`, following renames backwards.

        If `file_path` is None, the cache is filled with commit-only entries:

            (short_commit_hash, None) -> FileHistoryInfo(
                None,
                previous_commit,
                subject,
                date
            )

        `previous_commit` is the next older commit in the fetched history if
        known, or None only if the fetched log reaches the initial revision.
        `date` is the committer date from `%ci`, normalized to the same year-month-day
        format that `commit_subject_and_date_from_patch` returns.

        If `stop_at` is given, fetch the chain from `start_commit` down to and
        including `stop_at` (regardless of `limit`).  In this mode the
        `file_cache` entry for `stop_at` itself is intentionally *not* written
        — we don't have its parent in this fetch, so its `previous_commit`
        would be a lie.  `commit_cache` is populated for every fetched commit
        including `stop_at` (subject/date don't depend on the parent).

        Returns the ordered list of short hashes that were fetched, newest to
        oldest.  Existing callers can ignore the return value.

        All hashes are short hashes.
        """
        RS = "%x1e"  # record separator
        US = "%x1f"  # unit separaor

        log_output = self.git(
            "log",
            "--topo-order",
            f"--format={RS}%h{US}%ci{US}%s",
            "-z",
            None if stop_at else f"-{limit + 1}",
            start_commit,
            f"^{stop_at}^@" if stop_at else None,
            *(
                "--follow",
                "--name-status",
                "--",
                file_path
            ) if file_path else ()
        )
        records = parse_file_history_log(log_output)
        pairs = pairwise(chain(records, [None]))
        if stop_at is None:
            pairs = take(limit, pairs)

        filename = file_path
        hashes: list[ShortHash] = []
        for record, right in pairs:
            assert record
            hashes.append(record.short_hash)
            commit_cache[record.short_hash] = CommitHistoryInfo(
                record.subject,
                record.date
            )
            if stop_at is not None and right is None:
                # `record` is `stop_at`; we don't have its parent in this
                # fetch, so writing `file_cache` would lie about
                # `previous_commit`.  Leave the file-cache entry off so a
                # subsequent `previous_commit(stop_at)` re-fetches from there.
                break
            file_cache[(record.short_hash, file_path)] = FileHistoryInfo(
                filename,
                right.short_hash if right else None
            )

            if status := record.status:
                filename = self.to_full_path(status.from_path)

        return hashes


def parse_file_history_log(output: str) -> Iterator[FileHistoryEntry]:
    for record in output.split("\x1e"):
        if not record:
            continue

        header, _, name_status = record.partition("\0")
        try:
            short_hash, committer_date, subject = header.split("\x1f", 2)
        except ValueError:
            continue

        yield FileHistoryEntry(
            short_hash,
            date_from_committer_date(committer_date),
            subject,
            next(parse_name_status_z(name_status), None)
        )


def parse_name_status_z(output: str) -> Iterator[FileStatus]:
    fields = output.rstrip("\0").split("\0")
    idx = 0
    while idx < len(fields):
        mode = fields[idx].strip()
        idx += 1
        if not mode:
            continue

        if mode.startswith(("R", "C")):
            yield FileStatus(mode, ShortPath(fields[idx]), ShortPath(fields[idx + 1]))
            idx += 2
        else:
            yield FileStatus(mode, ShortPath(fields[idx]))
            idx += 1


def date_from_committer_date(committer_date: str) -> str:
    date, _, _ = committer_date.partition(" ")
    try:
        year, month, day = date.split("-")
        return "-".join((str(int(year)), str(int(month)), str(int(day))))
    except ValueError:
        return date
