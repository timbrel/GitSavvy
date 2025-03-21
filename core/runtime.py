from __future__ import annotations
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from functools import lru_cache, partial, wraps
import inspect
import os
import sys
import time
import threading
import traceback
import uuid

import sublime
import sublime_plugin


from .exceptions import GitSavvyError
from . import utils

from typing import (
    Any, Callable, Dict, Generator, Literal, Optional, Sequence, Tuple, TypeVar, Union,
    overload)

from typing_extensions import Concatenate as Con, ParamSpec, TypeAlias
P = ParamSpec('P')
T = TypeVar('T')
F = TypeVar('F', bound=Callable[..., Any])
Callback = Tuple[Callable, Tuple[Any, ...], Dict[str, Any]]
ReturnValue = Any

View = sublime.View
Edit = sublime.Edit


UI_THREAD_NAME = None  # type: Optional[str]
WORKER_THREAD_NAME = None  # type: Optional[str]
savvy_executor = ThreadPoolExecutor(max_workers=1)
auto_timeout = threading.local()
_enqueued_tasks = utils.Counter()


def determine_thread_names():
    def ui_callback():
        global UI_THREAD_NAME
        UI_THREAD_NAME = threading.current_thread().name

    def worker_callback():
        global WORKER_THREAD_NAME
        WORKER_THREAD_NAME = threading.current_thread().name

    sublime.set_timeout(ui_callback)
    sublime.set_timeout_async(worker_callback)


GITSAVVY__ = "{0}GitSavvy{0}".format(os.sep)
CORE_COMMANDS__ = "core{0}commands{0}".format(os.sep)


@contextmanager
def user_friendly_traceback(exception_s: type[BaseException] | tuple[type[BaseException], ...]):
    try:
        yield
    except exception_s as e:
        print(f"Abort: {e}  ")
        _, _, tb = sys.exc_info()
        found_culprit = False
        for frame in reversed(traceback.extract_tb(tb)):
            relative_filename = frame.filename.split(GITSAVVY__)[-1]
            if not found_culprit and relative_filename.startswith(CORE_COMMANDS__):
                found_culprit = True
                left_column = f"|> {relative_filename}:{frame.lineno}"
            else:
                left_column = f"|  {relative_filename}:{frame.lineno}"
            print(f"{left_column:<40} {frame.line}")


def it_runs_on_ui():
    # type: () -> bool
    return threading.current_thread().name == UI_THREAD_NAME


def ensure_on_ui(fn, *args, **kwargs):
    # type: (Callable[P, T], P.args, P.kwargs) -> None
    if it_runs_on_ui():
        fn(*args, **kwargs)
    else:
        enqueue_on_ui(fn, *args, **kwargs)


def it_runs_on_worker():
    # type: () -> bool
    return threading.current_thread().name == WORKER_THREAD_NAME


def ensure_on_worker(fn, *args, **kwargs):
    # type: (Callable[P, T], P.args, P.kwargs) -> None
    if it_runs_on_worker():
        fn(*args, **kwargs)
    else:
        enqueue_on_worker(fn, *args, **kwargs)

# `enqueue_on_*` functions emphasize that we run two queues and
# just put tasks on it.  In contrast to `set_timeout_*` which
# emphasizes that we delay or defer something. (In particular
# `set_timeout_async` is somewhat a misnomer because both calls
# return immediately.)
# Both functions have the standard python callable interface
# `(f, *a, *kw)`, which is used in e.g. `partial` or
# `executor.submit`. This has the advantage that we can swap
# the functions to change the behavior without changing the
# arguments.


def enqueue_on_ui(fn, *args, **kwargs):
    # type: (Callable[P, T], P.args, P.kwargs) -> None
    sublime.set_timeout(partial(fn, *args, **kwargs))


def enqueue_on_savvy(fn, *args, **kwargs):
    # type: (Callable[P, T], P.args, P.kwargs) -> None
    savvy_executor.submit(fn, *args, **kwargs)


def enqueue_on_worker(fn, *args, **kwargs):
    # type: (Callable[P, T], P.args, P.kwargs) -> None
    action = partial(fn, *args, **kwargs)

    def task():
        _enqueued_tasks.dec()
        action()

    _enqueue_on_worker(task)
    _enqueued_tasks.inc()


def run_when_worker_is_idle(fn, *args, **kwargs):
    action = partial(fn, *args, **kwargs)

    def task():
        if _enqueued_tasks.count() == 0:
            action()
        else:
            sublime.set_timeout_async(task)

    _enqueue_on_worker(task)


def _enqueue_on_worker(fn):
    # type: (Callable[[], T]) -> None
    fn_ = user_friendly_traceback((RuntimeError, GitSavvyError))(fn)
    sublime.set_timeout_async(fn_)


def run_on_new_thread(fn, *args, **kwargs):
    # type: (Callable[P, T], P.args, P.kwargs) -> None
    threading.Thread(target=_set_timout(fn), args=args, kwargs=kwargs).start()


