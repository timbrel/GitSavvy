"""
Observe GitHub's commit-check rollup for a commit after pushing it.

A run is started by `on_successful_push` and owns its complete lifecycle:
polling, animation, timed text transitions, and clearing.  It publishes
rendered strings into the store at

    store.current_state(repo_path)["github_ci_status"][(branch, full_sha)]

The status bar only selects the applicable string; it never schedules
anything or computes elapsed time.  All run state lives on the UI thread,
only the HTTPS requests run on short-lived background threads.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import os
import time

import sublime

from GitSavvy.common.util.debug import dprint
from GitSavvy.core import store
from GitSavvy.core.fns import last
from GitSavvy.core.git_mixins.remotes import by_raw_key
from GitSavvy.core.runtime import enqueue_on_ui, run_new_daemon_thread
from GitSavvy.core.settings import app_settings
from GitSavvy.core.ui__busy_spinner import BusyIndicators
from GitSavvy.github import github

from typing import Callable, Dict, List, Literal, NamedTuple, Optional, Tuple, TYPE_CHECKING
if TYPE_CHECKING:
    from GitSavvy.core.git_command import GitCommand
    from GitSavvy.core.git_mixins.remotes import PushDestination, RemoteInfoBlob


__all__ = (
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

RepoPath = str
CacheKey = Tuple[str, str]  # (branch name, full commit hash)
RunKey = Tuple[RepoPath, CacheKey]
Source = Tuple[str, str, str]  # lowercased (host, owner, repo) of the queried repository
Result = Literal["success", "failure"]


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
    checks_seen: bool = False
    first_success_at: Optional[float] = None
    consecutive_failures: int = 0
    result: Optional[Result] = None
    settled_at: float = 0.0
    done: bool = False

    @property
    def deadline(self) -> float:
        return self.started_at + DEADLINE


runs: Dict[RunKey, Run] = {}
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

    integration_url = integration_remote_url(remote_info)
    if not integration_url:
        dprint("CI status: skip, no integration remote")
        return

    try:
        repository = github.parse_remote(integration_url)
        pushed = github.parse_remote(push_url)
    except ValueError:
        dprint("CI status: skip, not a GitHub remote")
        return

    if not github.is_github_host(repository.fqdn):
        dprint("CI status: skip, {} is not a known GitHub host".format(repository.fqdn))
        return
    if pushed.fqdn.lower() != repository.fqdn.lower():
        dprint("CI status: skip, pushed to a different host than {}".format(repository.fqdn))
        return
    if not repository.token:
        dprint("CI status: skip, no API token for {}".format(repository.fqdn))
        return

    enqueue_on_ui(
        start_run,
        repo_path,
        (source_branch, source_sha),
        repository,
        github.PushTarget(pushed.owner, pushed.repo, destination_ref),
        integration_url
    )


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

    known_source = KnownSource(source_of(repository), integration_url)
    previous_source = known_sources.get(repo_path)
    if previous_source and previous_source.source != known_source.source:
        reset_repo(repo_path)
    known_sources[repo_path] = known_source

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
        publish(run, pending_text(t - run.started_at))
        return

    acknowledged = t - run.settled_at >= ACKNOWLEDGE_DURATION
    if run.result == "success":
        publish(run, "" if acknowledged else "[CI: ok]")
    else:
        publish(run, "[red]" if acknowledged else "[CI: failed]")
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

    if state == "failure":
        settle(run, "failure", t)
        return

    if state == "success":
        run.checks_seen = True
        if run.first_success_at is None:
            run.first_success_at = t
        elif t - run.first_success_at >= GREEN_CONFIRMATION:
            settle(run, "success", t)
            return
        run.next_request_at = t + POLL_INTERVAL

    elif state == "pending":
        run.checks_seen = True
        run.next_request_at = t + POLL_INTERVAL

    elif state == "no_checks":
        if not run.checks_seen and t - run.started_at >= DISCOVERY_GRACE:
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
    publish(run, "")
    finish(run)


def finish(run: Run) -> None:
    run.done = True
    run_key = (run.repo_path, run.key)
    if runs.get(run_key) is run:
        del runs[run_key]


def publish(run: Run, text: str) -> None:
    cache = store.current_state(run.repo_path)["github_ci_status"]
    if run.key in cache and cache.get(run.key) == text:
        return
    cache[run.key] = text
    store.update_state(run.repo_path, {"github_ci_status": cache})


def reset_repo(repo_path: RepoPath) -> None:
    """Forget all runs and published results of `repo_path`."""
    for run in list(runs.values()):
        if run.repo_path == repo_path:
            finish(run)
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
