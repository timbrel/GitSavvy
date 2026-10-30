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

The status bar only selects the applicable string; it never schedules
anything or computes elapsed time.  All run and lookup state lives on the
UI thread, only the HTTPS requests run on short-lived background threads.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from functools import partial
import os
import threading
import time

import sublime

from GitSavvy.common.util.debug import dprint
from GitSavvy.core import store
from GitSavvy.core.fns import last
from GitSavvy.core.git_mixins.remotes import by_raw_key
from GitSavvy.core.runtime import enqueue_on_ui, run_new_daemon_thread
from GitSavvy.core.settings import app_settings
from GitSavvy.core.ui__busy_spinner import BusyIndicators
from GitSavvy.core.utils import eat_but_log_errors
from GitSavvy.github import github

from typing import Callable, Dict, List, Literal, NamedTuple, Optional, Tuple, TYPE_CHECKING
if TYPE_CHECKING:
    from GitSavvy.core.git_command import GitCommand
    from GitSavvy.core.git_mixins.remotes import PushDestination, RemoteInfoBlob
    from GitSavvy.core.git_mixins.status import HeadState


__all__ = (
    "on_head_observed",
    "on_successful_push",
    "start",
    "stop",
)


SETTING = "github_ci_status_in_status_bar"
POLL_INTERVAL = 10.0  # [seconds], after the previous request completed
DISCOVERY_GRACE = 60.0  # [seconds] without any checks before giving up
GREEN_CONFIRMATION = 10.0  # [seconds] between two successful observations
DEADLINE = 30 * 60.0  # [seconds] since the run started
RETRY_DELAYS = (10.0, 20.0, 40.0)  # [seconds] after consecutive transient failures
FRAME_INTERVAL = 0.2  # [seconds]
LABEL_DURATION = 3.0  # [seconds] to show "CI:" while watching
ACKNOWLEDGE_DURATION = 5.0  # [seconds] to show a settled result verbosely
FRAMES = BusyIndicators.STARS
SETTLE_DELAY = 2.0  # [seconds] HEAD must stay put before a passive lookup

RepoPath = str
CacheKey = Tuple[str, str]  # (branch name, full commit hash)
RunKey = Tuple[RepoPath, CacheKey]
Source = Tuple[str, str, str]  # lowercased (host, owner, repo) of the queried repository
Result = Literal["success", "failure"]
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


runs: Dict[RunKey, Run] = {}
lookups: Dict[RunKey, Lookup] = {}
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
    `remote_info` to the store.
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
        integration_url
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
        enqueue_on_ui(schedule_lookup, repo_path, key, repository, integration_url)


def start() -> None:
    global unsubscribe_store, settings, was_enabled
    stop()
    settings = sublime.load_settings("GitSavvy.sublime-settings")
    settings.add_on_change(__name__, on_settings_changed)
    was_enabled = is_enabled()
    unsubscribe_store = store.subscribe("*", {"remote_info"}, on_remote_info_update)


def stop() -> None:
    global unsubscribe_store, settings
    if settings is not None:
        settings.clear_on_change(__name__)
        settings = None
    if unsubscribe_store is not None:
        unsubscribe_store()
        unsubscribe_store = None
    for run in list(runs.values()):
        finish(run)
    lookups.clear()
    forget_observed_heads()
    known_sources.clear()


def is_enabled() -> bool:
    return bool(app_settings.get(SETTING, True))


def start_run(
    repo_path: RepoPath,
    key: CacheKey,
    repository: github.GitHubRepo,
    push_target: Optional[github.PushTarget],
    integration_url: str
) -> None:
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
        next_request_at=started_at,
    )
    runs[(repo_path, key)] = run
    dprint("CI status: watch {} at {}/{}".format(key, repository.owner, repository.repo))
    advance(run, started_at)
    ensure_ticking()


def remember_source(repo_path: RepoPath, repository: github.GitHubRepo, integration_url: str) -> None:
    known_source = KnownSource(source_of(repository), integration_url)
    previous_source = known_sources.get(repo_path)
    if previous_source and previous_source.source != known_source.source:
        reset_repo(repo_path)
    known_sources[repo_path] = known_source


def advance_all() -> None:
    t = now()
    for run in list(runs.values()):
        advance(run, t)


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


