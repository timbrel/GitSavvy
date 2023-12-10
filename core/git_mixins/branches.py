import re

from GitSavvy.core import store
from GitSavvy.core.git_command import mixin_base
from GitSavvy.core.fns import filter_

from typing import Dict, List, NamedTuple, Optional, Sequence


BRANCH_DESCRIPTION_RE = re.compile(r"^branch\.(.*?)\.description (.*)$")


class Upstream(NamedTuple):
    remote: str
    branch: str
    canonical_name: str
    status: str


class Branch(NamedTuple):
    # For local branches, `remote` is empty and `canonical_name == name`.
    #                        For remote branches:
    name: str              # e.g. "master"
    remote: Optional[str]  # e.g. "origin"
    canonical_name: str    # e.g. "origin/master"
    commit_hash: str
    commit_msg: str
    active: bool
    is_remote: bool
    committerdate: int
    upstream: Optional[Upstream]


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
        # type: () -> Optional[Upstream]
        branch = self.get_current_branch()
        return branch.upstream if branch else None

    def get_remote_for_branch(self, branch_name):
        # type: (str) -> Optional[str]
        branch = self.get_local_branch_by_name(branch_name)
        if branch and branch.upstream:
            return branch.upstream.remote
        return None

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
        # type: () -> List[Branch]
        return self.get_branches(refs=["refs/heads"])

    def get_branches(self, *, refs=["refs/heads", "refs/remotes"]):
        # type: (Sequence[str]) -> List[Branch]
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
                "%(upstream:remotename)%00"
                "%(upstream:track,nobracket)%00"
                "%(committerdate:unix)%00"
                "%(objectname)%00"
                "%(contents:subject)"
            ),
            *refs
        )  # type: str
        branches = [
            branch
            for branch in (
                self._parse_branch_line(line)
                for line in filter_(stdout.splitlines())
            )
            if branch.name != "HEAD"
        ]
        self._cache_branches(branches, refs)
        return branches

    def _cache_branches(self, branches, refs):
        # type: (List[Branch], Sequence[str]) -> None
        if refs == ["refs/heads", "refs/remotes"]:
            next_state = branches

        elif refs == ["refs/heads"]:
            stored_state = store.current_state(self.repo_path).get("branches", [])
            next_state = branches + [b for b in stored_state if b.is_remote]

        elif refs == ["refs/remotes"]:
            stored_state = store.current_state(self.repo_path).get("branches", [])
            next_state = [b for b in stored_state if not b.is_remote] + branches

        else:
            return None

        store.update_state(self.repo_path, {"branches": next_state})

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
        store.update_state(self.repo_path, {"descriptions": rv})
        return rv

    def _parse_branch_line(self, line):
        # type: (str) -> Branch
        (head, ref, upstream, upstream_remote, upstream_status,
         committerdate, commit_hash, commit_msg) = line.split("\x00")

        active = head == "*"
        is_remote = ref.startswith("refs/remotes/")
        ref_ = ref.split("/")[2:]
        canonical_name = "/".join(ref_)
        if is_remote:
            remote, branch_name = ref_[0], "/".join(ref_[1:])
        else:
            remote, branch_name = None, canonical_name

        if upstream:
            is_remote_upstream = upstream.startswith("refs/remotes/")
            upstream_ = upstream.split("/")[2:]
            upstream_canonical = "/".join(upstream_)
            if is_remote_upstream:
                upstream_branch = "/".join(upstream_[len(upstream_remote.split("/")):])
            else:
                upstream_branch = upstream_canonical
            ups = Upstream(upstream_remote, upstream_branch, upstream_canonical, upstream_status)

        else:
            ups = None

        return Branch(
            branch_name,
            remote,
            canonical_name,
            commit_hash,
            commit_msg,
            active,
            is_remote,
            int(committerdate),
            upstream=ups
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
