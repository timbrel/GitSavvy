from functools import wraps

import sublime

from ...core.settings import GitSavvySettings

from typing import Callable, Optional, TypeVar, TYPE_CHECKING
T = TypeVar('T')

if TYPE_CHECKING:
    from typing_extensions import ParamSpec
    P = ParamSpec('P')


def destructive(description: str) -> "Callable[[Callable[P, T]], Callable[P, Optional[T]]]":
    def decorator(fn: "Callable[P, T]") -> "Callable[P, Optional[T]]":
        @wraps(fn)
        def wrapped_fn(*args: "P.args", **kwargs: "P.kwargs") -> Optional[T]:
            if GitSavvySettings().get("prompt_before_destructive_action"):
                message = (
                    "You are about to {desc}.  "
                    "This is a destructive action.  \n\n"
                    "Are you SURE you want to do this?  \n\n"
                    "(you can disable this prompt in "
                    "GitSavvy settings)").format(desc=description)
                if not sublime.ok_cancel_dialog(message):
                    return None

            return fn(*args, **kwargs)
        return wrapped_fn
    return decorator
