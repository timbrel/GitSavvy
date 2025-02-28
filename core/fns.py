from __future__ import annotations
from collections import deque
from functools import partial
import inspect
from itertools import accumulate as accumulate_, chain, islice, tee

from typing import Any, Callable, Iterable, Iterator, List, Optional, Set, Tuple, TypeVar
T = TypeVar('T')
U = TypeVar('U')

NOT_SET: Any = object()
filter_ = partial(filter, None)  # type: Callable[[Iterable[Optional[T]]], Iterator[T]]
flatten = chain.from_iterable


def consume(it: Iterable) -> None:
    deque(it, 0)


def maybe(fn):
    # type: (Callable[[], T]) -> Optional[T]
    try:
        return fn()
    except Exception:
        return None


def accumulate(iterable, initial=None):
    # type: (Iterable[int], int) -> Iterable[int]
    if initial is None:
        return accumulate_(iterable)
    else:
        return accumulate_(chain([initial], iterable))


def pairwise(iterable):
    # type: (Iterable[T]) -> Iterable[Tuple[T, T]]
    "s -> (s0,s1), (s1,s2), (s2, s3), ..."
    a, b = tee(iterable)
    next(b, None)
    return zip(a, b)


def unique(iterable):
    # type: (Iterable[T]) -> Iterator[T]
    seen = set()  # type: Set[T]
    for item in iterable:
        if item in seen:
            continue
        seen.add(item)
        yield item


def peek(iterable):
    # type: (Iterable[T]) -> Tuple[T, Iterable[T]]
    it = iter(iterable)
    head = next(it)
    return head, chain([head], it)


def drop(n, iterable):
    # type: (int, Iterable[T]) -> Iterator[T]
    return islice(iterable, n, None)


def head(iterable):
    # type: (Iterable[T]) -> List[T]
    return take(1, iterable)


def tail(iterable):
    # type: (Iterable[T]) -> Iterator[T]
    return drop(1, iterable)


def last(iterable, default=NOT_SET):
    # type: (Iterable[T], T | None) -> Optional[T]
    try:
        return deque(iterable, 1)[0]
    except IndexError:
        if default is NOT_SET:
            raise
        return default


def unzip(zipped):
    # type: (Iterable[Tuple[T, U]]) -> Tuple[Tuple[T, ...], Tuple[U, ...]]
    return tuple(zip(*zipped))  # type: ignore


# Below functions taken from https://github.com/erikrose/more-itertools
# Copyright (c) 2012 Erik Rose


def take(n, iterable):
    # type: (int, Iterable[T]) -> List[T]
    """Return first *n* items of the iterable as a list.
        >>> take(3, range(10))
        [0, 1, 2]
    If there are fewer than *n* items in the iterable, all of them are
    returned.
        >>> take(10, range(3))
        [0, 1, 2]
    """
    return list(islice(iterable, n))


def chunked(iterable, n):
    """Break *iterable* into lists of length *n*:

        >>> list(chunked([1, 2, 3, 4, 5, 6], 3))
        [[1, 2, 3], [4, 5, 6]]

    If the length of *iterable* is not evenly divisible by *n*, the last
    returned list will be shorter:

        >>> list(chunked([1, 2, 3, 4, 5, 6, 7, 8], 3))
        [[1, 2, 3], [4, 5, 6], [7, 8]]

    To use a fill-in value instead, see the :func:`grouper` recipe.

    :func:`chunked` is useful for splitting up a computation on a large number
    of keys into batches, to be pickled and sent off to worker processes. One
    example is operations on rows in MySQL, which does not implement
    server-side cursors properly and would otherwise load the entire dataset
    into RAM on the client.

    """
    return iter(partial(take, n, iter(iterable)), [])


def partition(pred, iterable):
    # type: (Optional[Callable[[T], bool]], Iterable[T]) -> Tuple[Iterator[T], Iterator[T]]
    """
    Returns a 2-tuple of iterables derived from the input iterable.
    The first yields the items that have ``pred(item) == False``.
    The second yields the items that have ``pred(item) == True``.

        >>> is_odd = lambda x: x % 2 != 0
        >>> iterable = range(10)
        >>> even_items, odd_items = partition(is_odd, iterable)
        >>> list(even_items), list(odd_items)
        ([0, 2, 4, 6, 8], [1, 3, 5, 7, 9])

    If *pred* is None, :func:`bool` is used.

        >>> iterable = [0, 1, False, True, '', ' ']
        >>> false_items, true_items = partition(None, iterable)
        >>> list(false_items), list(true_items)
        ([0, False, ''], [1, True, ' '])

    """
    if pred is None:
        pred = bool

    evaluations = ((pred(x), x) for x in iterable)
    t1, t2 = tee(evaluations)
    return (
        (x for (cond, x) in t1 if not cond),
        (x for (cond, x) in t2 if cond),
    )


def arity(fn: Callable) -> int:
    """
    Returns the arity (number of required parameters) of a given function.
    Handles regular functions and functools.partial objects.

    Args:
        fn: The function to analyze (can be a regular function or partial)

    Returns:
        int: The number of required parameters (arity)

    Examples:
        >>> def add(a, b, c):
        ...     return a + b + c
        >>> arity(add)
        3

        >>> add_partial = partial(add, 1)
        >>> arity(add_partial)
        2

        >>> def greet(name, greeting="Hello"):
        ...     return f"{greeting}, {name}!"
        >>> arity(greet)
        1

        >>> greet_partial = partial(greet, greeting="Hi")
        >>> arity(greet_partial)
        1
    """
    # Handle partial functions
    if isinstance(fn, partial):
        # Get the original function's signature
        signature = inspect.signature(fn.func)
        parameters = list(signature.parameters.values())

        # Count how many positional arguments are provided in the partial
        positional_args_filled = len(fn.args)

        # Get the keyword arguments provided in the partial
        keyword_args = fn.keywords or {}

        # Count required parameters, accounting for those already filled by partial
        required_params = 0
        for i, param in enumerate(parameters):
            # Skip parameters already filled by positional args in partial
            if i < positional_args_filled:
                continue

            # Skip parameters already filled by keyword args in partial
            if param.name in keyword_args:
                continue

            # Count the parameter as required if it doesn't have a default and isn't var args
            if param.default is param.empty and param.kind not in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                required_params += 1

        return required_params

    # Handle regular functions
    signature = inspect.signature(fn)

    # Count parameters that don't have a default value
    required_params = 0
    for param in signature.parameters.values():
        # Parameter is required if it doesn't have a default value and is not a *args or **kwargs
        if param.default is param.empty and param.kind not in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            required_params += 1

    return required_params
