from collections import defaultdict
import threading


MYPY = False
if MYPY:
    from typing import DefaultDict, Dict, Tuple, TypedDict

    RepoPath = str
    RepoStore = TypedDict(
        'RepoStore',
        {
            "short_hash_length": int,
        },
        total=False
    )

state = defaultdict(lambda: {})  # type: DefaultDict[RepoPath, RepoStore]
cache = {}  # type: Dict[Tuple, str]


lock = threading.Lock()


def update_state(repo_path, partial_state):
    # type: (RepoPath, RepoStore) -> None
    with lock:
        state[repo_path].update(partial_state)


def current_state(repo_path):
    # type: (RepoPath) -> RepoStore
    return state[repo_path]