def run_new_daemon_thread(fn, *args, **kwargs):
    # type: (Callable[P, T], P.args, P.kwargs) -> None
    threading.Thread(target=_set_timout(fn), args=args, kwargs=kwargs, daemon=True).start()


def _set_timout(fn):
    def wrapped(*args, **kwargs):
        auto_timeout.value = None
        return fn(*args, **kwargs)
    return wrapped


def on_worker(fn):
    # type: (Callable[P, T]) -> Callable[P, None]
    @wraps(fn)
    def wrapped(*a, **kw):
        # type: (P.args, P.kwargs) -> None
        enqueue_on_worker(fn, *a, **kw)
    return wrapped


def on_new_thread(fn):
    # type: (Callable[P, T]) -> Callable[P, None]
    @wraps(fn)
    def wrapped(*a, **kw):
        # type: (P.args, P.kwargs) -> None
        run_on_new_thread(fn, *a, **kw)
    return wrapped


def run_as_future(fn, *args, **kwargs):
    # type: (Callable[P, T], P.args, P.kwargs) -> Future[T]
    fut = Future()  # type: Future[T]

    def task():
        # type: () -> None
        fut.set_running_or_notify_cancel()
        try:
            rv = fn(*args, **kwargs)
        except Exception as e:
            fut.set_exception(e)
        else:
            fut.set_result(rv)
    run_on_new_thread(task)
    return fut


def run_or_timeout(fn, timeout):
    # type: (Callable[[], T], float) -> T
    cond = threading.Condition()
    result: T
    exc: Exception

    def program():
        # type: () -> None
        nonlocal cond, exc, result
        try:
            result = fn()
        except Exception as e:
            exc = e
        finally:
            with cond:
                cond.notify_all()

    with cond:
        run_on_new_thread(program)
        if not cond.wait(timeout):
            raise TimeoutError()

    try:
        raise exc
    except UnboundLocalError:
        return result


def run_and_check_timeout(fn, timeout, callback):
    # type: (Callable[[], T], float, Union[Callable[[], None], Sequence[Callable[[], None]]]) -> T
    cond = threading.Condition()
    callbacks = callback if isinstance(callback, list) else [callback]

    def checker():
        # type: () -> None
        with cond:
            if not cond.wait(timeout):
                for callback in callbacks:
                    callback()

    run_on_new_thread(checker)
    try:
        return fn()
    finally:
        with cond:
            cond.notify_all()


lock = threading.Lock()
COMMANDS = {}  # type: Dict[str, Callback]
RESULTS = {}  # type: Dict[str, ReturnValue]


@overload
def run_as_text_command(fn, view, *args, **kwargs):
    # type: (Callable[Con[View, P], T], View, P.args, P.kwargs) -> Optional[T]
    ...


@overload
def run_as_text_command(fn, view, *args, **kwargs):  # noqa: F811
    # type: (Callable[Con[View, Edit, P], T], View, P.args, P.kwargs) -> Optional[T]
    ...


def run_as_text_command(fn, view, *args, **kwargs):  # noqa: F811
    # type: (Union[Callable[Con[View, P], T], Callable[Con[View, Edit, P], T]], View, P.args, P.kwargs) -> Optional[T]
    token = uuid.uuid4().hex
    with lock:
        COMMANDS[token] = (fn, (view, ) + args, kwargs)
    view.run_command('gs_generic_text_cmd', {'token': token})
    with lock:
        # If the view has been closed, Sublime will not run
        # text commands on it anymore (but also not throw).
        # For now, we stay close, don't raise and just return
        # `None`.
        rv = RESULTS.pop(token, None)
    return rv


@overload
def text_command(fn):
    # type: (Callable[Con[View, Edit, P], T]) -> Callable[Con[View, P], Optional[T]]
    ...


@overload
def text_command(fn):  # noqa: F811
    # type: (Callable[Con[View, P], T]) -> Callable[Con[View, P], Optional[T]]
    ...


def text_command(fn):  # noqa: F811
    # type: (Union[Callable[Con[View, P], T], Callable[Con[View, Edit, P], T]]) -> Callable[Con[View, P], Optional[T]]
    @wraps(fn)  # type: ignore[arg-type]
    def decorated(view, *args, **kwargs):
        # type: (sublime.View, P.args, P.kwargs) -> Optional[T]
        return run_as_text_command(fn, view, *args, **kwargs)
    return decorated


@lru_cache()
def wants_edit_object(fn):
    # type: (Callable) -> bool
    sig = inspect.signature(fn)
    return 'edit' in sig.parameters


class gs_generic_text_cmd(sublime_plugin.TextCommand):
    def run_(self, edit_token, cmd_args):
        cmd_args = self.filter_args(cmd_args)
        token = cmd_args['token']
        with lock:
            # Any user can "redo" text commands, but we don't want that.
            try:
                fn, args, kwargs = COMMANDS.pop(token)
            except KeyError:
                return

        edit = self.view.begin_edit(edit_token, self.name(), cmd_args)
        try:
            if wants_edit_object(fn):
                return self.run(token, fn, args[0], edit, *args[1:], **kwargs)
            else:
                return self.run(token, fn, *args, **kwargs)
        finally:
            self.view.end_edit(edit)

    def run(self, token, fn, *args, **kwargs):
        rv = fn(*args, **kwargs)
        with lock:
            RESULTS[token] = rv


