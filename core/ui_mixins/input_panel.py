import sublime


def show_single_line_input_panel(
        caption, initial_text, on_done, on_change=None, on_cancel=None, select_text=True):
    window = sublime.active_window()
    v = window.show_input_panel(caption, initial_text, on_done, on_change, on_cancel)
    if select_text:
        v.run_command("select_all")
    v.settings().set("git_savvy.single_line_input_panel", True)
    return v
