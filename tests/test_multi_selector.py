from unittesting import DeferrableTestCase

import sublime

from GitSavvy.core.interfaces import multi_selector


class TestMultiselectMarkers(DeferrableTestCase):
    def test_reload_reset_clears_saved_and_orphaned_markers(self) -> None:
        window = sublime.active_window()
        views = [window.new_file(), window.new_file()]
        try:
            for view in views:
                view.run_command("append", {"characters": "abc def"})
                multi_selector.set_multiselect_markers(view, [sublime.Region(0, 3)])
            views[0].settings().set("git_savvy.multi_selection", [[0, 3]])

            multi_selector.reset_multiselections()

            for view in views:
                self.assertEqual(view.settings().get("git_savvy.multi_selection"), [])
                self.assertEqual(view.get_regions(multi_selector.REGION_KEY), [])
                self.assertFalse(view.get_status("gs_multiselect_info"))
        finally:
            for view in views:
                view.set_scratch(True)
                view.close()

    def test_uses_the_explicit_scope_for_each_view_type(self) -> None:
        cases = (
            ({"git_savvy.log_graph_view": True}, "graph"),
            ({"git_savvy.commit_view": True}, "commit"),
            ({"git_savvy.diff_view": True}, "diff"),
            ({"git_savvy.interface": "branch"}, "dashboard"),
            ({"git_savvy.interface": "rebase"}, "dashboard"),
            ({"git_savvy.interface": "remotes"}, "dashboard"),
            ({"git_savvy.interface": "status"}, "dashboard"),
            ({"git_savvy.interface": "tags"}, "dashboard"),
        )
        for settings, color_role in cases:
            with self.subTest(settings=settings):
                view = FakeView(settings)

                multi_selector.set_multiselect_markers(
                    view,
                    [sublime.Region(0, 1)]
                )

                self.assertEqual(
                    view.added_styles["scope"],
                    multi_selector.MULTISELECT_SCOPES[color_role]
                )

    def test_falls_back_to_the_leaf_scope_for_unknown_views(self) -> None:
        view = FakeView({})

        multi_selector.set_multiselect_markers(view, [sublime.Region(0, 1)])

        self.assertEqual(
            view.added_styles["scope"],
            multi_selector.MULTISELECT_SCOPE
        )


class FakeView:
    def __init__(self, settings: dict) -> None:
        self._settings = settings
        self.added_styles = {}

    def settings(self) -> dict:
        return self._settings

    def add_regions(self, key, regions, **styles) -> None:
        self.added_styles = styles

    def set_status(self, key, value) -> None:
        pass
