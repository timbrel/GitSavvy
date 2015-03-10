import sublime


def destructive(description):
    def decorator(fn):

        def wrapped_fn(*args, **kwargs):
            settings = sublime.load_settings("GitSavvy.sublime-settings")
            if settings.get("prompt_before_destructive_action"):
                message = (
                    "You are about to {desc}.  "
                    "This is a destructive action.  \n\n"
                    "Would you like to proceed?  \n\n"
                    "(you can disable this prompt in "
                    "GitSavvy.sublime-settings)").format(desc=description)
                if not sublime.ok_cancel_dialog(message):
                    return

            fn(*args, **kwargs)

        return wrapped_fn

    return decorator
