from __future__ import annotations

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.common import global_events
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


class TestInterfaceFocusEvents(DeferrableTestCase):
    def setUp(self) -> None:
        self.previous_seen = global_events.SEEN.copy()
        self.previous_ignore = global_events.IGNORE_NEXT_ACTIVATE
        global_events.SEEN.clear()
        global_events.IGNORE_NEXT_ACTIVATE = False
        self.listener = global_events.GsInterfaceFocusEventListener()
        self.dashboard = self.make_view(1, {"git_savvy.interface": "status"})
        when(self.dashboard).run_command(...).thenReturn(None)
        when(global_events).migrate_color_scheme(...).thenReturn(None)

    def tearDown(self) -> None:
        global_events.SEEN.clear()
        global_events.SEEN.update(self.previous_seen)
        global_events.IGNORE_NEXT_ACTIVATE = self.previous_ignore
        unstub()

    @p.expand([
        ("dired", {"is_widget": True, "dired_path": "/repo/"}),
        ("dired_with_history", {"dired_path": "/repo/"}),
        ("terminus", {"is_widget": True, "terminus_view": True}),
        ("file", {})
    ])
    def test_returning_to_dashboard_refreshes(self, label: str, settings: dict) -> None:
        self.listener.on_activated(self.dashboard)
        self.listener.on_deactivated(self.dashboard)
        other_view = self.make_view(2, settings)
        self.listener.on_activated(other_view)

        # File operations in dired do not emit GitSavvy refresh events.
        # Returning to the dashboard must catch up with those changes.
        self.listener.on_deactivated(other_view)
        self.listener.on_activated(self.dashboard)

        verify(self.dashboard, times=2).run_command("gs_interface_refresh")

    def test_closing_palette_does_not_refresh_seen_dashboard(self) -> None:
        self.listener.on_activated(self.dashboard)
        self.listener.on_deactivated(self.dashboard)
        palette = self.make_view(2, {"is_widget": True})
        self.listener.on_activated(palette)
        self.listener.on_deactivated(palette)
        self.listener.on_activated(self.dashboard)

        verify(self.dashboard, times=1).run_command("gs_interface_refresh")

    def make_view(self, vid: int, settings: dict) -> sublime.View:
        view = mock()
        when(view).id().thenReturn(vid)
        when(view).settings().thenReturn(settings)
        when(view).window().thenReturn(None)
        return view
