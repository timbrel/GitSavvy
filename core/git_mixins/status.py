from __future__ import annotations
from dataclasses import dataclass
from itertools import dropwhile
import os
import re
import string

from GitSavvy.core.fns import tail


from typing import Iterable, List, NamedTuple, Optional, Set, TYPE_CHECKING


class HeadState(NamedTuple):
    detached: bool
    branch: Optional[str]
    remote: Optional[str]
    clean: bool
    ahead: Optional[str]
    behind: Optional[str]
    gone: bool


class FileStatus(NamedTuple):
    path: str
    path_alt: Optional[str]  # For renames and copies, the old path
    index_status: str
    working_status: str

    @classmethod
    def new(cls, path: str, status: str, alt: str | None = None) -> FileStatus:
        return cls(path, alt, status[0], status[1])


@dataclass(frozen=True)
class WorkingDirState:
    staged_files: List[FileStatus]
    unstaged_files: List[FileStatus]
    untracked_files: List[FileStatus]
    merge_conflicts: List[FileStatus]

    @property
    def is_clean(self):
        # type: () -> bool
        return not (
            self.staged_files
            or self.unstaged_files
            or self.untracked_files
            or self.merge_conflicts
        )


MERGE_CONFLICT_PORCELAIN_STATUSES = (
    ("A", "A"),  # unmerged, both added
    ("U", "U"),  # unmerged, both modified
    ("D", "U"),  # unmerged, deleted by us
    ("U", "D"),  # unmerged, deleted by them

    # The following combinations are unlikely to be seen in the wild
    # https://public-inbox.org/git/xmqq4n2czq6n.fsf@gitster.dls.corp.google.com
    ("D", "D"),  # unmerged, both deleted
    ("A", "U"),  # unmerged, added by us
    ("U", "A"),  # unmerged, added by them
)


if TYPE_CHECKING:
    from GitSavvy.core.git_command import (HistoryMixin, _GitCommand)
    class mixin_base(HistoryMixin, _GitCommand): pass  # noqa: E701
