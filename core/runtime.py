from functools import partial
import sublime


MYPY = False
if MYPY:
    from typing import Any, Callable, Iterator, Literal


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


