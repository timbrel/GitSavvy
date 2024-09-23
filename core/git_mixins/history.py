from __future__ import annotations
import email.utils
from itertools import chain, takewhile

from ..exceptions import GitSavvyError
from ...common import util
from GitSavvy.core.fns import last, pairwise
from GitSavvy.core.git_command import mixin_base
from GitSavvy.core.utils import cached


from typing import Iterator, List, NamedTuple, Optional


class LogEntry(NamedTuple):
    short_hash: str
    long_hash: str
    ref: str
    summary: str
    raw_body: str
    author: str
    email: str
    datetime: str


class RefLogEntry(NamedTuple):
    short_hash: str
    long_hash: str
    summary: str
    reflog_name: str
    reflog_selector: str
    author: str
    datetime: str


class CommitInfo(NamedTuple):
    commit_hash: str
    short_hash: str
    subject: str
    date: str


def is_dynamic_ref(ref):
    # type: (Optional[str]) -> bool
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
            follow=False) -> List[LogEntry]:

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
            file_path if file_path else None
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

    def get_short_hash(self, commit_hash):
        # type: (str) -> str
        short_hash_length = self.current_state().get("short_hash_length")
        if short_hash_length:
            return commit_hash[:short_hash_length]

        short_hash = self.git("rev-parse", "--short", commit_hash).strip()
        self.update_store({"short_hash_length": len(short_hash)})
        return short_hash

    def resolve_commitish(self, ref: str) -> str:
        return self.git("rev-parse", "--short", ref).strip()

    def filename_at_commit(self, filename, commit_hash):
        # type: (str, str) -> str
        lines = self.git(
            "log",
            "--format=",  # we don't need any commit info beside the name status
            "--follow",
            "--name-status",
            "{}..".format(commit_hash),
            "--", filename
        ).strip().splitlines()

        try:
            return lines[-1].split("\t")[1]
        except IndexError:
            return filename

    @cached(not_if={"base_commit": is_dynamic_ref, "target_commit": is_dynamic_ref})
    def list_touched_filenames(self, base_commit, target_commit, cached=None):
        # type: (Optional[str], Optional[str], Optional[bool]) -> List[str]
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
        filename = self.get_rel_path(filename)
        return self.git("show", "{}:{}".format(commit_hash or "", filename))

    def find_matching_lineno(self, base_commit="HEAD", target_commit="HEAD", line=1, file_path=None):
        # type: (Optional[str], Optional[str], int, str) -> int
        """
        Return the matching line of the target_commit given the line number of the base_commit.
        """
        if not file_path:
            file_path = self.file_path

        diff = self.no_context_diff(base_commit, target_commit, file_path)
        return self.adjust_line_according_to_diff(diff, line)

    def reverse_find_matching_lineno(self, base_commit="HEAD", target_commit="HEAD", line=1, file_path=None):
        # type: (Optional[str], Optional[str], int, str) -> int
        """
        Return the matching line of the base_commit given the line number of the target_commit.
        """
        if not file_path:
            file_path = self.file_path

        diff = self.no_context_diff(base_commit, target_commit, file_path)
        hunks = util.parse_diff(diff)
        if not hunks:
            return line
        return self.reverse_adjust_line_according_to_hunks(hunks, line)

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

    def adjust_line_according_to_diff(self, diff, line):
        # type: (str, int) -> int
        hunks = util.parse_diff(diff)
        if not hunks:
            return line

        return self.adjust_line_according_to_hunks(hunks, line)

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

    def commit_subject_and_date(self, commit_hash: str) -> CommitInfo:
        # call with the same settings as gs_show_commit to either use or
        # warm up the cache
        show_diffstat = self.savvy_settings.get("show_diffstat")
        patch = self.read_commit(commit_hash, show_diffstat=show_diffstat)
        return self.commit_subject_and_date_from_patch(patch)

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
        return CommitInfo(commit_hash, self.get_short_hash(commit_hash), subject, date)

    @cached(not_if={"current_commit": is_dynamic_ref})
    def previous_commit(self, current_commit, file_path=None, follow=False):
        # type: (str, Optional[str], bool) -> Optional[str]
        return last(
            self._log_commits(current_commit, file_path, follow, limit=2),
            None
        )

    @cached(not_if={"current_commit": is_dynamic_ref})
    def recent_commit(self, current_commit, file_path=None, follow=False):
        # type: (str, Optional[str], bool) -> Optional[str]
        return last(
            self._log_commits(current_commit, file_path, follow, limit=1),
            None
        )

    def next_commit(self, current_commit, file_path=None, follow=False):
        # type: (str, Optional[str], bool) -> Optional[str]
        return last(
            self._log_commits(f"{current_commit}..", file_path, follow),
            None
        )

    def next_commits(
        self,
        current_commit: str,
        file_path: str | None = None,
        follow: bool = False,
        branch_hint: str | None = None,
    ) -> dict[str, str]:
        if current_commit != self.get_short_hash(current_commit):
            raise RuntimeError("`next_commits` must be called with a short commit hash.")

        if branch_hint is None:
            try:
                branch_hint = next(iter(
                    self.git_throwing_silently(
                        "for-each-ref",
                        "--format=%(refname)",
                        "--contains",
                        current_commit,
                        "--sort=-committerdate",
                        "--sort=-HEAD"
                    ).strip().splitlines()
                ))
            except (GitSavvyError, StopIteration):
                if self.commit_is_ancestor_of_head(current_commit):
                    branch_hint = ""
                else:
                    raise ValueError(f"{current_commit} seems orphaned")

        return {
            right: left
            for left, right in pairwise(chain(
                takewhile(
                    lambda c: c != current_commit,
                    self._log_commits(f"{branch_hint}", file_path, follow)
                ),
                [current_commit]
            ))
        }

    def _log_commits(
        self,
        commitish: Optional[str],
        file_path: Optional[str],
        follow: bool,
        limit: Optional[int] = None
    ) -> List[str]:
        return self.git_throwing_silently(
            "log",
            "--format=%h",
            "--topo-order",
            "--follow" if follow else None,
            None if limit is None else f"-{limit}",
            commitish,
            "--",
            file_path
        ).strip().splitlines()