else:
    mixin_base = object


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

    def update_working_dir_status(self):
        # type: () -> None
        self.get_working_dir_status()

    def get_working_dir_status(self):
        # type: () -> WorkingDirState
        lines = self._get_status()
        files = self._parse_status_for_file_statuses(lines)
        working_dir_status = self._group_status_entries(files)

        branch_status = self._get_branch_status_components(lines)
        current_branch = branch_status.branch
        last_branches = self.current_state()["last_branches"]
        if current_branch and current_branch != last_branches[-1]:
            last_branches.append(current_branch)
        self.update_store({
            "status": working_dir_status,
            "head": branch_status,
            "last_branches": last_branches,
            "long_status": self._format_branch_status(branch_status, working_dir_status),
            "short_status": self._format_branch_status_short(branch_status),
        })
        return working_dir_status

    def _parse_status_for_file_statuses(self, lines):
        # type: (List[str]) -> List[FileStatus]
        porcelain_entries = iter(lines[1:])
        entries = []

        for entry in porcelain_entries:
            if not entry:
                continue
            index_status = entry[0].strip()
            working_status = entry[1].strip()
            path = entry[3:]
            path_alt = (
                next(porcelain_entries)
                if index_status in ["R", "C"] or working_status in ["R", "C"]
                else None)
            entries.append(FileStatus(path, path_alt, index_status, working_status))

        return entries

    def _group_status_entries(self, file_status_list):
        # type: (List[FileStatus]) -> WorkingDirState
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
            if f.working_status:
                unstaged.append(f)
            if f.index_status:
                staged.append(f)

        return WorkingDirState(
            staged_files=staged,
            unstaged_files=unstaged,
            untracked_files=untracked,
            merge_conflicts=conflicts,
        )

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
            return HeadState(True, None, None, clean, None, None, False)

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
            return HeadState(False, None if clean else addl_lines[0], None, clean, None, None, False)

        branch, _, remote, _, _, _, ahead, _, _, behind, gone = status_match.groups()

        return HeadState(False, branch, remote, clean, ahead, behind, bool(gone))

    def _format_branch_status(self, branch_status, working_dir_status, delim="\n           "):
        # type: (HeadState, WorkingDirState, str) -> str
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
            onto = self._read_rebase_file("onto")
            rebase_progress = self._rebase_progress()
            secondary.append(
                "Rebasing `{}`{}{}.".format(
                    self.rebase_branch_name(),
                    " onto {}".format(self.get_short_hash(onto)) if onto else "",
                    " ({})".format(rebase_progress) if rebase_progress else ""
                )
            )
            rebase_stopped_at = self.rebase_stopped_at()
            if rebase_stopped_at:
                secondary.append("`{}".format(rebase_stopped_at))

            if working_dir_status.merge_conflicts:
                secondary.append("hint: Resolve all conflicts and stage them")
            elif (
                working_dir_status.staged_files
                and not working_dir_status.unstaged_files
                and not working_dir_status.untracked_files
                and not working_dir_status.merge_conflicts
            ):
                if rebase_stopped_at and rebase_stopped_at.startswith("edit"):
                    secondary.append("hint: Commit stage")
                elif rebase_stopped_at and rebase_stopped_at.startswith("pick"):
                    secondary.append("hint: Run `rebase --continue` to continue")
            elif working_dir_status.is_clean:
                if rebase_stopped_at and rebase_stopped_at.startswith("edit"):
                    secondary.append("hint: Amend, edit, or run `rebase --continue` to continue")
                else:
                    secondary.append("hint: Run `rebase --continue` to continue")

        if self.in_cherry_pick():
            secondary.append("Cherry-picking {}.".format(self.cherry_pick_head()))
        if self.in_revert():
            secondary.append("Reverting {}.".format(self.revert_head()))
        if self.in_bisect():
            secondary.append("Bisecting from {} on.".format(self.bisect_start_commit()))

        return delim.join([status] + secondary) if secondary else status

    def _format_branch_status_short(self, branch_status):
        # type: (HeadState) -> str
        if self.in_rebase():
            rebase_progress = self._rebase_progress()
            return "(no branch, rebasing {}{})".format(
                self.rebase_branch_name(),
                " {}".format(rebase_progress) if rebase_progress else ""
            )

        detached, branch, remote, clean, ahead, behind, gone = branch_status

        dirty = "" if clean else "*"

        if detached:
            return "DETACHED" + dirty

        assert branch
        output = branch + dirty

        if ahead:
            output += "+" + ahead
        if behind:
            output += "-" + behind

        merge_head = self.merge_head() if self.in_merge() else ""
        if merge_head:
            output += " (merging {})".format(merge_head)
        cherry_pick_head = self.cherry_pick_head() if self.in_cherry_pick() else ""
        if cherry_pick_head:
            output += " (cherry-picking {})".format(cherry_pick_head)
        revert_head = self.revert_head() if self.in_revert() else ""
        if revert_head:
            output += " (reverting {})".format(revert_head)
        bisect_start = self.bisect_start_commit() if self.in_bisect() else ""
        if bisect_start:
            output += " (bisecting {})".format(bisect_start)

        return output

    def in_rebase(self):
        return self.in_rebase_apply() or self.in_rebase_merge()

    def in_rebase_apply(self):
        return os.path.isdir(self._rebase_apply_dir)

    def in_rebase_merge(self):
        return os.path.isdir(self._rebase_merge_dir)

    @property
    def _rebase_apply_dir(self):
        return os.path.join(self.git_dir, "rebase-apply")

    @property
    def _rebase_merge_dir(self):
        return os.path.join(self.git_dir, "rebase-merge")

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

    def rebase_stopped_at(self):
        # type: () -> str
        commit_hash = self._read_git_file("REBASE_HEAD")
        if not commit_hash:
            return ""

        done = self._read_rebase_file("done")
        comment_char = "#"
        try:
            item = [
                line
                for line in done.splitlines()
                if line and not line.startswith(comment_char)
            ][-1]
        except IndexError:
            return ""

        parts = item.split()
        if parts[0] in {"pick", "fixup", "squash", "reword", "edit"}:
            parts[1] = self.get_short_hash(parts[1])
            return " ".join(parts)
        else:
            return item

    def _rebase_progress(self):
        # type: () -> str
        cursor, total = self._read_rebase_file("msgnum"), self._read_rebase_file("end")
        if cursor and total:
            return "{}/{}".format(cursor, total)
        return ""

    def _read_rebase_file(self, fname):
        # type: (str) -> str
        path = os.path.join(self._rebase_dir, fname)
        try:
            with open(path, "r") as f:
                return f.read().strip()
        except Exception:
            return ""

    def _read_git_file(self, *fname):
        # type: (str) -> str
        path = os.path.join(self.git_dir, *fname)
        try:
            with open(path, "r") as f:
                return f.read().strip()
        except Exception:
            return ""

    def in_merge(self):
        # type: () -> bool
        return os.path.exists(os.path.join(self.git_dir, "MERGE_HEAD"))

    def merge_head(self):
        # type: () -> str
        path = os.path.join(self.git_dir, "MERGE_HEAD")
        with open(path, "r") as f:
            commit_hash = f.read().strip()
        return self.get_short_hash(commit_hash)

    def in_cherry_pick(self):
        # type: () -> bool
        return os.path.exists(os.path.join(self.git_dir, "CHERRY_PICK_HEAD"))

    def cherry_pick_head(self):
        # type: () -> str
        commit_hash = self._read_git_file("CHERRY_PICK_HEAD")
        return self.get_short_hash(commit_hash) if commit_hash else ""

    def in_revert(self):
        # type: () -> bool
        return os.path.exists(os.path.join(self.git_dir, "REVERT_HEAD"))

    def revert_head(self):
        # type: () -> str
        commit_hash = self._read_git_file("REVERT_HEAD")
        return self.get_short_hash(commit_hash) if commit_hash else ""

    def in_bisect(self) -> bool:
        return os.path.exists(os.path.join(self.git_dir, "BISECT_START"))

    def bisect_start_commit(self) -> str:
        commit_hash = self._read_git_file("BISECT_START")
        return self.get_short_hash(commit_hash) if commit_hash else ""

    def conflicting_files_(self):
        # type: () -> List[str]
        # List all files that are or *were* conflicting.  This is a bit of a hack
        # as I could not find an API for that.  Note that this is not `git ls-files -u`
        # or `git diff --name-only --diff-filter=U` because we want to see also files
        # already staged ("resolved").  We exactly may want to revert such a resolution
        # with `checkout -m -- <path>`.

        # We parse something like this:
        """
        Merge branch 'n' into m

        # Conflicts:
        #   core/commands/merge.py
        """
        merge_msg = self._read_git_file("MERGE_MSG")
        return [
            # E.g. "#  core/commands/merge.py"
            line[1:].strip()
            for line in tail(dropwhile(
                lambda x: not x.startswith("# Conflicts:"),
                merge_msg.splitlines()
            ))
            if line.startswith("#\t")
        ]

    def check_for_conflict_markers(self, file_paths):
        # type: (List[str]) -> Set[str]
        to_check = set(file_paths) & set(self.conflicting_files_())
        if not to_check:
            return set()

        return {
            re.search(r"^(?P<fpath>[^:]+)", line).group("fpath")  # type: ignore[union-attr]
            for line in self.git(
                "diff",
                "--check",
                "--", *to_check,
                show_panel_on_error=False,
                throw_on_error=False
            ).splitlines()
            if "leftover conflict marker" in line
        }

    def is_probably_untracked_file(self, file_path: str) -> bool:
        """Check in the store if `file_path` is untracked."""
        return bool(
            (status := self.current_state().get("status"))
            and (rel_file_path := os.path.relpath(file_path, self.repo_path))
            and (normed_git_path := rel_file_path.replace("\\", "/"))
            and any(file.path == normed_git_path for file in status.untracked_files)
        )

    def _mark_untracked_files_as_staged(self, files: list[str]) -> None:
        status = self.current_state().get("status")
        if not status:
            return

        staged = extract_paths(status.staged_files)
        staged_files = sorted(
            status.staged_files + [
                FileStatus.new(f, "A ")
                for f in files if f not in staged
            ]
        )
        untracked_files = [f for f in status.untracked_files if f.path not in files]
        self.update_store({
            "status": WorkingDirState(
                staged_files=staged_files,
                unstaged_files=status.unstaged_files,
                untracked_files=untracked_files,
                merge_conflicts=status.merge_conflicts
            )
        })

    def _mark_staged_files_as_untracked(self, files: list[str]) -> None:
        status = self.current_state().get("status")
        if not status:
            return

        staged_files = [f for f in status.staged_files if f.path not in files]
        untracked = extract_paths(status.untracked_files)
        untracked_files = sorted(
            status.untracked_files + [
                FileStatus.new(f, "??")
                for f in files if f not in untracked
            ]
        )
        self.update_store({
            "status": WorkingDirState(
                staged_files=staged_files,
                unstaged_files=status.unstaged_files,
                untracked_files=untracked_files,
                merge_conflicts=status.merge_conflicts
            )
        })


def extract_paths(files: Iterable[FileStatus]) -> set[str]:
    return {f.path for f in files if f.path}
