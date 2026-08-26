from unittesting import DeferrableTestCase

import sublime

from GitSavvy.core.commands import multi_selector


class TestMultiselectMarkers(DeferrableTestCase):
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
