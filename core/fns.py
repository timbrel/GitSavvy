from functools import partial
from itertools import accumulate as accumulate_, chain, islice, tee

MYPY = False
if MYPY:
    from typing import Callable, Iterable, Iterator, Optional, Set, Tuple, TypeVar
    T = TypeVar('T')


filter_ = partial(filter, None)  # type: Callable[[Iterable[Optional[T]]], Iterator[T]]
flatten = chain.from_iterable


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


# Below functions taken from https://github.com/erikrose/more-itertools
# Copyright (c) 2012 Erik Rose


def take(n, iterable):
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
