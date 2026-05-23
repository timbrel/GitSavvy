from __future__ import annotations
from collections import OrderedDict
from functools import lru_cache, wraps
import inspect

import sublime_plugin

from typing import Any, Callable, Dict, Tuple, TypeVar, overload

from typing_extensions import Concatenate, ParamSpec
P = ParamSpec('P')
T = TypeVar('T')


__all__ = ("UntilFocusSwitchCacheController",)


class Cache(OrderedDict):
    def __init__(self, maxsize=128):
        assert maxsize > 0
        self.maxsize = maxsize
        super().__init__()

    def __getitem__(self, key):
        value = super().__getitem__(key)
        # py>3.8 is optimized such that `pop` and `popitem`
        # call `__getitem__` but `move_to_end` already throws.
        try:
            self.move_to_end(key)
        except KeyError:
            pass
        return value

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        super().__setitem__(key, value)
        if len(self) > self.maxsize:
            self.popitem(last=False)


general_purpose_cache = Cache(maxsize=512)  # type: Dict[Tuple, Any]


def cached(not_if, cache=general_purpose_cache):
    # type: (Dict[str, Callable], Dict[Tuple, Any]) -> Callable[[Callable[P, T]], Callable[P, T]]
    def decorator(fn):
        # type: (Callable[P, T]) -> Callable[P, T]
        fn_s = inspect.signature(fn)

        def should_skip(arguments):
            return any(
                fn(arguments[name])
                for name, fn in not_if.items()
            )

        @wraps(fn)
        def decorated(*args, **kwargs):
            # type: (P.args, P.kwargs) -> T
            arguments = _bind_arguments(fn_s, args, kwargs)
            if should_skip(arguments):
                return fn(*args, **kwargs)

            key = (fn.__name__,) + tuple(sorted(arguments.items()))
            try:
                return cache[key]
            except KeyError:
                rv = cache[key] = fn(*args, **kwargs)
                return rv

        return decorated
    return decorator


def _bind_arguments(sig: inspect.Signature, args: tuple, kwargs: dict) -> Dict[str, Any]:
    bound = sig.bind(*args, **kwargs)
    arguments = bound.arguments

    def default_value_of(parameter):
        if parameter.default is not parameter.empty:
            return parameter.default
        if parameter.kind is parameter.VAR_KEYWORD:
            return {}
        if parameter.kind is parameter.VAR_POSITIONAL:
            return tuple()

    return {
        name: (arguments[name] if name in arguments else default_value_of(p))
        for name, p in sig.parameters.items()
        if name != "self"
    }


def cache_in_store_as(key):
    # type: (str) -> Callable[[Callable[P, T]], Callable[P, T]]
    """Store the return value of the decorated function in the store."""
    def decorator(fn):
        # type: (Callable[P, T]) -> Callable[P, T]
        @wraps(fn)
        def decorated(*args, **kwargs):
            # type: (P.args, P.kwargs) -> T
            rv = fn(*args, **kwargs)
            self = args[0]
            self.update_store({key: rv})  # type: ignore[attr-defined]
            return rv

        return decorated
    return decorator


until_focus_switch_cache = Cache()


class UntilFocusSwitchCacheController(sublime_plugin.EventListener):
    def on_deactivated(self, view):
        until_focus_switch_cache.clear()


@overload
def cached_until_focus_switch(
    fn: Callable[Concatenate[Any, P], T]) -> Callable[Concatenate[Any, P], T]: ...
@overload                                                                     # noqa: E302
def cached_until_focus_switch(fn: Callable[P, T], *args: P.args, **kwargs: P.kwargs) -> T: ...
def cached_until_focus_switch(fn, *args, **kwargs):                           # noqa: E302
    def impl_(fn: Callable[..., T], args, kwargs) -> T:
        sig = get_signature(fn)
        arguments = _bind_arguments(sig, args, kwargs)
        self = args[0]
        key = (self.repo_path, fn.__name__, tuple(sorted(arguments.items())))
        try:
            return until_focus_switch_cache[key]
        except KeyError:
            val = until_focus_switch_cache[key] = fn(*args, **kwargs)
            return val

    if hasattr(fn, "__self__"):
        # Normalize to the decorator shape: unwrap the bound method and
        # prepend `self` to args, so `impl_` always sees the unbound
        # function plus self at args[0].
        return impl_(fn.__func__, (fn.__self__,) + args, kwargs)

    @wraps(fn)
    def wrapper(*args, **kwargs):
        return impl_(fn, args, kwargs)

    return wrapper


def get_signature(fn: Callable) -> inspect.Signature:
    return _get_signature(getattr(fn, "__func__", fn))


@lru_cache(maxsize=None)
def _get_signature(fn: Callable) -> inspect.Signature:
    return inspect.signature(fn)
