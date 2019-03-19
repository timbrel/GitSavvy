import sublime
from ...core.settings import GitSavvySettings


def destructive(description):
    def decorator(fn):

        def wrapped_fn(*args, **kwargs):
            if GitSavvySettings().get("prompt_before_destructive_action"):
                message = (
                    "You are about to {desc}.  "
                    "This is a destructive action.  \n\n"
                    "Are you SURE you want to do this?  \n\n"
                    "(you can disable this prompt in "
                    "GitSavvy settings)").format(desc=description)
                if not sublime.ok_cancel_dialog(message):
                    return

            return fn(*args, **kwargs)

        return wrapped_fn

    return decorator
