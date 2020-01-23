from functools import partial
from itertools import accumulate as accumulate_, chain, tee

MYPY = False
if MYPY:
    from typing import Callable, Iterable, Iterator, Optional, Tuple, TypeVar
    T = TypeVar('T')


filter_ = partial(filter, None)  # type: Callable[[Iterator[Optional[T]]], Iterator[T]]
flatten = chain.from_iterable


def accumulate(iterable, initial):
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
