from __future__ import annotations

import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import spy2, unstub, verify
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.core.interfaces.log_graph import SELECTED_ITEM_SCOPE
from GitSavvy.core.interfaces.log_graph_renderer import draw_prelude, prelude


class TestGraphPrelude(DeferrableTestCase):
    def setUp(self) -> None:
        self.view = sublime.active_window().new_file()
        self.view.set_scratch(True)
        self.view.assign_syntax("Packages/GitSavvy/syntax/graph.sublime-syntax")
        self.addCleanup(self.view.close)
        self.addCleanup(unstub)
        self.settings = self.view.settings()
        self.settings.set("git_savvy.repo_path", "/repo")

    @p.expand([
        (True, [], "all"),
        (True, ["refs/heads/topic"], "all  topic"),
        (False, [], "all"),
        (False, ["HEAD", "refs/heads/topic", "refs/remotes/origin/main"], "all  HEAD topic origin/main")
    ])
    def test_all_label_and_branches(self, enabled: bool, branches: list[str], expected: str) -> None:
        self.settings.set("git_savvy.log_graph_view.all_branches", enabled)
        self.settings.set("git_savvy.log_graph_view.branches", branches)
        self.assertEqual(prelude(self.view), f"\n  REPO: /repo\n  {expected}\n\n")

    def test_active_filters_are_on_their_own_line(self) -> None:
        self.settings.set("git_savvy.log_graph_view.all_branches", True)
        self.settings.set("git_savvy.log_graph_view.apply_filters", True)
        self.settings.set("git_savvy.log_graph_view.filters", "--author=someone")
        self.assertEqual(prelude(self.view), "\n  REPO: /repo\n  all\n  --author=someone\n\n")

    def test_multiline_pickaxe_stays_in_the_prelude(self) -> None:
        self.settings.set("git_savvy.log_graph_view.apply_filters", True)
        self.settings.set("git_savvy.log_graph_view.filters", "-S'first\nsecond'")
        draw_prelude(self.view, prelude(self.view))

        self.assertEqual(
            self.view.substr(sublime.Region(0, self.view.size())),
            "\n  REPO: /repo\n  all\n  -S'''\n    first\n    second\n  '''\n\n"
        )
        point = self.view.find("second", 0).begin()
        self.assertTrue(self.view.match_selector(point, "meta.prelude.git_savvy.graph"))

    def test_all_uses_selected_item_scope_only_when_enabled(self) -> None:
        spy2(self.view.add_regions)
        self.settings.set("git_savvy.log_graph_view.all_branches", True)
        draw_prelude(self.view, prelude(self.view))

        regions = self.view.get_regions("gs_log_graph.all")
        self.assertEqual([self.view.substr(region) for region in regions], ["all"])
        verify(self.view).add_regions(
            "gs_log_graph.all", regions, scope=SELECTED_ITEM_SCOPE, flags=sublime.RegionFlags.NO_UNDO
        )

        self.settings.set("git_savvy.log_graph_view.all_branches", False)
        draw_prelude(self.view, prelude(self.view))
        self.assertEqual(self.view.get_regions("gs_log_graph.all"), [])

        self.settings.set("git_savvy.log_graph_view.all_branches", True)
        draw_prelude(self.view, prelude(self.view))
        self.assertEqual(
            [self.view.substr(region) for region in self.view.get_regions("gs_log_graph.all")], ["all"]
        )

    def test_only_the_header_label_is_highlighted(self) -> None:
        self.settings.set("git_savvy.log_graph_view.all_branches", True)
        self.settings.set("git_savvy.log_graph_view.apply_filters", True)
        self.settings.set("git_savvy.log_graph_view.filters", "all")
        draw_prelude(self.view, prelude(self.view))

        self.assertEqual(self.view.get_regions("gs_log_graph.all"), [self.view.find("all", 0)])

    def test_highlight_tracks_a_changed_first_line(self) -> None:
        self.settings.set("git_savvy.log_graph_view.all_branches", True)
        draw_prelude(self.view, prelude(self.view))
        self.settings.set("git_savvy.repo_path", "/a/much/longer/repository")
        region = self.view.find_by_selector("meta.prelude.git_savvy.graph")[0]
        draw_prelude(self.view, prelude(self.view), region)

        self.assertEqual(
            [self.view.substr(region) for region in self.view.get_regions("gs_log_graph.all")], ["all"]
        )

    def test_selected_refs_are_individually_highlighted(self) -> None:
        refs = ["HEAD", "refs/heads/topic", "refs/remotes/origin/main", "refs/tags/2.62.2"]
        self.settings.set("git_savvy.log_graph_view.branches", refs)
        spy2(self.view.add_regions)
        draw_prelude(self.view, prelude(self.view))

        regions = self.view.get_regions("gs_log_graph.branches")
        self.assertEqual([self.view.substr(r) for r in regions], ["HEAD", "topic", "origin/main", "2.62.2"])
        self.assertEqual(self.view.get_regions("gs_log_graph.prelude_refs"), regions)
        self.assertEqual(self.settings.get("git_savvy.log_graph_view.prelude_refs"), refs)
        verify(self.view).add_regions(
            "gs_log_graph.branches", regions, scope=SELECTED_ITEM_SCOPE, flags=sublime.RegionFlags.NO_UNDO
        )
        self.assertEqual(self.view.get_regions("gs_log_graph.all"), [])

    def test_all_keeps_choices_commented_and_not_highlighted(self) -> None:
        self.settings.set("git_savvy.log_graph_view.all_branches", True)
        refs = ["refs/heads/topic", "refs/remotes/origin/master", "refs/tags/2.62.2"]
        self.settings.set("git_savvy.log_graph_view.branches", refs)
        draw_prelude(self.view, prelude(self.view))

        self.assertEqual(prelude(self.view), "\n  REPO: /repo\n  all  topic origin/master 2.62.2\n\n")
        self.assertEqual(self.view.get_regions("gs_log_graph.branches"), [])
        regions = self.view.get_regions("gs_log_graph.prelude_refs")
        self.assertEqual([self.view.substr(r) for r in regions], ["topic", "origin/master", "2.62.2"])
        for region in regions:
            self.assertTrue(self.view.match_selector(region.begin(), "comment.prelude.git_savvy.graph"))

    def test_switching_modes_keeps_choices_but_changes_highlights(self) -> None:
        self.settings.set("git_savvy.log_graph_view.all_branches", True)
        refs = ["refs/heads/topic", "refs/tags/v1"]
        self.settings.set("git_savvy.log_graph_view.branches", refs)
        draw_prelude(self.view, prelude(self.view))
        self.settings.set("git_savvy.log_graph_view.all_branches", False)
        self.settings.set("git_savvy.log_graph_view.branches", ["refs/tags/v1"])
        draw_prelude(self.view, prelude(self.view))
        self.assertEqual([self.view.substr(r) for r in self.view.get_regions("gs_log_graph.branches")], ["v1"])
        self.assertEqual(self.settings.get("git_savvy.log_graph_view.prelude_refs"), refs)
        self.assertEqual(self.view.get_regions("gs_log_graph.all"), [])

        self.settings.set("git_savvy.log_graph_view.overview", True)
        draw_prelude(self.view, prelude(self.view))
        self.assertEqual(self.view.get_regions("gs_log_graph.branches"), [])
        self.assertEqual(self.view.get_regions("gs_log_graph.prelude_refs"), [])
        self.assertEqual(self.settings.get("git_savvy.log_graph_view.prelude_refs"), [])

    def test_overview_retains_its_label_without_all_highlight(self) -> None:
        self.settings.set("git_savvy.log_graph_view.all_branches", True)
        draw_prelude(self.view, prelude(self.view))
        self.settings.set("git_savvy.log_graph_view.overview", True)
        draw_prelude(self.view, prelude(self.view))

        self.assertEqual(prelude(self.view), "\n  REPO: /repo\n  OVERVIEW\n\n")
        self.assertEqual(self.view.get_regions("gs_log_graph.all"), [])