THROTTLED_CACHE = {}
THROTTLED_LOCK = threading.Lock()


def throttled(fn, *args, **kwargs):
    # type: (Callable[P, T], P.args, P.kwargs) -> Callable[[], None]
    token = (fn,)
    action = partial(fn, *args, **kwargs)
    with THROTTLED_LOCK:
        THROTTLED_CACHE[token] = action

    def task():
        with THROTTLED_LOCK:
            ok = THROTTLED_CACHE.get(token) == action
        if ok:
            action()

    return task


AWAIT_UI_THREAD:  Literal["AWAIT_UI_THREAD"]  = 'AWAIT_UI_THREAD'   # noqa: E221, E241
AWAIT_WORKER:     Literal["AWAIT_WORKER"]     = 'AWAIT_WORKER'      # noqa: E221, E241
ENSURE_UI_THREAD: Literal["ENSURE_UI_THREAD"] = 'ENSURE_UI_THREAD'
ENSURE_WORKER:    Literal["ENSURE_WORKER"]    = 'ENSURE_WORKER'     # noqa: E221, E241
HopperR: TypeAlias = Generator[
    Literal["AWAIT_UI_THREAD", "AWAIT_WORKER", "ENSURE_UI_THREAD", "ENSURE_WORKER"],
    "timer",
    None
]


def cooperative_thread_hopper(fn):
    # type: (Callable[P, HopperR]) -> Callable[P, None]
    """Mark given function as cooperative.

    `fn` must return `HopperR` t.i. it must yield AWAIT_UI_THREAD,
    AWAIT_WORKER, ENSURE_UI_THREAD, or ENSURE_WORKER at some point.

    Every yield answers with a timer object which can be used to
    measure the time since the continuation started.

    E.g. don't block the UI for too long:
        timer = yield AWAIT_UI_THREAD
        ... do something ...
        if timer.elapsed > 100:  # [milliseconds]
            yield AWAIT_UI_THREAD

    When calling `fn` it will run on the same thread as the caller
    until the function yields.  It then schedules a task on the
    desired thread which will continue execution the function.

    It is thus cooperative in the sense that all other tasks
    already queued will get a chance to run before we continue.
    It is "async" in the sense that the function does not run
    from start to end in a blocking manner but can be suspended.

    However, it is sync till the first yield (but you could of
    course yield on the first line!), only then execution returns
    to the call site.

    Be aware that, if the call site and the thread you request are
    _not_ the same, you can get concurrent execution afterwards!
    This is a side-effect of running two threads.
    """
    def tick(gen: HopperR, initial_call=False) -> None:
        try:
            # workaround mypy marking `send(None)` as error
            # https://github.com/python/mypy/issues/11023#issuecomment-1255901328
            if initial_call:
                rv = next(gen)
            else:
                rv = gen.send(timer())
        except StopIteration:
            return
        except Exception as ex:
            raise ex from None

        if rv == ENSURE_UI_THREAD:
            ensure_on_ui(tick, gen)
        elif rv == ENSURE_WORKER:
            ensure_on_worker(tick, gen)
        elif rv == AWAIT_UI_THREAD:
            enqueue_on_ui(tick, gen)
        elif rv == AWAIT_WORKER:
            enqueue_on_worker(tick, gen)

    @wraps(fn)
    def decorated(*args: P.args, **kwargs: P.kwargs) -> None:
        gen = fn(*args, **kwargs)
        if inspect.isgenerator(gen):
            tick(gen, initial_call=True)

    return decorated


class timer:
    UI_BLOCK_TIME = 17

    def __init__(self) -> None:
        """Create a new timer and start it."""
        self.start = time.perf_counter()

    @property
    def elapsed(self) -> float:
        """Get the elapsed time in milliseconds."""
        return (time.perf_counter() - self.start) * 1000

    def exceeded(self, ms: float) -> bool:
        """Check if the elapsed time has exceeded the given milliseconds."""
        return self.elapsed > ms

    def exhausted_ui_budget(self) -> bool:
        """Check if the elapsed time has exceeded the UI_BLOCK_TIME of 17ms."""
        return self.exceeded(self.UI_BLOCK_TIME)

    def reset(self) -> None:
        """Reset the timer to the current time."""
        self.start = time.perf_counter()


def time_budget(budget: float = timer.UI_BLOCK_TIME) -> Callable[[], bool]:
    """
    Create a function to check if a time budget has been exhausted.
    Anytime it has been exceeded, the timer is reset, so you don't have to.

    Args:
        budget (float): The time budget in seconds. Defaults to 17ms.
    Returns:
        Callable[[], bool]: The predicate function to check if the budget has been exceeded.
    """
    t = timer()

    def budget_exhausted() -> bool:
        if t.exceeded(budget):
            t.reset()
            return True
        return False

    return budget_exhausted
