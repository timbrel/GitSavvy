from collections import namedtuple
import os
import re
import string

from ..constants import MERGE_CONFLICT_PORCELAIN_STATUSES


MYPY = False
if MYPY:
    from GitSavvy.core.git_command import (
        HistoryMixin,
        _GitCommand,
    )

    class mixin_base(
        HistoryMixin,
        _GitCommand,
    ):
        pass

else:
    mixin_base = object

FileStatus = namedtuple("FileStatus", ("path", "path_alt", "index_status", "working_status"))

MYPY = False
if MYPY:
    from typing import List, Tuple, Union
    HeadState = Tuple


class StatusMixin(mixin_base):

    def _get_status(self):
        # type: () -> List[str]
        return self.git(
            "status",
            "--porcelain",
            "-z",
            "-b",
            custom_environ={"GIT_OPTIONAL_LOCKS": "0"}
        ).rstrip("\x00").split("\x00")

    def _parse_status_for_file_statuses(self, lines):
        # type: (List[str]) -> List[FileStatus]
        porcelain_entries = lines[1:].__iter__()
        entries = []

        for entry in porcelain_entries:
            if not entry:
                continue
            index_status = entry[0]
            working_status = entry[1].strip() or None
            path = entry[3:]
            path_alt = porcelain_entries.__next__() if index_status in ["R", "C"] else None
            entries.append(FileStatus(path, path_alt, index_status, working_status))

        return entries

    def get_status(self):
        # type: () -> List[FileStatus]
        """
        Return a list of FileStatus objects.  These objects correspond
        to all files that are 1) staged, 2) modified, 3) new, 4) deleted,
        5) renamed, or 6) copied as well as additional status information that can
        occur mid-merge.
        """

        lines = self._get_status()
        return self._parse_status_for_file_statuses(lines)

    def group_status_entries(self, file_status_list):
        # type: (List[FileStatus]) -> Tuple[List[FileStatus], ...]
        """
        Take entries from `git status` and sort them into groups.
        """
        staged, unstaged, untracked, conflicts = [], [], [], []

        for f in file_status_list:
            if (f.index_status, f.working_status) in MERGE_CONFLICT_PORCELAIN_STATUSES:
                conflicts.append(f)
                continue
            if f.index_status == "?":
                untracked.append(f)
                continue
            elif f.working_status in ("M", "D", "T", "A"):
                unstaged.append(f)
            if f.index_status != " ":
                staged.append(f)

        return staged, unstaged, untracked, conflicts

    def get_branch_status(self, delim=None):
        # type: (str) -> Union[str, Tuple[str, List[str]]]
        """
        Return a tuple of:

          1) the name of the active branch
          2) the status of the active local branch
             compared to its remote counterpart.

        If no remote or tracking branch is defined, do not include remote-data.
        If HEAD is detached, provide that status instead.

        If a delimeter is provided, join tuple components with it, and return
        that value.
        """
        lines = self._get_status()
        branch_status = self._get_branch_status_components(lines)
        return self._format_branch_status(branch_status, delim)

    def _get_branch_status_components(self, lines):
        # type: (List[str]) -> HeadState
        """
        Return a tuple of:

          0) boolean indicating whether repo is in detached state
          1) active branch name
          2) remote branch name
          3) boolean indicating whether branch is clean
          4) # commits ahead of remote
          5) # commits behind of remote
          6) boolean indicating whether the remote branch is gone
        """

        first_line, *addl_lines = lines
        # Any additional lines will mean files have changed or are untracked.
        clean = len(addl_lines) == 0

        if first_line.startswith("## HEAD (no branch)"):
            return True, None, None, clean, None, None, False

        if (
            first_line.startswith("## No commits yet on ")
            # older git used these
            or first_line.startswith("## Initial commit on ")
        ):
            first_line = first_line[:3] + first_line[21:]

        valid_punctuation = "".join(c for c in string.punctuation if c not in "~^:?*[\\")
        branch_pattern = "[A-Za-z0-9" + re.escape(valid_punctuation) + "\u263a-\U0001f645]+?"
        branch_suffix = r"( \[((ahead (\d+))(, )?)?(behind (\d+))?(gone)?\])?)"
        short_status_pattern = "## (" + branch_pattern + r")(\.\.\.(" + branch_pattern + ")" + branch_suffix + "?$"
        status_match = re.match(short_status_pattern, first_line)

        if not status_match:
            return False, None if clean else addl_lines[0], None, clean, None, None, False

        branch, _, remote, _, _, _, ahead, _, _, behind, gone = status_match.groups()

        return False, branch, remote, clean, ahead, behind, bool(gone)

    def _format_branch_status(self, branch_status, delim=None):
        # type: (HeadState, str) -> Union[str, Tuple[str, List[str]]]
        detached, branch, remote, clean, ahead, behind, gone = branch_status

        secondary = []

        if detached:
            status = "HEAD is in a detached state."

        else:
            tracking = " tracking `{}`".format(remote)
            status = "On branch `{}`{}.".format(branch, tracking if remote else "")

            if ahead and behind:
                secondary.append("You're ahead by {} and behind by {}.".format(ahead, behind))
            elif ahead:
                secondary.append("You're ahead by {}.".format(ahead))
            elif behind:
                secondary.append("You're behind by {}.".format(behind))
            elif gone:
                secondary.append("The remote branch is gone.")

        if self.in_merge():
            secondary.append("Merging {}.".format(self.merge_head()))

        if self.in_rebase():
            secondary.append("Rebasing {}.".format(self.rebase_branch_name()))

        if delim:
            return delim.join([status] + secondary) if secondary else status
        return status, secondary

    def get_branch_status_short(self):
        # type: () -> str
        if self.in_rebase():
            return "(no branch, rebasing {})".format(self.rebase_branch_name())

        lines = self._get_status()
        branch_status = self._get_branch_status_components(lines)
        return self._format_branch_status_short(branch_status)

    def _format_branch_status_short(self, branch_status):
        # type: (HeadState) -> str
        detached, branch, remote, clean, ahead, behind, gone = branch_status

        dirty = "" if clean else "*"

        if detached:
            return "DETACHED" + dirty

        output = branch + dirty

        if ahead:
            output += "+" + ahead
        if behind:
            output += "-" + behind

        merge_head = self.merge_head() if self.in_merge() else ""
        return output if not merge_head else output + " (merging {})".format(merge_head)

    def in_rebase(self):
        return self.in_rebase_apply() or self.in_rebase_merge()

    def in_rebase_apply(self):
        return os.path.isdir(self._rebase_apply_dir)

    def in_rebase_merge(self):
        return os.path.isdir(self._rebase_merge_dir)

    @property
    def _rebase_apply_dir(self):
        return os.path.join(self.repo_path, ".git", "rebase-apply")

    @property
    def _rebase_merge_dir(self):
        return os.path.join(self.repo_path, ".git", "rebase-merge")

    @property
    def _rebase_dir(self):
        return self._rebase_merge_dir if self.in_rebase_merge() else self._rebase_apply_dir

    def rebase_branch_name(self):
        return self._read_rebase_file("head-name").replace("refs/heads/", "")

    def rebase_orig_head(self):
        # type: () -> str
        return self._read_rebase_file("orig-head")

    def rebase_conflict_at(self):
        # type: () -> str
        if self.in_rebase_merge():
            return (
                self._read_rebase_file("stopped-sha")
                or self._read_rebase_file("current-commit")
            )
        else:
            return self._read_rebase_file("original-commit")

    def rebase_onto_commit(self):
        # type: () -> str
        return self._read_rebase_file("onto")

    def _read_rebase_file(self, fname):
        # type: (str) -> str
        path = os.path.join(self._rebase_dir, fname)
        try:
            with open(path, "r") as f:
                return f.read().strip()
        except Exception:
            return ""

    def in_merge(self):
        # type: () -> bool
        return os.path.exists(os.path.join(self.repo_path, ".git", "MERGE_HEAD"))

    def merge_head(self):
        # type: () -> str
        path = os.path.join(self.repo_path, ".git", "MERGE_HEAD")
        with open(path, "r") as f:
            commit_hash = f.read().strip()
        return self.get_short_hash(commit_hash)
