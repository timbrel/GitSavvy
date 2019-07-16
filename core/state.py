from collections import defaultdict
from functools import wraps
import threading

if False:
    from typing import Callable, DefaultDict, Dict, List, Optional, TypeVar
    from mypy_extensions import TypedDict
    from .git_mixins.status import FileStatus

    T = TypeVar('T')

    RepoPath = str
    RepoStatus = TypedDict(
        'RepoStatus',
        {
            'detached': bool,
            'branch': Optional[str],
            'remote': Optional[str],
            'clean': bool,
            'ahead': Optional[int],
            'behind': Optional[int],
            'gone': bool,
            'rebasing': bool,
            'rebase_branch_name': Optional[str],
            'merging': bool,
            'merge_head': Optional[str],
            'file_statuses': List[FileStatus],
            'staged': List[FileStatus],
            'unstaged': List[FileStatus],
            'untracked': List[FileStatus],
            'conflicts': List[FileStatus],
            'short_status': str,
        },
        total=False,
    )
    Subscriber = Callable[[RepoPath, RepoStatus], None]
    SubscriberKey = str


State = defaultdict(lambda: {})  # type: DefaultDict[RepoPath, RepoStatus]
subscribers = {}  # type: Dict[SubscriberKey, Subscriber]


def sync(lock):
    # type: (threading.Lock) -> Callable[[T], T]
    def decorator(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            with lock:
                return fn(*a, **kw)

        return wrapper

    return decorator


lock = threading.Lock()
atomic = sync(lock)


def update_state(repo_path, partial_state):
    # type: (RepoPath, RepoStatus) -> None
    with lock:
        State[repo_path].update(partial_state)
    notify_all(repo_path, State[repo_path])


def notify_all(repo_path, state):
    # type: (RepoPath, RepoStatus) -> None
    for fn in subscribers.values():
        fn(repo_path, state)


@atomic
def subscribe(key, fn):
    # type: (SubscriberKey, Subscriber) -> None
    subscribers[key] = fn


@atomic
def unsubscribe(key):
    # type: (SubscriberKey) -> None
    subscribers.pop(key)


def current_state(repo_path):
    # type: (RepoPath) -> RepoStatus
    return State[repo_path]
