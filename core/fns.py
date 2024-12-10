from __future__ import annotations
from collections import deque
from functools import partial
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
