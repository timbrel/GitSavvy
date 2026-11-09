from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.interfaces import branch, remotes, status, tags, util
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


class TestStatusRefresh(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    @p.expand([
        (module, interface, whole_window)
        for module, interface in [
            (status, status.StatusInterface),
            (branch, branch.BranchInterface),
            (tags, tags.TagsInterface),
            (remotes, remotes.RemotesInterface),
        ]
        for whole_window in (False, True)
    ])
    def test_dashboard_refresh_requests_status_only_once(
        self, module, interface_class, whole_window
    ) -> None:
        view, window = self.make_view(interface_class.interface_type)
        interface = mock({
            'view': view,
            'short_repo_path': '/repo',
            'state': {},
            'app_settings': {},
        })
        requests = []

        def run_command(command):
            if command == 'gs_interface_refresh':
                interface_class.refresh_view_state(interface)
            elif command == 'gs_update_status':
                requests.append(command)

        when(view).run_command(...).thenAnswer(run_command)
        when(module).enqueue_on_worker(...).thenReturn(None)

        if whole_window:
            util.refresh_gitsavvy_interfaces(window)
        else:
            util.refresh_gitsavvy(view)

        self.assertEqual(1, len(requests))

    @p.expand([
        (interface_type, whole_window)
        for interface_type in (None, 'rebase')
        for whole_window in (False, True)
    ])
    def test_other_views_still_request_status(self, interface_type, whole_window) -> None:
        view, window = self.make_view(interface_type)

        if whole_window:
            util.refresh_gitsavvy_interfaces(window)
        else:
            util.refresh_gitsavvy(view)

        verify(view).run_command('gs_update_status')

    def make_view(self, interface_type):
        view = mock()
        window = mock()
        when(view).settings().thenReturn({'git_savvy.interface': interface_type})
        when(view).window().thenReturn(window)
        when(window).active_view().thenReturn(view)
        when(util).visible_views(window).thenReturn([view])
        return view, window
