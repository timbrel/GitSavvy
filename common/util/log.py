import sublime


def panel(*msgs):
    msg = "\n".join(str(msg) for msg in msgs)
    sublime.active_window().active_view().run_command("gs_display_panel", {"msg": msg})
