from collections import namedtuple
import re

from GitSavvy.core.git_command import mixin_base


MYPY = False
if MYPY:
    from typing import Dict, Iterable, NamedTuple, Optional, Sequence

    # For local branches, `remote` is empty and `canonical_name == name`.
    # For remote branches:
    Branch = NamedTuple("Branch", [
        ("name", str),              # e.g. "master"
        ("remote", Optional[str]),  # e.g. "origin"
        ("canonical_name", str),    # e.g. "origin/master"
        ("commit_hash", str),
        ("commit_msg", str),
        ("tracking", str),
        ("tracking_status", str),
        ("active", bool),
        ("description", str)
    ])
else:
    Branch = namedtuple("Branch", (
        "name",
        "remote",
        "canonical_name",
        "commit_hash",
        "commit_msg",
        "tracking",
        "tracking_status",
        "active",
        "description"
    ))

BRANCH_DESCRIPTION_RE = re.compile(r"^branch\.(.*?)\.description (.*)$")


class BranchesMixin(mixin_base):

    def get_current_branch(self):
        # type: () -> Optional[Branch]
        for branch in self.get_local_branches():
            if branch.active:
                return branch
        return None

    def get_current_branch_name(self):
        # type: () -> Optional[str]
        """
        Return the name of the current branch.
        """
        branch = self.get_current_branch()
        if branch:
            return branch.name
        return None

    def get_upstream_for_active_branch(self):
        # type: () -> Optional[str]
        """
        Return ref for remote tracking branch.
        """
        return self.git(
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{u}",
            throw_on_error=False
        ).strip() or None

    def get_remote_for_branch(self, branch_name):
        # type: (str) -> Optional[str]
        return self.git(
            "config",
            "--get",
            "branch.{}.remote".format(branch_name),
            throw_on_error=False
        ).strip() or None

    def get_local_branch_by_name(self, branch_name):
        # type: (str) -> Optional[Branch]
        """
        Get a local Branch tuple from branch name.
        """
        for branch in self.get_local_branches():
            if branch.name == branch_name:
                return branch
        return None

    def get_local_branches(self):
        # type: () -> Iterable[Branch]
        return self.get_branches(refs=["refs/heads"])

    def get_branches(
        self, *,
        sort_by_recent=False,
        fetch_descriptions=False,
        refs=["refs/heads", "refs/remotes"]
    ):
        # type: (bool, bool, Sequence[str]) -> Iterable[Branch]
        """
        Return a list of all local and remote branches.
        """
        stdout = self.git(
            "for-each-ref",
            (
                "--format="
                "%(HEAD)%00"
                "%(refname)%00"
                "%(upstream)%00"
                "%(upstream:track)%00"
                "%(objectname)%00"
                "%(contents:subject)"
            ),
            "--sort=-committerdate" if sort_by_recent else None,
            *refs
        )
        branches = (
            branch
            for branch in (
                self._parse_branch_line(line)
                for line in stdout.split("\n")
            )
            if branch and branch.name != "HEAD"
        )
        if not fetch_descriptions:
            return branches

        descriptions = self.fetch_branch_description_subjects()
        return (
            branch._replace(description=descriptions.get(branch.canonical_name, ""))
            for branch in branches
        )

    def fetch_branch_description_subjects(self):
        # type: () -> Dict[str, str]
        rv = {}
        for line in self.git(
            "config",
            "--get-regex",
            r"branch\..*\.description",
            throw_on_error=False
        ).strip("\n").splitlines():
            match = BRANCH_DESCRIPTION_RE.match(line)
            if match is None:
                continue

            branch_name, description = match.group(1), match.group(2)
            rv[branch_name] = description
        return rv

    def _parse_branch_line(self, line):
        # type: (str) -> Optional[Branch]
        line = line.strip()
        if not line:
            return None
        head, ref, upstream, upstream_status, commit_hash, commit_msg = line.split("\x00")

        active = head == "*"
        is_remote = ref.startswith("refs/remotes/")

        branch_name = ref[13:] if is_remote else ref[11:]
        remote = ref[13:].split("/", 1)[0] if is_remote else None
        upstream = upstream[13:]
        if upstream_status:
            # remove brackets
            upstream_status = upstream_status[1:len(upstream_status) - 1]

        return Branch(
            "/".join(branch_name.split("/")[1:]) if is_remote else branch_name,
            remote,
            branch_name,
            commit_hash,
            commit_msg,
            upstream,
            upstream_status,
            active,
            description=""
        )

    def merge(self, branch_names):
        """
        Merge `branch_names` into active branch.
        """

        self.git("merge", *branch_names)

    def branches_containing_commit(self, commit_hash, local_only=True, remote_only=False):
        """
        Return a list of branches which contain a particular commit.
        """
        branches = self.git(
            "branch",
            "-a" if not local_only and not remote_only else None,
            "-r" if remote_only else None,
            "--contains",
            commit_hash
        ).strip().split("\n")
        return [branch.strip() for branch in branches]

    def validate_branch_name(self, branch):
        return self.git("check-ref-format", "--branch", branch, throw_on_error=False).strip()
