import sublime


from typing import Callable, Optional
ValueCallback = Callable[[str], None]
CancelCallback = Callable[[], None]


def show_single_line_input_panel(
    caption,  # type: str
    initial_text,  # type: str
    on_done,  # type: ValueCallback
    on_change=None,  # type: Optional[ValueCallback]
    on_cancel=None,  # type: Optional[CancelCallback]
    select_text=True  # type: bool
):  # type: (...) -> sublime.View
    window = sublime.active_window()
    v = window.show_input_panel(caption, initial_text, on_done, on_change, on_cancel)
    if select_text:
        v.run_command("select_all")
    v.settings().set("git_savvy.single_line_input_panel", True)
    return v
