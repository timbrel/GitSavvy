import sublime


def universal_newlines(string):
    return string.replace('\r\n', '\n').replace('\r', '\n')


def panel(message, run_async=True):
    message = universal_newlines(str(message))
    view = sublime.active_window().active_view()
    if run_async:
        sublime.set_timeout_async(
            lambda: view.run_command("gs_display_panel", {"msg": message})
        )
    else:
        view.run_command("gs_display_panel", {"msg": message})


def panel_append(message, run_async=True):
    message = universal_newlines(str(message))
    view = sublime.active_window().active_view()
    if run_async:
        sublime.set_timeout_async(
            lambda: view.run_command("gs_append_panel", {"msg": message})
        )
    else:
        view.run_command("gs_append_panel", {"msg": message})
