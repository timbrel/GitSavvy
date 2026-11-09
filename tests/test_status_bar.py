from unittesting import DeferrableTestCase

from GitSavvy.core.commands import status_bar
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


class TestOnStatusUpdate(DeferrableTestCase):
    def setUp(self):
        self.view = mock()
        window = mock()
        when(window).active_view().thenReturn(self.view)
        when(status_bar.sublime).active_window().thenReturn(window)

    def tearDown(self):
        unstub()

    def test_redraws_the_active_view_of_the_updated_repo(self):
        when(status_bar).find_repo_path_for_view(self.view).thenReturn("/repo")

        status_bar.on_status_update("/repo", {})

        verify(self.view).run_command("gs_draw_status_bar")

    @p.expand([("other repo", "/other"), ("no repo", None)])
    def test_skips_the_active_view_of_another_repo(self, _, view_repo_path):
        when(status_bar).find_repo_path_for_view(self.view).thenReturn(view_repo_path)

        status_bar.on_status_update("/repo", {})

        verify(self.view, times=0).run_command(...)
