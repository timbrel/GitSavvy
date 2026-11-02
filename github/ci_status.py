"""
Observe GitHub's commit-check rollup for a commit after pushing it.

A run is started by `on_successful_push` and owns its complete lifecycle:
polling, animation, timed text transitions, and clearing.  It publishes
rendered strings into the store at

    store.current_state(repo_path)["github_ci_status"][(branch, full_sha)]

Additionally, `on_head_observed` looks up the CI result once whenever
`git status` observes that HEAD moved to a pushed branch commit or a
detached commit, e.g. after a checkout, a pull, or on startup.  A
detached HEAD's result is published under the branch name "".  These
lookups are passive: no
animation and no acknowledgements, only a failure shows as `[red]`.

`on_branches_observed` looks up all pushed local branches with one
request whenever the branches dashboard refreshes.  Its results are
published like those of passive lookups.

All observations also publish the plain CI state, e.g. "success", at

    store.current_state(repo_path)["github_ci_results"][(branch, full_sha)]

for the branches dashboard.

The status bar only selects the applicable string; it never schedules
anything or computes elapsed time.

Threads: the UI thread owns all run and lookup state, `known_sources`,
and the published caches; every function touching them is
`@must_run_on_ui`.  Not for Sublime's sake: git never blocks the UI
thread, so its timers keep the animation, polling, and delays on time,
and the status bar reads the published status texts there.  The entry
points run on the threads that ran git, and do their git reads there;
the HTTPS requests run on short-lived background threads.  Both hand their
results over with `enqueue_on_ui`.  Only `observed_heads` is shared:
the git threads deduplicate HEADs under `observed_heads_lock` before
they read any remote info.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from functools import partial
import os
import threading
import time

import sublime

from GitSavvy.core import store
from GitSavvy.core.caches import Cache
from GitSavvy.core.fns import last
from GitSavvy.core.git_mixins.remotes import by_raw_key
from GitSavvy.core.runtime import enqueue_on_ui, must_run_on_ui, run_new_daemon_thread
from GitSavvy.core.settings import app_settings
from GitSavvy.core.ui__busy_spinner import BusyIndicators
from GitSavvy.core.utils import eat_but_log_errors
from GitSavvy.github import github

from typing import Callable, Dict, List, Literal, NamedTuple, Optional, Sequence, Tuple, TYPE_CHECKING
if TYPE_CHECKING:
    from GitSavvy.core.git_command import GitCommand
    from GitSavvy.core.git_mixins.branches import Branch
    from GitSavvy.core.git_mixins.remotes import PushDestination, RemoteInfoBlob
    from GitSavvy.core.git_mixins.status import HeadState


__all__ = (
    "on_branches_observed",
    "on_head_observed",
    "on_successful_push",
    "start",
    "stop",
)


SETTING = "github_ci_status_in_status_bar"
POLL_INTERVAL = 10.0  # [seconds], after the previous request completed
# [seconds] since the push, when to look for checks until GitHub reports any
DISCOVERY_SCHEDULE = (3.0, 6.0, 9.0, 15.0, 25.0, 35.0, 45.0, 55.0)
# Without local workflows, only external CI could report checks.
QUIET_DISCOVERY_SCHEDULE = (5.0, 15.0, 25.0, 35.0)
GREEN_CONFIRMATION = 10.0  # [seconds] between two successful observations
DEADLINE = 30 * 60.0  # [seconds] since the run started
RETRY_DELAYS = (10.0, 20.0, 40.0)  # [seconds] after consecutive transient failures
FRAME_INTERVAL = 0.2  # [seconds]
LABEL_DURATION = 3.0  # [seconds] to show "CI:" while watching
ACKNOWLEDGE_DURATION = 5.0  # [seconds] to show a settled result verbosely
FRAMES = BusyIndicators.STARS
SETTLE_DELAY = 2.0  # [seconds] HEAD must stay put before a passive lookup
MAX_BATCH_SIZE = 50  # branches per dashboard lookup

RepoPath = str
CacheKey = Tuple[str, str]  # (branch name, full commit hash)
RunKey = Tuple[RepoPath, CacheKey]
Source = Tuple[str, str, str]  # lowercased (host, owner, repo) of the queried repository
Result = Literal["success", "failure"]
# A `github.CiState` but "unknown", which is never published.
KnownState = Literal["success", "failure", "pending", "no_checks"]
DETACHED = ""  # the branch name of passive lookups for a detached HEAD


class KnownSource(NamedTuple):
    source: Source
    remote_url: str


@dataclass(eq=False)
class Run:
    repo_path: RepoPath
    key: CacheKey
    repository: github.GitHubRepo = field(repr=False)  # carries the token
    # Set until the first accepted observation verified the pushed ref.
    push_target: Optional[github.PushTarget]
    started_at: float
    next_request_at: float
    discovery: Sequence[float]
    in_flight: bool = False
    # When GitHub first reported checks; the busy text only shows from then on.
    checks_seen_at: Optional[float] = None
    first_success_at: Optional[float] = None
    consecutive_failures: int = 0
    result: Optional[Result] = None
    settled_at: float = 0.0
    done: bool = False

    @property
    def deadline(self) -> float:
        return self.started_at + DEADLINE


@dataclass(eq=False)
class Lookup:
    repo_path: RepoPath
    key: CacheKey
    repository: github.GitHubRepo = field(repr=False)  # carries the token


@dataclass(eq=False)
class BatchLookup:
    repo_path: RepoPath
    keys: List[CacheKey]
    repository: github.GitHubRepo = field(repr=False)  # carries the token


runs: Dict[RunKey, Run] = {}
lookups: Dict[RunKey, Lookup] = {}
batch_lookups: Dict[RepoPath, BatchLookup] = {}
# The last HEAD `git status` observed per repository, or `None` if it was
# not eligible for a lookup.  Written on the threads running `git status`.
observed_heads: Dict[RepoPath, Optional[CacheKey]] = {}
observed_heads_lock = threading.Lock()
known_sources: Dict[RepoPath, KnownSource] = {}
ticking = False
unsubscribe_store: Optional[Callable[[], None]] = None
settings: Optional[sublime.Settings] = None
was_enabled = True


def on_successful_push(
    cmd: GitCommand,
    remote: str,
    destinations: List[PushDestination],
    source_branch: str,
    source_sha: Optional[str]
) -> None:
    """
    Start observing CI for `source_sha` after `git push` succeeded.

    Runs on the thread that pushed.  Unsupported or ambiguous pushes skip
    monitoring silently.  Only reads the git config, which also republishes
    `remote_info` to the store, and lists the local workflows.
    """
    if not is_enabled() or not source_sha:
        return

    repo_path = cmd.repo_path
    destination = single_branch_update(destinations)
    if not destination:
        dprint("CI status: skip, not a single branch push to a single destination")
        return
    push_url, destination_ref = destination

    remote_info = cmd.get_remote_info()
    if is_mirror(remote_info, remote):
        dprint("CI status: skip, {} is a mirror".format(remote))
        return

    integration = integration_repository(remote_info)
    if not integration:
        return
    repository, integration_url = integration

    try:
        pushed = github.parse_remote(push_url)
    except ValueError:
        dprint("CI status: skip, not a GitHub remote")
        return
    if pushed.fqdn.lower() != repository.fqdn.lower():
        dprint("CI status: skip, pushed to a different host than {}".format(repository.fqdn))
        return

    enqueue_on_ui(
        start_run,
        repo_path,
        (source_branch, source_sha),
        repository,
        github.PushTarget(pushed.owner, pushed.repo, destination_ref),
        integration_url,
        discovery_schedule(repo_path)
    )


def on_head_observed(cmd: GitCommand, head: HeadState) -> None:
    """
    Look up the CI result of HEAD once whenever it moved.

    Runs on the thread that ran `git status`.  A HEAD stays fresh until it
    moves: observing the same eligible `(branch, sha)` again never queries
    again.  Moving away and back, or becoming eligible again, does.

    A branch is eligible if it looks pushed: it has an upstream and is not
    ahead of it.  A detached HEAD is always eligible, as we cannot tell
    locally if GitHub knows the commit; if not, the lookup answers
    "unknown" and shows nothing.  Its result is published under the
    `DETACHED` pseudo branch.  Nothing is eligible in the middle of a
    rebase, merge, cherry-pick, revert, or bisect.  The lookup itself
    waits until HEAD stayed put for `SETTLE_DELAY`.
    """
    global observed_heads
    if not is_enabled():
        return

    with eat_but_log_errors():
        repo_path = cmd.repo_path
        key = passive_key(cmd, head)
        with observed_heads_lock:
            if repo_path in observed_heads and observed_heads[repo_path] == key:
                return
            observed_heads[repo_path] = key
        if not key:
            return

        # Quiet: this runs on every HEAD change, also in non-GitHub repos.
        integration = integration_repository(cmd.get_remote_info(), verbose=False)
        if not integration:
            return
        repository, integration_url = integration
        schedule_lookup(repo_path, key, repository, integration_url)


def on_branches_observed(cmd: GitCommand, branches: Sequence[Branch]) -> None:
    """
    Look up the CI results of all pushed local branches with one request.

    Runs on the thread that refreshed the branches dashboard, after it
    fetched `branches`.  Eligible are local branches that look pushed:
    their upstream exists and they are not ahead of it.  At most
    `MAX_BATCH_SIZE` of them, the most recently committed first.

    Unlike HEAD lookups, every call looks up again, but only one batch
    per repository is in flight.  Keys a push run owns are skipped.
    """
    if not is_enabled():
        return

    with eat_but_log_errors():
        keys = branch_keys(branches)
        if not keys:
            return

        # Quiet: this runs on every dashboard refresh, also in non-GitHub repos.
        integration = integration_repository(cmd.get_remote_info(), verbose=False)
        if not integration:
            return
        repository, integration_url = integration
        enqueue_on_ui(start_batch_lookup, cmd.repo_path, keys, repository, integration_url)


def dprint(*args, **kwargs) -> None:
    from GitSavvy.core.commands import debug

    debug.dprint(*args, **kwargs)


@must_run_on_ui
def start() -> None:
    global unsubscribe_store, settings, was_enabled
    stop()
    settings = sublime.load_settings("GitSavvy.sublime-settings")
    settings.add_on_change(__name__, on_settings_changed)
    was_enabled = is_enabled()
    unsubscribe_store = store.subscribe("*", {"remote_info"}, on_remote_info_update)


@must_run_on_ui
def stop() -> None:
    global unsubscribe_store, settings, runs, lookups, batch_lookups, known_sources
    if settings is not None:
        settings.clear_on_change(__name__)
        settings = None
    if unsubscribe_store is not None:
        unsubscribe_store()
        unsubscribe_store = None
    for run in list(runs.values()):
        finish(run)
    lookups.clear()
    batch_lookups.clear()
    forget_observed_heads()
    known_sources.clear()


def is_enabled() -> bool:
    return bool(app_settings.get(SETTING, True))


@must_run_on_ui
def start_run(
    repo_path: RepoPath,
    key: CacheKey,
    repository: github.GitHubRepo,
    push_target: Optional[github.PushTarget],
    integration_url: str,
    discovery: Sequence[float]
) -> None:
    global runs, lookups
    if not is_enabled():
        return

    remember_source(repo_path, repository, integration_url)
    # A push supersedes a passive lookup of the same key.
    lookups.pop((repo_path, key), None)
    previous_run = runs.get((repo_path, key))
    if previous_run:
        finish(previous_run)

    started_at = now()
    run = Run(
        repo_path,
        key,
        repository,
        push_target,
        started_at=started_at,
        next_request_at=started_at + discovery[0],
        discovery=discovery,
    )
    runs[(repo_path, key)] = run
    dprint("CI status: watch {} at {}/{}".format(key, repository.owner, repository.repo))
    advance(run, started_at)
    ensure_ticking()


@must_run_on_ui
def remember_source(repo_path: RepoPath, repository: github.GitHubRepo, integration_url: str) -> None:
    global known_sources
    known_source = KnownSource(source_of(repository), integration_url)
    previous_source = known_sources.get(repo_path)
    if previous_source and previous_source.source != known_source.source:
        reset_repo(repo_path)
    known_sources[repo_path] = known_source


@must_run_on_ui
def advance_all() -> None:
    global runs
    t = now()
    for run in list(runs.values()):
        advance(run, t)


@must_run_on_ui
def advance(run: Run, t: float) -> None:
    """Publish the text for `t` and start a request if one is due."""
    if run.done:
        return

    if run.result is None:
        if t >= run.deadline:
            dprint("CI status: {} timed out".format(run.key))
            terminate(run)
            return
        if not run.in_flight and t >= run.next_request_at:
            if not has_associated_window(run.repo_path):
                dprint("CI status: {} has no window anymore".format(run.key))
                terminate(run)
                return
            run.in_flight = True
            request_observation(run)
        # Stay silent while finding out if there are checks at all.
        if run.checks_seen_at is not None:
            publish(run.repo_path, run.key, pending_text(t - run.checks_seen_at))
            record_results(run.repo_path, {run.key: "pending"})
        return

    acknowledged = t - run.settled_at >= ACKNOWLEDGE_DURATION
    if run.result == "success":
        publish(run.repo_path, run.key, "" if acknowledged else "[CI: ok]")
    else:
        publish(run.repo_path, run.key, "[red]" if acknowledged else "[CI: failed]")
    if acknowledged:
        finish(run)


def pending_text(elapsed: float) -> str:
    # Count in whole milliseconds to avoid float artifacts like 0.6 / 0.2 < 3
    frame = FRAMES[int(elapsed * 1000) // int(FRAME_INTERVAL * 1000) % len(FRAMES)]
    return "[CI: {}]".format(frame) if elapsed < LABEL_DURATION else "[{}]".format(frame)


@must_run_on_ui
def request_observation(run: Run) -> None:
    # Read the mutable `push_target` here, not on the request thread.
    run_new_daemon_thread(fetch_observation, run, run.push_target)


def fetch_observation(run: Run, push_target: Optional[github.PushTarget]) -> None:
    observation = github.query_ci_status(run.repository, run.key[1], push_target)
    enqueue_on_ui(on_observation, run, observation)


@must_run_on_ui
def on_observation(run: Run, observation: github.CiObservation) -> None:
    run.in_flight = False
    if run.done:
        return

    t = now()
    state = observation.state
    dprint("CI status: {} -> {} {}".format(run.key, state, observation.reason).rstrip())
    if state != "unknown":
        # The response was only accepted if the pushed ref pointed at our commit.
        run.push_target = None
        run.consecutive_failures = 0
    if state != "success":
        run.first_success_at = None
    if state in ("success", "pending") and run.checks_seen_at is None:
        run.checks_seen_at = t

    if state == "failure":
        settle(run, "failure", t)
        return

    if state == "success":
        if run.first_success_at is None:
            run.first_success_at = t
        elif t - run.first_success_at >= GREEN_CONFIRMATION:
            settle(run, "success", t)
            return
        run.next_request_at = t + POLL_INTERVAL

    elif state == "pending":
        run.next_request_at = t + POLL_INTERVAL

    elif state == "no_checks":
        if run.checks_seen_at is None:
            next_request_at = next_discovery_request(run, t)
            if next_request_at is None:
                dprint("CI status: {} has no checks".format(run.key))
                record_results(run.repo_path, {run.key: "no_checks"})
                terminate(run)
                return
            run.next_request_at = next_request_at
        else:
            run.next_request_at = t + POLL_INTERVAL

    elif observation.retry_after is not None:
        run.next_request_at = t + observation.retry_after

    elif observation.transient:
        run.consecutive_failures += 1
        if run.consecutive_failures > len(RETRY_DELAYS):
            terminate(run)
            return
        run.next_request_at = t + RETRY_DELAYS[run.consecutive_failures - 1]

    else:
        terminate(run)
        return

    if run.next_request_at > run.deadline:
        terminate(run)


def next_discovery_request(run: Run, t: float) -> Optional[float]:
    return next((run.started_at + d for d in run.discovery if run.started_at + d > t), None)


@must_run_on_ui
def settle(run: Run, result: Result, t: float) -> None:
    run.result = result
    run.settled_at = t
    record_results(run.repo_path, {run.key: result})
    advance(run, t)


@must_run_on_ui
def terminate(run: Run) -> None:
    """Stop without a result; never claims success."""
    publish(run.repo_path, run.key, "")
    finish(run)


@must_run_on_ui
def finish(run: Run) -> None:
    global runs
    run.done = True
    run_key = (run.repo_path, run.key)
    if runs.get(run_key) is run:
        del runs[run_key]


@must_run_on_ui
def publish(repo_path: RepoPath, key: CacheKey, text: str) -> None:
    cache = store.current_state(repo_path)["github_ci_status"]
    if key in cache and cache.get(key) == text:
        return
    cache[key] = text
    store.update_state(repo_path, {"github_ci_status": cache})


@must_run_on_ui
def record_results(repo_path: RepoPath, states: Dict[CacheKey, KnownState]) -> None:
    """
    Publish plain CI states.  The dashboard renders them on the worker,
    so publish a changed copy instead of mutating the published cache.
    """
    current = store.current_state(repo_path)["github_ci_results"]
    changes = {
        key: state
        for key, state in states.items()
        if key not in current or current.get(key) != state
    }
    if not changes:
        return
    results = Cache(maxsize=current.maxsize)
    results.update(current)
    results.update(changes)
    store.update_state(repo_path, {"github_ci_results": results})


@must_run_on_ui
def reset_repo(repo_path: RepoPath) -> None:
    """Forget all runs and published results of `repo_path`."""
    global runs, lookups, batch_lookups, known_sources
    for run in list(runs.values()):
        if run.repo_path == repo_path:
            finish(run)
    for run_key in [run_key for run_key in lookups if run_key[0] == repo_path]:
        del lookups[run_key]
    batch_lookups.pop(repo_path, None)
    known_sources.pop(repo_path, None)
    state = store.current_state(repo_path)
    cache = state["github_ci_status"]
    if cache:
        cache.clear()
        store.update_state(repo_path, {"github_ci_status": cache})
    results = state["github_ci_results"]
    if results:
        store.update_state(repo_path, {"github_ci_results": Cache(maxsize=results.maxsize)})


@must_run_on_ui
def ensure_ticking() -> None:
    global ticking
    if not ticking:
        ticking = True
        schedule_tick()


def schedule_tick() -> None:
    sublime.set_timeout(tick, int(FRAME_INTERVAL * 1000))


@must_run_on_ui
def tick() -> None:
    global ticking, runs
    advance_all()
    if runs:
        schedule_tick()
    else:
        ticking = False


def now() -> float:
    return time.monotonic()


@must_run_on_ui
def on_settings_changed() -> None:
    global was_enabled, runs, known_sources
    enabled = is_enabled()
    if was_enabled and not enabled:
        for run in list(runs.values()):
            finish(run)
        for repo_path in list(known_sources):
            reset_repo(repo_path)
        # Re-enabling looks up the current HEADs again.
        forget_observed_heads()
    was_enabled = enabled


def on_remote_info_update(repo_path: RepoPath, state: store.RepoStore) -> None:
    # Runs on the publishing thread, which must not read `known_sources`.
    enqueue_on_ui(
        on_integration_url_observed,
        repo_path,
        integration_remote_url(state["remote_info"])
    )


@must_run_on_ui
def on_integration_url_observed(repo_path: RepoPath, url: Optional[str]) -> None:
    """
    Forget everything about `repo_path` if its integration remote now
    points elsewhere.  A renamed remote with the same URL, or a different
    URL for the same repository, does not count.
    """
    global known_sources
    known = known_sources.get(repo_path)
    if not known or url == known.remote_url:
        return
    try:
        source = source_of(github.parse_remote(url)) if url else None
    except ValueError:
        source = None
    if source != known.source:
        reset_repo(repo_path)
        # Look up the current HEAD in the new source.
        forget_observed_heads(repo_path)


def schedule_lookup(
    repo_path: RepoPath,
    key: CacheKey,
    repository: github.GitHubRepo,
    integration_url: str
) -> None:
    # `set_timeout` is safe on any thread and runs the callback on the UI thread.
    sublime.set_timeout(
        partial(lookup_if_settled, repo_path, key, repository, integration_url),
        int(SETTLE_DELAY * 1000)
    )


@must_run_on_ui
def lookup_if_settled(
    repo_path: RepoPath,
    key: CacheKey,
    repository: github.GitHubRepo,
    integration_url: str
) -> None:
    """
    Start a one-shot lookup unless HEAD moved on in the meantime, a push
    run owns the key, or a lookup of the key is already in flight.
    """
    global observed_heads, runs, lookups
    with observed_heads_lock:
        observed_head = observed_heads.get(repo_path)
    if not is_enabled() or observed_head != key:
        return
    run_key = (repo_path, key)
    if run_key in runs or run_key in lookups:
        return

    remember_source(repo_path, repository, integration_url)
    lookup = Lookup(repo_path, key, repository)
    lookups[run_key] = lookup
    dprint("CI status: look up {} at {}/{}".format(key, repository.owner, repository.repo))
    request_lookup(lookup)


def request_lookup(lookup: Lookup) -> None:
    run_new_daemon_thread(fetch_lookup, lookup)


def fetch_lookup(lookup: Lookup) -> None:
    observation = github.query_ci_status(lookup.repository, lookup.key[1])
    enqueue_on_ui(on_lookup_result, lookup, observation)


@must_run_on_ui
def on_lookup_result(lookup: Lookup, observation: github.CiObservation) -> None:
    global lookups
    run_key = (lookup.repo_path, lookup.key)
    if lookups.get(run_key) is not lookup:
        return
    del lookups[run_key]

    dprint("CI status: {} is {} {}".format(lookup.key, observation.state, observation.reason).rstrip())
    state = observation.state
    if state != "unknown":
        publish(lookup.repo_path, lookup.key, passive_text(state))
        record_results(lookup.repo_path, {lookup.key: state})


@must_run_on_ui
def start_batch_lookup(
    repo_path: RepoPath,
    keys: List[CacheKey],
    repository: github.GitHubRepo,
    integration_url: str
) -> None:
    global batch_lookups, runs
    if not is_enabled() or repo_path in batch_lookups:
        return

    remember_source(repo_path, repository, integration_url)
    keys = [key for key in keys if (repo_path, key) not in runs]
    if not keys:
        return
    batch = BatchLookup(repo_path, keys, repository)
    batch_lookups[repo_path] = batch
    dprint("CI status: look up {} branches at {}/{}".format(
        len(keys), repository.owner, repository.repo
    ))
    request_batch_lookup(batch)


def request_batch_lookup(batch: BatchLookup) -> None:
    run_new_daemon_thread(fetch_batch_lookup, batch)


def fetch_batch_lookup(batch: BatchLookup) -> None:
    observations = github.query_ci_statuses(batch.repository, [sha for _, sha in batch.keys])
    enqueue_on_ui(on_batch_lookup_result, batch, observations)


@must_run_on_ui
def on_batch_lookup_result(batch: BatchLookup, observations: Dict[str, github.CiObservation]) -> None:
    global batch_lookups, runs
    repo_path = batch.repo_path
    if batch_lookups.get(repo_path) is not batch:
        return
    del batch_lookups[repo_path]

    states: Dict[CacheKey, KnownState] = {}
    unknown: List[str] = []
    for key in batch.keys:
        observation = observations.get(key[1])
        if (repo_path, key) in runs or not observation:
            continue
        if observation.state == "unknown":
            unknown.append("{} ({})".format(key[0], observation.reason))
            continue
        states[key] = observation.state

    dprint("CI status: looked up {} branches{}".format(
        len(states), ", unknown: {}".format(", ".join(unknown)) if unknown else ""
    ))
    cache = store.current_state(repo_path)["github_ci_status"]
    for key, state in states.items():
        text = passive_text(state)
        # Only replace what is there, e.g. an earlier `[red]`, with "".
        if text or key in cache:
            publish(repo_path, key, text)
    record_results(repo_path, states)


def passive_text(state: KnownState) -> str:
    """
    Render a passive observation.  Success and pending render nothing,
    so a rerun clears an earlier `[red]`.
    """
    if state == "failure":
        return "[red]"
    return ""


def forget_observed_heads(repo_path: Optional[RepoPath] = None) -> None:
    global observed_heads
    with observed_heads_lock:
        if repo_path is None:
            observed_heads.clear()
        else:
            observed_heads.pop(repo_path, None)


def passive_key(cmd: GitCommand, head: HeadState) -> Optional[CacheKey]:
    if not head.commit_hash:
        return None
    if not head.detached and (not head.branch or not head.remote or head.ahead):
        return None
    if (
        cmd.in_rebase()
        or cmd.in_merge()
        or cmd.in_cherry_pick()
        or cmd.in_revert()
        or cmd.in_bisect()
    ):
        return None
    return (head.branch or DETACHED, head.commit_hash)


def branch_keys(branches: Sequence[Branch]) -> List[CacheKey]:
    """
    Return the keys of the local branches that look pushed, the most
    recently committed first, at most `MAX_BATCH_SIZE`.
    """
    eligible = sorted(
        (
            branch for branch in branches
            if branch.is_local
            and branch.upstream
            and branch.upstream.status != "gone"
            and "ahead" not in branch.upstream.status
        ),
        key=lambda branch: -branch.committerdate
    )
    return [(branch.name, branch.commit_hash) for branch in eligible[:MAX_BATCH_SIZE]]


def integration_repository(
    remote_info: RemoteInfoBlob,
    verbose: bool = True
) -> Optional[Tuple[github.GitHubRepo, str]]:
    """
    Return the integration repository to query, and its remote url, if
    it is on a known GitHub host and we have a token for it.  With
    `verbose`, log why a repository does not qualify.
    """
    log = dprint if verbose else lambda *args: None
    integration_url = integration_remote_url(remote_info)
    if not integration_url:
        log("CI status: skip, no integration remote")
        return None

    try:
        repository = github.parse_remote(integration_url)
    except ValueError:
        log("CI status: skip, not a GitHub remote")
        return None

    if not github.is_github_host(repository.fqdn):
        log("CI status: skip, {} is not a known GitHub host".format(repository.fqdn))
        return None
    if not repository.token:
        log("CI status: skip, no API token for {}".format(repository.fqdn))
        return None
    return repository, integration_url


def single_branch_update(destinations: List[PushDestination]) -> Optional[Tuple[str, str]]:
    """
    Return the push url and the qualified destination ref of a push that
    updated exactly one branch on exactly one destination, otherwise `None`.
    """
    if len(destinations) != 1 or len(destinations[0].refs) != 1:
        return None
    destination = destinations[0]
    ref = destination.refs[0]
    # fast-forward, forced update, new ref, up to date; not deletions or rejections
    if ref.flag not in (" ", "+", "*", "="):
        return None
    if not ref.dst.startswith("refs/heads/"):
        return None
    return destination.url, ref.dst


def discovery_schedule(repo_path: RepoPath) -> Sequence[float]:
    """
    Look for checks less eagerly if the repository has no GitHub Actions
    workflows.  Their absence is only a hint, external CI can still report
    checks, so we look anyway.
    """
    try:
        names = os.listdir(os.path.join(repo_path, ".github", "workflows"))
    except OSError:
        names = []
    if any(name.endswith((".yml", ".yaml")) for name in names):
        return DISCOVERY_SCHEDULE
    return QUIET_DISCOVERY_SCHEDULE


def is_mirror(remote_info: RemoteInfoBlob, remote: str) -> bool:
    config = next((r.config for r in remote_info.remotes if r.name == remote), [])
    value = last(by_raw_key(config, "mirror"), None)
    return value is not None and value.lower() in ("true", "yes", "on", "1", "")


def integration_remote_url(remote_info: RemoteInfoBlob) -> Optional[str]:
    return next(
        (r.url for r in remote_info.remotes if r.name == remote_info.integration_remote),
        None
    )


def source_of(repository: github.GitHubRepo) -> Source:
    return (repository.fqdn.lower(), repository.owner.lower(), repository.repo.lower())


def has_associated_window(repo_path: RepoPath) -> bool:
    for window in sublime.windows():
        if any(is_within(folder, repo_path) or is_within(repo_path, folder) for folder in window.folders()):
            return True
        for view in window.views():
            if view.settings().get("git_savvy.repo_path") == repo_path:
                return True
            file_name = view.file_name()
            if file_name and is_within(file_name, repo_path):
                return True
    return False


def is_within(path: str, directory: str) -> bool:
    path, directory = os.path.normcase(os.path.normpath(path)), os.path.normcase(os.path.normpath(directory))
    return path == directory or path.startswith(directory.rstrip(os.sep) + os.sep)
