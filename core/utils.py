from contextlib import contextmanager
import inspect
import time
import threading
import traceback

import sublime


MYPY = False
if MYPY:
    from typing import Callable, Iterator, Literal


@contextmanager
def print_runtime(message):
    start_time = time.perf_counter()
    yield
    end_time = time.perf_counter()
    duration = round((end_time - start_time) * 1000)
    thread_name = threading.current_thread().name[0]
    print('{} took {}ms [{}]'.format(message, duration, thread_name))


@contextmanager
def eat_but_log_errors(exception=Exception):
    try:
        yield
    except exception:
        traceback.print_exc()


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
            sublime.set_timeout(lambda: tick(gen))
        elif rv == AWAIT_WORKER:
            sublime.set_timeout_async(lambda: tick(gen))

    def decorated(*args, **kwargs):
        gen = fn(*args, **kwargs)
        if inspect.isgenerator(gen):
            tick(gen)

    return decorated


def line_indentation(line):
    # type: (str) -> int
    return len(line) - len(line.lstrip())
