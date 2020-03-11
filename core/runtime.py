from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache, partial, wraps
import inspect
import threading
import uuid

import sublime
import sublime_plugin


MYPY = False
if MYPY:
    from typing import Any, Callable, Dict, Iterator, Literal, Optional, Tuple, TypeVar
    T = TypeVar('T')
    Callback = Tuple[Callable, Tuple[Any, ...], Dict[str, Any]]
    ReturnValue = Any


savvy_executor = ThreadPoolExecutor(max_workers=1)

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
    # type: (Callable, Any, Any) -> None
    sublime.set_timeout(partial(fn, *args, **kwargs))


def enqueue_on_worker(fn, *args, **kwargs):
    # type: (Callable, Any, Any) -> None
    sublime.set_timeout_async(partial(fn, *args, **kwargs))


def enqueue_on_savvy(fn, *args, **kwargs):
    # type: (Callable, Any, Any) -> None
    savvy_executor.submit(fn, *args, **kwargs)


def run_on_new_thread(fn, *args, **kwargs):
    # type: (Callable, Any, Any) -> None
    threading.Thread(target=fn, args=args, kwargs=kwargs).start()


def run_or_timeout(fn, timeout):
    cond = threading.Condition()
    result = None
    exc = None

    def program():
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

    if exc:
        raise exc
    else:
        return result


lock = threading.Lock()
COMMANDS = {}  # type: Dict[str, Callback]
RESULTS = {}  # type: Dict[str, ReturnValue]


def run_as_text_command(fn, view, *args, **kwargs):
    # type: (Callable[..., T], sublime.View, Any, Any) -> Optional[T]
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


def text_command(fn):
    # type: (Callable[..., T]) -> Callable[..., T]
    @wraps(fn)
    def decorated(view, *args, **kwargs):
        # type: (sublime.View, Any, Any) -> Optional[T]
        return run_as_text_command(fn, view, *args, **kwargs)
    return decorated


@lru_cache()
def wants_edit_object(fn):
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
    # type: (...) -> Callable[[], None]
    token = (fn,)
    action = partial(fn, *args, **kwargs)
    with THROTTLED_LOCK:
        THROTTLED_CACHE[token] = action

    def task():
        with THROTTLED_LOCK:
            ok = THROTTLED_CACHE[token] == action
        if ok:
            action()

    return task


AWAIT_UI_THREAD = 'AWAIT_UI_THREAD'  # type: Literal["AWAIT_UI_THREAD"]
AWAIT_WORKER = 'AWAIT_WORKER'  # type: Literal["AWAIT_WORKER"]
if MYPY:
    HopperR = Iterator[Literal["AWAIT_UI_THREAD", "AWAIT_WORKER"]]
    HopperFn = Callable[..., HopperR]


def cooperative_thread_hopper(fn):
    # type: (HopperFn) -> Callable[..., None]
    """Mark given function as cooperative.

    `fn` must return `HopperR` t.i. it must yield AWAIT_UI_THREAD
    or AWAIT_UI_THREAD at some point.

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
    """
    def tick(gen, send_value=None):
        try:
            rv = gen.send(send_value)
        except StopIteration:
            return
        except Exception as ex:
            raise ex from None

        if rv == AWAIT_UI_THREAD:
            enqueue_on_ui(tick, gen)
        elif rv == AWAIT_WORKER:
            enqueue_on_worker(tick, gen)

    def decorated(*args, **kwargs):
        gen = fn(*args, **kwargs)
        if inspect.isgenerator(gen):
            tick(gen)

    return decorated
