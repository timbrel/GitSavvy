from contextlib import contextmanager
import inspect
import time
import threading
import traceback

import sublime


MYPY = False
if MYPY:
    from typing import Callable, Iterator, Literal, Union


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
    HopperR = Iterator[Union[Literal["AWAIT_UI_THREAD", "AWAIT_WORKER"]]]
    HoperFn = Callable[..., HopperR]


def cooperative_thread_hopper(fn):
    # type: (HoperFn) -> Callable[..., None]
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
