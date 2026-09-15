from __future__ import annotations
import re
import time

from GitSavvy.core.git_command import mixin_base, NOT_SET
from GitSavvy.core.fns import filter_
from GitSavvy.core.exceptions import GitSavvyError
from GitSavvy.core.caches import cache_in_store_as
from GitSavvy.core.utils import hprint, yes_no_switch
from GitSavvy.core.runtime import (
    run_on_new_thread,
    run_when_worker_is_idle,
    run_when_worker_is_idle_after
)
from GitSavvy.core.types import FullHash

from typing import Dict, List, NamedTuple, Optional, Sequence


BRANCH_DESCRIPTION_RE = re.compile(r"^branch\.(.*?)\.description (.*)$")
FOR_EACH_REF_SUPPORTS_AHEAD_BEHIND = (2, 41, 0)
FOR_EACH_REF_SUPPORTS_WORKTREEPATH = (2, 23, 0)
AHEAD_BEHIND_QUERY_TIMEOUT = 0.2  # [s]
AHEAD_BEHIND_PROBE_DELAYS = (0, 1, 10, 60, 10 * 60)  # [s]
AHEAD_BEHIND_SLOW_PROBE_DELAYS = (0, 60, 10 * 60)  # [s]
AHEAD_BEHIND_SLOW_RETRY_INTERVAL = 60 * 60  # [s]
COMMIT_GRAPH_WRITE_INTERVAL = 60 * 60  # [s]
# Retry deadlines and graph-write times persist, so use time.time(), not monotonic time.


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
    commit_hash: FullHash
    commit_msg: str
    active: bool
    is_remote: bool
    committerdate: int
    human_committerdate: str
    relative_committerdate: str
    upstream: Optional[Upstream]
    distance_to_head: Optional[AheadBehind]
    worktree_path: Optional[str]

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

    def get_branches(
        self,
        *,
        refs: Sequence[str] = ["refs/heads", "refs/remotes"],
        merged: Optional[bool] = None
    ) -> List[Branch]:
        """
        Return a list of local and/or remote branches.
        """
        supports_ahead_behind = self.git_version >= FOR_EACH_REF_SUPPORTS_AHEAD_BEHIND
        ahead_behind_is_fast = not self.current_state().get(
            "ahead_behind_consecutive_failures", 0
        )

        if supports_ahead_behind and ahead_behind_is_fast:
            try:
                return self._get_branches(
                    refs, merged, with_ahead_behind=True, probe_speed=True
                )
            except GitSavvyError as e:
                if self._is_timeout(e):
                    self._record_ahead_behind_timeout()
                    self._schedule_ahead_behind_probe(refs, merged)
                    return self._get_branches(
                        refs, merged, with_ahead_behind=False
                    )
                if "fatal: failed to find 'HEAD'" in e.stderr:
                    return self._get_branches(
                        refs, merged, with_ahead_behind=False
                    )

                e.show_error_panel()
                raise

        elif supports_ahead_behind:
            self._schedule_ahead_behind_probe(refs, merged, full_probe=False)

        return self._get_branches(refs, merged, with_ahead_behind=False)

    def _get_branches(
        self,
        refs: Sequence[str],
        merged: Optional[bool],
        *,
        with_ahead_behind: bool,
        probe_speed: bool = False
    ) -> List[Branch]:
        supports_worktreepath = self.git_version >= FOR_EACH_REF_SUPPORTS_WORKTREEPATH
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
                    "%(ahead-behind:HEAD)" if with_ahead_behind else "",
                    "%(worktreepath)" if supports_worktreepath else ""
                ))
            ),
            *refs,
            # With ahead/behind data we don't use the `--[no-]merged` argument
            # and instead filter here in Python land.
            yes_no_switch("--merged", merged) if not with_ahead_behind else None,
            timeout=AHEAD_BEHIND_QUERY_TIMEOUT if probe_speed else NOT_SET
        )
        branches = [
            branch
            for branch in (
                self._parse_branch_line(line)
                for line in filter_(stdout.splitlines())
            )
            if branch.name != "HEAD"
        ]
        if with_ahead_behind:
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

    def _schedule_ahead_behind_probe(
        self,
        refs: Sequence[str],
        merged: Optional[bool],
        *,
        full_probe: bool = True
    ) -> None:
        # Avoid filling the worker queue while a known-slow repo is cooling down.
        retry_at = self.current_state().get("ahead_behind_retry_at", 0)
        if time.time() >= retry_at:
            run_when_worker_is_idle(
                self._start_ahead_behind_probe_if_due,
                refs,
                merged,
                full_probe
            )

    def _start_ahead_behind_probe_if_due(
        self,
        refs: Sequence[str],
        merged: Optional[bool],
        full_probe: bool
    ) -> None:
        # Multiple callers can schedule this before the first task starts.
        # Re-check on the serialized worker: an earlier task may have claimed
        # the probe or restored the repo to the fast path in the meantime.
        state = self.current_state()
        now = time.time()
        if (
            not state.get("ahead_behind_consecutive_failures", 0)
            or now < state.get("ahead_behind_retry_at", 0)
        ):
            return

        # Updating the deadline claims the probe without a separate lock.
        self.update_store({
            "ahead_behind_retry_at": now + AHEAD_BEHIND_SLOW_RETRY_INTERVAL
        })

        def task():
            self._write_commit_graph_if_due()
            # Run on worker, _get_branches runs with a short timeout,
            # and eventually, when successful, updates the store.
            run_when_worker_is_idle(
                self._run_ahead_behind_probe,
                refs,
                merged,
                full_probe,
                attempt=0
            )

        run_on_new_thread(task)

    def _run_ahead_behind_probe(
        self,
        refs: Sequence[str],
        merged: Optional[bool],
        full_probe: bool,
        *,
        attempt: int
    ) -> None:
        try:
            self._get_branches(
                refs, merged, with_ahead_behind=True, probe_speed=True
            )
        except GitSavvyError as e:
            if not self._is_timeout(e):
                hprint(f"Ahead/behind probe raised: {e}")
                return

            probe_delays = (
                AHEAD_BEHIND_PROBE_DELAYS
                if full_probe
                else AHEAD_BEHIND_SLOW_PROBE_DELAYS
            )
            next_attempt = attempt + 1
            if next_attempt < len(probe_delays):
                run_when_worker_is_idle_after(
                    probe_delays[next_attempt] * 1000,
                    self._run_ahead_behind_probe,
                    refs,
                    merged,
                    full_probe,
                    attempt=next_attempt
                )
            else:
                self._record_slow_ahead_behind_query()
                hprint(
                    "Ahead/behind queries are still slow after "
                    f"{len(probe_delays)} background probes."
                )
            return

        self._record_fast_ahead_behind_query()
        hprint("Ahead/behind queries are fast again.")

    def _write_commit_graph_if_due(self) -> None:
        now = time.time()
        last_run = self.current_state().get(
            "last_commit_graph_write", -COMMIT_GRAPH_WRITE_INTERVAL
        )
        if now - last_run < COMMIT_GRAPH_WRITE_INTERVAL:
            return

        self.update_store({"last_commit_graph_write": now})
        hprint(
            f"`git for-each-ref` took more than "
            f"{AHEAD_BEHIND_QUERY_TIMEOUT * 1000:g}ms. Running "
            "`git commit-graph write` before probing again."
        )
        try:
            self.git_throwing_silently("commit-graph", "write")
        except GitSavvyError as e:
            hprint(f"`git commit-graph write` raised: {e}")

    def _record_ahead_behind_timeout(self) -> None:
        failures = self.current_state().get("ahead_behind_consecutive_failures", 0)
        self.update_store({
            "ahead_behind_consecutive_failures": failures + 1,
            "ahead_behind_retry_at": 0
        })

    def _record_slow_ahead_behind_query(self) -> None:
        failures = self.current_state().get("ahead_behind_consecutive_failures", 0)
        self.update_store({"ahead_behind_consecutive_failures": failures + 1})

    def _record_fast_ahead_behind_query(self) -> None:
        self.update_store({
            "ahead_behind_consecutive_failures": 0,
            "ahead_behind_retry_at": 0
        })

    def _is_timeout(self, error: GitSavvyError) -> bool:
        return "timed out after" in error.stderr

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
         commit_hash, commit_msg, ahead_behind, worktree_path_str) = line.split("\x00")

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
        worktree_path = worktree_path_str or None

        return Branch(
            branch_name,
            remote,
            canonical_name,
            FullHash(commit_hash),
            commit_msg,
            active,
            is_remote,
            int(committerdate),
            human_committerdate,
            relative_committerdate,
            upstream=ups,
            distance_to_head=ahead_behind_,
            worktree_path=worktree_path
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
