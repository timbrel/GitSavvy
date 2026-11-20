from __future__ import annotations
from concurrent.futures import Future
from functools import partial
import subprocess
from threading import Lock, local
from typing import Callable, Generic, TYPE_CHECKING, TypeVar

from typing_extensions import ParamSpec

from .exceptions import AbortedGitCall
from .runtime import start_future
from .utils import abort_proc


P = ParamSpec("P")
T = TypeVar("T")
_scope = local()


def run_as_abortable_future(fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> AbortableFuture[T]:
    future: AbortableFuture[T] = AbortableFuture()
    start_future(future, partial(future._run, partial(fn, *args, **kwargs)))
    return future


# Future isn't subscriptable at runtime in Python 3.8.
if TYPE_CHECKING:
    from concurrent.futures import Future as _Future
else:
    class _Future(Future, Generic[T]):
        pass


class AbortableFuture(_Future[T]):
    def __init__(self) -> None:
        super().__init__()
        self._cancellation = GitCancellation()

    def abort(self) -> None:
        """Request that this job's managed Git calls stop, without cancelling the thread."""
        if not self.done():
            self._cancellation.abort()

    def _run(self, fn: Callable[[], T]) -> T:
        _scope.cancellation = self._cancellation
        try:
            self._cancellation.check()
            return fn()
        finally:
            # Drop the scope before start_future publishes the result and invokes callbacks.
            del _scope.cancellation


def current_git_cancellation() -> GitCancellation | None:
    return getattr(_scope, "cancellation", None)


class GitCancellation:
    """Abort the managed Git processes of one operation, including subsequent calls."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._aborted = False
        self._running: set[subprocess.Popen] = set()

    def abort(self) -> None:
        with self._lock:
            if self._aborted:
                return
            self._aborted = True
            for proc in self._running:
                if proc.poll() is None:
                    abort_proc(proc)

    def check(self) -> None:
        with self._lock:
            if self._aborted:
                raise AbortedGitCall("Aborted by user.", show_panel=False)

    def track(self, proc: subprocess.Popen) -> None:
        with self._lock:
            if self._aborted:
                # Close raced with Popen. Let git() drain and reap as usual.
                abort_proc(proc)
            else:
                self._running.add(proc)

    def forget(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._running.discard(proc)
