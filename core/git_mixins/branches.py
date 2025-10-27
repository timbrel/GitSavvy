from __future__ import annotations
import re

from GitSavvy.core.git_command import mixin_base, NOT_SET
from GitSavvy.core.fns import filter_
from GitSavvy.core.exceptions import GitSavvyError
from GitSavvy.core.utils import cache_in_store_as, hprint, measure_runtime, yes_no_switch
from GitSavvy.core.runtime import run_on_new_thread

from typing import Dict, List, NamedTuple, Optional, Sequence


BRANCH_DESCRIPTION_RE = re.compile(r"^branch\.(.*?)\.description (.*)$")
FOR_EACH_REF_SUPPORTS_AHEAD_BEHIND = (2, 41, 0)


class Upstream(NamedTuple):
    remote: str
    branch: str
    canonical_name: str
    status: str


class AheadBehind(NamedTuple):
    ahead: int
    behind: int


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
    human_committerdate: str
    relative_committerdate: str
    upstream: Optional[Upstream]
    distance_to_head: Optional[AheadBehind]

    @property
    def is_local(self) -> bool:
        return not self.is_remote


class BranchesMixin(mixin_base):

    def get_current_branch(self):
        # type: () -> Optional[Branch]
        for branch in self.get_local_branches():
            if branch.active:
                return branch
        return None

    def compute_branches_to_show(self, branch_name: str) -> list[str] | None:
        """
        For a given `branch_name` (use "HEAD" for the current branch)
        look up its name and its upstream and return that as a list
        """
        branches: list[Branch] = (
            self.current_state().get("branches", [])
            or self.get_branches()
        )
        for b in branches:
            if (
                b.active
                if branch_name == "HEAD"
                else b.canonical_name == branch_name
            ):
                if b.upstream and b.upstream.status != "gone":
                    return [b.canonical_name, b.upstream.canonical_name]
                else:
                    return [b.canonical_name]
        else:
            # Assume `None` implies "HEAD" but doesn't show up prominently
            return None if branch_name == "HEAD" else [branch_name]

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

    def get_branches(self, *, refs=["refs/heads", "refs/remotes"], merged=None):
        # type: (Sequence[str], Optional[bool]) -> List[Branch]
        """
        Return a list of local and/or remote branches.
        """

        def get_branches__(probe_speed: bool, supports_ahead_behind: bool) -> List[Branch]:
            WAIT_TIME = 200  # [ms]
            try:
                stdout: str = self.git_throwing_silently(
                    "for-each-ref",
                    "--format={}".format(
                        "%00".join((
                            "%(HEAD)",
                            "%(refname)",
                            "%(upstream)",
                            "%(upstream:remotename)",
                            "%(upstream:track,nobracket)",
                            "%(committerdate:unix)",
                            "%(committerdate:human)",
                            "%(committerdate:relative)",
                            "%(objectname)",
                            "%(contents:subject)",
                            "%(ahead-behind:HEAD)" if supports_ahead_behind else ""
                        ))
                    ),
                    *refs,
                    # If `supports_ahead_behind` we don't use the `--[no-]merged` argument
                    # and instead filter here in Python land.
                    yes_no_switch("--merged", merged) if not supports_ahead_behind else None,
                    timeout=WAIT_TIME / 1000 if probe_speed else NOT_SET
                )
            except GitSavvyError as e:
                if probe_speed and "timed out after" in e.stderr:

                    def run_commit_graph_write():
                        hprint(
                            f"`git for-each-ref` took more than {WAIT_TIME}ms which is slow "
                            "for our purpose. We now run `git commit-graph write` to see if "
                            "it gets better."
                        )
                        try:
                            self.git_throwing_silently("commit-graph", "write")
                        except GitSavvyError as err:
                            hprint(f"`git commit-graph write` raised: {err}")
                            self.update_store({"slow_repo": True})
                            return

                        with measure_runtime() as ms:
                            get_branches__(False, supports_ahead_behind)
                        elapsed = ms.get()
                        ok = elapsed < WAIT_TIME
                        hprint(
                            f"After `git commit-graph write` the `git for-each-ref` call "
                            f"{'' if ok else 'still '}takes {elapsed}ms"
                        )
                        if not ok:
                            hprint("Disabling sections in the branches dashboard.")

                        self.update_store({"slow_repo": True if not ok else False})

                    run_on_new_thread(run_commit_graph_write)
                    return get_branches__(False, False)

                if "fatal: failed to find 'HEAD'" in e.stderr and supports_ahead_behind:
                    return get_branches__(False, False)

                e.show_error_panel()
                raise

            branches = [
                branch
                for branch in (
                    self._parse_branch_line(line)
                    for line in filter_(stdout.splitlines())
                )
                if branch.name != "HEAD"
            ]
            if supports_ahead_behind:
                # Cache git's full output but return a filtered result if requested.
                self._cache_branches(branches, refs)
                if merged is True:
                    branches = [b for b in branches if b.distance_to_head.ahead == 0]  # type: ignore[union-attr]
                elif merged is False:
                    branches = [b for b in branches if b.distance_to_head.ahead > 0]  # type: ignore[union-attr]

            elif merged is None:
                # For older git versions cache git's output only if it was not filtered by `merged`.
                self._cache_branches(branches, refs)

            return branches

        slow_repo = self.current_state().get("slow_repo", None)
        compute_ahead_behind = (
            self.git_version >= FOR_EACH_REF_SUPPORTS_AHEAD_BEHIND
            and not slow_repo
        )
        probe_speed = compute_ahead_behind and slow_repo is None
        return get_branches__(probe_speed, compute_ahead_behind)

    def _cache_branches(self, branches, refs):
        # type: (List[Branch], Sequence[str]) -> None
        if refs == ["refs/heads", "refs/remotes"]:
            next_state = branches

        elif refs == ["refs/heads"]:
            stored_state = self.current_state().get("branches", [])
            next_state = branches + [b for b in stored_state if b.is_remote]

        elif refs == ["refs/remotes"]:
            stored_state = self.current_state().get("branches", [])
            next_state = [b for b in stored_state if b.is_local] + branches

        else:
            return None

        self.update_store({"branches": next_state})

    @cache_in_store_as("descriptions")
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
        # type: (str) -> Branch
        (head, ref, upstream, upstream_remote, upstream_status,
         committerdate, human_committerdate, relative_committerdate,
         commit_hash, commit_msg, ahead_behind) = line.split("\x00")

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

        ahead_behind_ = AheadBehind(*map(int, ahead_behind.split(" "))) if ahead_behind else None

        return Branch(
            branch_name,
            remote,
            canonical_name,
            commit_hash,
            commit_msg,
            active,
            is_remote,
            int(committerdate),
            human_committerdate,
            relative_committerdate,
            upstream=ups,
            distance_to_head=ahead_behind_
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
