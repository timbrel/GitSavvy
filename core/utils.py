from contextlib import contextmanager
import time
import threading
import traceback


MYPY = False
if MYPY:
    ...


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


def line_indentation(line):
    # type: (str) -> int
    return len(line) - len(line.lstrip())
