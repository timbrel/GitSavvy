from sublime_plugin import EventListener

from . import util


class GsInterfaceFocusEventListener(EventListener):

    """
    Trigger handlers for view life-cycle events.
    """

    def on_activated(self, view):
        util.view.refresh_gitsavvy(view)
        if view.settings().get('git_savvy.vintageous_friendly', False) is True:
            view.run_command('gs_vintageous_enter_normal_mode')

    def on_close(self, view):
        util.view.handle_closed_view(view)
