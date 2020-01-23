from concurrent.futures import ThreadPoolExecutor
from functools import partial
import inspect
import threading

import sublime


MYPY = False
if MYPY:
    from typing import Any, Callable, Iterator, Literal


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
