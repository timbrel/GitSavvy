from collections import defaultdict
import threading


from .utils import Cache

MYPY = False
if MYPY:
    from typing import Any, DefaultDict, Dict, Optional, Tuple, TypedDict
    from GitSavvy.core.git_mixins.status import WorkingDirState

    RepoPath = str
    RepoStore = TypedDict(
        'RepoStore',
        {
            "status": WorkingDirState,
            "last_remote_used": Optional[str],
            "last_remote_used_with_option_all": Optional[str],
            "short_hash_length": int,
        },
        total=False
    )

state = defaultdict(lambda: {})  # type: DefaultDict[RepoPath, RepoStore]
cache = Cache(maxsize=512)  # type: Dict[Tuple, Any]


lock = threading.Lock()


def update_state(repo_path, partial_state):
    # type: (RepoPath, RepoStore) -> None
    with lock:
        state[repo_path].update(partial_state)


def current_state(repo_path):
    # type: (RepoPath) -> RepoStore
    return state[repo_path]
