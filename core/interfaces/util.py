import sublime


from typing import Optional


##########
# GLOBAL #
##########

def refresh_gitsavvy_interfaces(
    window: Optional[sublime.Window],
    refresh_sidebar: bool = False,
    refresh_status_bar: bool = True
) -> None:
    """
    Looks for GitSavvy interface views in the current window and refresh them.

    Note that it only refresh visible views.
    Other views will be refreshed when activated.
    """
    if window is None:
        return

    if refresh_sidebar:
        window.run_command("refresh_folder_list")
    if refresh_status_bar:
        av = window.active_view()
        if av:
            av.run_command("gs_update_status")

    for group in range(window.num_groups()):
        view = window.active_view_in_group(group)
        if view is None:
            continue

        if view.settings().get("git_savvy.interface") is not None:
            view.run_command("gs_interface_refresh")

        if view.settings().get("git_savvy.log_graph_view", False):
            view.run_command("gs_log_graph_refresh")


def refresh_gitsavvy(
    view: Optional[sublime.View],
    refresh_sidebar: bool = False,
    refresh_status_bar: bool = True
) -> None:
    """
    Called after GitSavvy action was taken that may have effected the
    state of the Git repo.
    """
    if view is None:
        return

    if view.settings().get("git_savvy.interface") is not None:
        view.run_command("gs_interface_refresh")

    if view.settings().get("git_savvy.log_graph_view", False):
        view.run_command("gs_log_graph_refresh")

    if view.window() and refresh_status_bar:
        view.run_command("gs_update_status")

    window = view.window()
    if window and refresh_sidebar:
        window.run_command("refresh_folder_list")


#################
# MISCELLANEOUS #
#################


def mark_as_lintable(view: sublime.View) -> None:
    view.settings().set("SublimeLinter.enabled?", True)