def request_observation(run: Run) -> None:
    run_new_daemon_thread(fetch_observation, run, run.push_target)


def fetch_observation(run: Run, push_target: Optional[github.PushTarget]) -> None:
    observation = github.query_ci_status(run.repository, run.key[1], push_target)
    enqueue_on_ui(on_observation, run, observation)


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
        if run.checks_seen_at is None and t - run.started_at >= DISCOVERY_GRACE:
            dprint("CI status: {} has no checks".format(run.key))
            terminate(run)
            return
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


def settle(run: Run, result: Result, t: float) -> None:
    run.result = result
    run.settled_at = t
    advance(run, t)


def terminate(run: Run) -> None:
    """Stop without a result; never claims success."""
    publish(run.repo_path, run.key, "")
    finish(run)


def finish(run: Run) -> None:
    run.done = True
    run_key = (run.repo_path, run.key)
    if runs.get(run_key) is run:
        del runs[run_key]


def publish(repo_path: RepoPath, key: CacheKey, text: str) -> None:
    cache = store.current_state(repo_path)["github_ci_status"]
    if key in cache and cache.get(key) == text:
        return
    cache[key] = text
    store.update_state(repo_path, {"github_ci_status": cache})


def reset_repo(repo_path: RepoPath) -> None:
    """Forget all runs and published results of `repo_path`."""
    for run in list(runs.values()):
        if run.repo_path == repo_path:
            finish(run)
    for run_key in [run_key for run_key in lookups if run_key[0] == repo_path]:
        del lookups[run_key]
    known_sources.pop(repo_path, None)
    cache = store.current_state(repo_path)["github_ci_status"]
    if cache:
        cache.clear()
        store.update_state(repo_path, {"github_ci_status": cache})


def ensure_ticking() -> None:
    global ticking
    if not ticking:
        ticking = True
        schedule_tick()


def schedule_tick() -> None:
    sublime.set_timeout(tick, int(FRAME_INTERVAL * 1000))


def tick() -> None:
    global ticking
    advance_all()
    if runs:
        schedule_tick()
    else:
        ticking = False


def now() -> float:
    return time.monotonic()


def on_settings_changed() -> None:
    global was_enabled
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
    # Runs on the publishing thread.  The source only changes if the
    # integration remote now points elsewhere; a renamed remote with the
    # same URL, or a different URL for the same repository, does not count.
    known = known_sources.get(repo_path)
    if not known:
        return
    url = integration_remote_url(state["remote_info"])
    if url == known.remote_url:
        return
    try:
        source = source_of(github.parse_remote(url)) if url else None
    except ValueError:
        source = None
    if source != known.source:
        enqueue_on_ui(reset_repo_if_source_is, repo_path, known)


def reset_repo_if_source_is(repo_path: RepoPath, known: KnownSource) -> None:
    if known_sources.get(repo_path) == known:
        reset_repo(repo_path)
        # Look up the current HEAD in the new source.
        forget_observed_heads(repo_path)


def schedule_lookup(
    repo_path: RepoPath,
    key: CacheKey,
    repository: github.GitHubRepo,
    integration_url: str
) -> None:
    sublime.set_timeout(
        partial(lookup_if_settled, repo_path, key, repository, integration_url),
        int(SETTLE_DELAY * 1000)
    )


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
    if not is_enabled() or observed_heads.get(repo_path) != key:
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


def on_lookup_result(lookup: Lookup, observation: github.CiObservation) -> None:
    run_key = (lookup.repo_path, lookup.key)
    if lookups.get(run_key) is not lookup:
        return
    del lookups[run_key]

    dprint("CI status: {} is {} {}".format(lookup.key, observation.state, observation.reason).rstrip())
    text = passive_text(observation.state)
    if text is not None:
        publish(lookup.repo_path, lookup.key, text)


def passive_text(state: github.CiState) -> Optional[str]:
    """
    Render a passive observation; `None` keeps what was published before.
    Success and pending render nothing, so a rerun clears an earlier `[red]`.
    """
    if state == "failure":
        return "[red]"
    if state == "unknown":
        return None
    return ""


def forget_observed_heads(repo_path: Optional[RepoPath] = None) -> None:
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
