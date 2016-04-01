from sublime_plugin import EventListener

from . import util


class GsInterfaceFocusEventListener(EventListener):

    """
    Trigger handlers for view life-cycle events.
    """

    def on_activated(self, view):
        util.view.refresh_gitsavvy(view)

    def on_close(self, view):
        util.view.handle_closed_view(view)
