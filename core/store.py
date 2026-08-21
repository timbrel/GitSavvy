from __future__ import annotations
from collections import defaultdict, deque
from functools import partial
import threading
import uuid

from .utils import eat_but_log_errors
from GitSavvy.core import app_state


from typing import (
    AbstractSet, Callable, cast, DefaultDict, Deque, Dict, List, Optional, Set, Tuple,
    TypedDict, TYPE_CHECKING
)

if TYPE_CHECKING:
    from GitSavvy.core.git_mixins.active_branch import Commit
    from GitSavvy.core.git_mixins.branches import Branch
    from GitSavvy.core.git_mixins.worktrees import Worktree
    from GitSavvy.core.git_mixins.stash import Stash
    from GitSavvy.core.git_mixins.tags import TagList
    from GitSavvy.core.git_mixins.remotes import RemoteInfoBlob
    from GitSavvy.core.git_mixins.status import HeadState, WorkingDirState

    class RepoStore(TypedDict, total=False):
        status: WorkingDirState
        head: HeadState
        long_status: str
        short_status: str
        branches: List[Branch]
        remotes: Dict[str, str]
        remote_info: RemoteInfoBlob
        worktrees: List[Worktree]
        remotes_with_no_tags_set: Set[str]
        local_tags: TagList
        last_branches: Deque[Optional[str]]
        last_branch_used_to_pull_from: Optional[str]
        last_branch_used_to_rebase_from: Optional[str]
        last_remote_used: Optional[str]
        last_remote_used_for_push: Optional[str]
        last_remote_used_with_option_all: Optional[str]
        last_reset_mode_used: Optional[str]
        short_hash_length: int
        skipped_files: List[str]
        ahead_behind_consecutive_failures: int
        ahead_behind_retry_at: float
        last_commit_graph_write: float
        stashes: List[Stash]
        recent_commits: List[Commit]
        descriptions: Dict[str, str]
        default_graph_options: Dict[str, str]
    RepoPath = str
    SubscriberKey = str
    Keys = AbstractSet[str]


def initial_state() -> RepoStore:
    return {
        "last_branches": deque([None] * 2, 2),
    }


PERSISTED_KEYS = {
    "ahead_behind_consecutive_failures",
    "ahead_behind_retry_at",
    "last_branch_used_to_pull_from",
    "last_branch_used_to_rebase_from",
    "last_commit_graph_write",
    "last_remote_used",
    "last_remote_used_for_push",
    "last_remote_used_with_option_all",
}
PERSISTED_STATE_KEY = "by_repo"

state = defaultdict(initial_state)  # type: DefaultDict[RepoPath, RepoStore]
subscribers = {}  # type: Dict[SubscriberKey, Tuple[RepoPath, Keys, Callable]]

lock = threading.Lock()


def update_state(repo_path: RepoPath, partial_state: RepoStore) -> None:
    with lock:
        state[repo_path].update(partial_state)
        _persist_state(repo_path, partial_state)
    notify_all(repo_path, partial_state.keys(), state[repo_path])


def notify_all(
    repo_path: RepoPath,
    updated_keys: Keys,
    current_state: RepoStore
) -> None:
    for (subscribed_repo_path, keys, fn) in subscribers.values():
        if (
            subscribed_repo_path in {repo_path, "*"}
            and updated_keys & keys
        ):
            with eat_but_log_errors():
                fn(repo_path, current_state)


def current_state(repo_path: RepoPath) -> RepoStore:
    return state[repo_path]


def subscribe(repo_path: RepoPath, keys: Keys, fn: Callable) -> Callable[[], None]:
    key = uuid.uuid4().hex
    subscribers[key] = (repo_path, keys, fn)
    return partial(_unsubscribe, key)


def _unsubscribe(key: SubscriberKey) -> None:
    subscribers.pop(key, None)


def _persist_state(repo_path: RepoPath, partial_state: RepoStore) -> None:
    persistent_update = {
        key: value
        for key, value in partial_state.items()
        if key in PERSISTED_KEYS
    }
    if not persistent_update:
        return

    by_repo = app_state.get(PERSISTED_STATE_KEY, {})
    if not isinstance(by_repo, dict):
        by_repo = {}
    repo_state = by_repo.get(repo_path, {})
    if not isinstance(repo_state, dict):
        repo_state = {}

    app_state.set(PERSISTED_STATE_KEY, {
        **by_repo,
        repo_path: {**repo_state, **persistent_update}
    })


def load_app_state() -> None:
    by_repo = app_state.get(PERSISTED_STATE_KEY, {})
    if not isinstance(by_repo, dict):
        return

    with lock:
        for repo_path, repo_state in by_repo.items():
            if not isinstance(repo_path, str) or not isinstance(repo_state, dict):
                continue
            state[repo_path].update(cast("RepoStore", {
                key: value
                for key, value in repo_state.items()
                if key in PERSISTED_KEYS
            }))
