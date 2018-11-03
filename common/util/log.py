import sublime


def panel(*msgs, run_async=True):
    msg = "\n".join(str(msg) for msg in msgs)
    view = sublime.active_window().active_view()
    if run_async:
        sublime.set_timeout_async(
            lambda: view.run_command("gs_display_panel", {"msg": msg})
        )
    else:
        view.run_command("gs_display_panel", {"msg": msg})


def panel_append(*msgs, run_async=True):
    msg = "\n".join(str(msg) for msg in msgs)
    view = sublime.active_window().active_view()
    if run_async:
        sublime.set_timeout_async(
            lambda: view.run_command("gs_append_panel", {"msg": msg})
        )
    else:
        view.run_command("gs_append_panel", {"msg": msg})
