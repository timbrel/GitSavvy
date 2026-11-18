from __future__ import annotations

import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.tests.mockito import mock, unstub, verify, when

from GitSavvy.core.interfaces import log_graph_panels as panels
from GitSavvy.core.interfaces.log_graph_panels import gs_log_graph_edit_files
from GitSavvy.core.interfaces.log_graph_renderer import gs_log_graph_refresh
from GitSavvy.core.git_mixins.branches import Branch, Upstream
from GitSavvy.core.types import FullHash


class TestGraphBranchInput(DeferrableTestCase):
    def setUp(self) -> None:
        settings_view = sublime.active_window().new_file()
        settings_view.set_scratch(True)
        self.addCleanup(settings_view.close)
        self.addCleanup(unstub)
        self.settings = settings_view.settings()
        self.window = mock()
        self.view = mock()
        when(self.view).settings().thenReturn(self.settings)
        when(self.view).window().thenReturn(self.window)
        when(self.view).run_command(...).thenReturn(None)
        when(self.window).run_command(...).thenReturn(None)
        when(panels).enqueue_on_ui(...).thenAnswer(lambda fn, *args: fn(*args))
        self.command = panels.gs_log_graph_edit_branches(self.view)
        upstream = Upstream("origin", "main", "origin/main", "")
        self.branches = [
            branch("main", 10, upstream=upstream, active=True),
            branch("topic", 30, upstream=upstream),
            branch("main", 20, remote="origin")
        ]
        when(self.command).get_branches().thenReturn(self.branches)

    def test_lists_selected_branches_first_then_unselected_by_recency(self) -> None:
        self.set_selected(["main"])
        handler = self.command.input({"selected_index": 1})
        items, index = handler.list_items()

        self.assertEqual([item.value for item in items], [
            "refs/heads/main", "HEAD", "refs/heads/topic", "refs/remotes/origin/main"
        ])
        self.assertEqual([item.text for item in items], ["✓ main", "  HEAD", "  topic", "  origin/main"])
        self.assertIn("upstream", items[3].annotation)
        self.assertIn("remote", items[3].annotation)
        self.assertEqual(index, 1)
        verify(self.command, times=1).get_branches()

    def test_selected_branches_are_also_sorted_by_recency(self) -> None:
        self.set_selected(["main", "topic"])
        handler = self.handler()
        self.assertEqual(handler.refs, [
            "refs/heads/topic", "refs/heads/main", "HEAD", "refs/remotes/origin/main"
        ])

    def test_equal_timestamps_have_stable_name_order(self) -> None:
        when(self.command).get_branches().thenReturn([
            branch("z", 10), branch("a", 10), branch("main", 10, remote="origin")
        ])
        handler = self.command.input({})
        items, _ = handler.list_items()
        self.assertEqual([item.text for item in items], ["a", "origin/main", "z"])

    def test_local_and_remote_names_are_unambiguous(self) -> None:
        when(self.command).get_branches().thenReturn([
            branch("origin/main", 10), branch("main", 10, remote="origin")
        ])
        self.set_selected(["origin/main"])
        handler = self.command.input({})
        items, _ = handler.list_items()
        self.assertEqual([item.value for item in items], [
            "refs/heads/origin/main", "HEAD", "refs/remotes/origin/main"
        ])
        self.assertEqual(handler.selected_branches, ["refs/heads/origin/main"])

    def test_enter_selects_branch_with_upstream_without_another_git_query(self) -> None:
        self.select("refs/heads/topic")

        self.assertEqual(self.selected(), ["refs/heads/topic"])
        self.assertEqual(self.upstreams(), ["refs/remotes/origin/main"])
        self.assertFalse(self.settings.get("git_savvy.log_graph_view.all_branches"))
        self.assertFalse(self.settings.get("git_savvy.log_graph_view.overview"))
        verify(self.command, times=1).get_branches()
        verify(self.view).run_command("gs_log_graph_refresh", {"assume_complete_redraw": True})
        verify(panels, times=0).enqueue_on_ui(...)

    @p.expand([("ctrl",), ("primary",)])
    def test_modifier_enter_keeps_the_same_row_selected(self, modifier: str) -> None:
        self.select("refs/heads/main", modifier)

        verify(self.view).run_command("gs_log_graph_edit_branches", {"selected_index": 0})

    @p.expand([
        (["main", "topic"], "refs/heads/topic", 2),
        (["main"], "refs/heads/main", 2),
        (["main@{1}"], "main@{1}", 0)
    ])
    def test_unselect_keeps_focus_on_the_moved_branch_or_first_remaining_row(
        self, selected: list[str], ref: str, index: int
    ) -> None:
        self.set_selected(selected)
        self.select(ref, "ctrl")

        verify(self.view).run_command("gs_log_graph_edit_branches", {"selected_index": index})

    def test_unselect_keeps_a_shared_upstream(self) -> None:
        self.set_selected(["main", "topic"])
        self.select("refs/heads/topic")

        self.assertEqual(self.selected(), ["refs/heads/main"])
        self.assertEqual(self.upstreams(), ["refs/remotes/origin/main"])

    def test_unselect_preserves_an_explicitly_selected_upstream(self) -> None:
        self.set_selected(["main", "origin/main"])
        self.select("refs/heads/main")

        self.assertEqual(self.selected(), ["refs/remotes/origin/main"])
        self.assertEqual(self.upstreams(), [])

    def test_unselecting_explicit_upstream_still_includes_it_implicitly(self) -> None:
        self.set_selected(["main", "origin/main"])
        self.select("refs/remotes/origin/main")

        self.assertEqual(self.selected(), ["refs/heads/main"])
        self.assertEqual(self.upstreams(), ["refs/remotes/origin/main"])

    def test_selection_round_trip_from_all_branches(self) -> None:
        self.settings.set("git_savvy.log_graph_view.all_branches", True)
        self.select("refs/heads/topic")
        self.assertFalse(self.settings.get("git_savvy.log_graph_view.all_branches"))

        self.select("refs/heads/main")
        self.select("refs/heads/topic")
        self.assertEqual(self.selected(), ["refs/heads/main"])
        self.assertFalse(self.settings.get("git_savvy.log_graph_view.all_branches"))

        self.select("refs/heads/main")
        self.assertTrue(self.settings.get("git_savvy.log_graph_view.all_branches"))
        self.assertEqual(self.handler().selected_branches, [])

    def test_unselecting_last_branch_returns_to_all_branches(self) -> None:
        self.set_selected(["topic"])
        self.select("refs/heads/topic")

        self.assertEqual(self.selected(), [])
        self.assertEqual(self.upstreams(), [])
        self.assertTrue(self.settings.get("git_savvy.log_graph_view.all_branches"))

    @p.expand([
        (None, []),
        (Upstream("origin", "main", "origin/main", "gone"), []),
        (Upstream(".", "main", "main", ""), ["refs/heads/main"])
    ])
    def test_missing_and_local_upstreams(self, upstream: Upstream | None, expected: list[str]) -> None:
        when(self.command).get_branches().thenReturn([branch("topic", upstream=upstream)])
        self.select("refs/heads/topic")

        self.assertEqual(self.upstreams(), expected)

    @p.expand([("all_branches",), ("overview",)])
    def test_selection_leaves_all_or_overview_mode(self, mode: str) -> None:
        self.set_selected(["topic"])
        self.settings.set(f"git_savvy.log_graph_view.{mode}", True)
        self.select("refs/heads/topic")

        self.assertEqual(self.selected(), ["refs/heads/topic"])
        self.assertFalse(self.settings.get("git_savvy.log_graph_view.all_branches"))
        self.assertFalse(self.settings.get("git_savvy.log_graph_view.overview"))

    def test_path_and_log_filters_are_preserved(self) -> None:
        self.settings.set("git_savvy.log_graph_view.paths", ["src"])
        self.settings.set("git_savvy.log_graph_view.filters", "--author=someone")
        self.settings.set("git_savvy.log_graph_view.apply_filters", False)
        self.select("refs/heads/topic")

        self.assertEqual(self.settings.get("git_savvy.log_graph_view.paths"), ["src"])
        self.assertEqual(self.settings.get("git_savvy.log_graph_view.filters"), "--author=someone")
        self.assertFalse(self.settings.get("git_savvy.log_graph_view.apply_filters"))

    def test_head_and_existing_revision_selections_remain_editable(self) -> None:
        self.set_selected(["HEAD", "main@{1}"])
        handler = self.command.input({})
        items, _ = handler.list_items()

        self.assertEqual(handler.selected_branches, ["HEAD", "main@{1}"])
        self.assertEqual(items[1].value, "main@{1}")
        self.assertTrue(handler.validate("main@{1}"))

    def test_head_can_be_selected_alongside_branches_and_removed_independently(self) -> None:
        self.set_selected(["topic"])
        self.select("HEAD", "ctrl")

        self.assertEqual(self.selected(), ["refs/heads/topic", "HEAD"])
        self.assertEqual(self.handler().refs[:2], ["HEAD", "refs/heads/topic"])
        self.assertEqual(self.handler().refs.count("HEAD"), 1)
        self.assertEqual(self.upstreams(), ["refs/remotes/origin/main"])
        verify(self.view).run_command("gs_log_graph_edit_branches", {"selected_index": 0})

        self.select("HEAD")
        self.assertEqual(self.selected(), ["refs/heads/topic"])
        self.assertFalse(self.settings.get("git_savvy.log_graph_view.all_branches"))

    def test_head_selection_includes_its_upstream_without_resolving_to_branch_name(self) -> None:
        self.set_selected(["topic"])
        self.select("HEAD")
        self.select("refs/heads/topic")
        self.assertEqual(self.selected(), ["HEAD"])
        self.assertEqual(self.upstreams(), ["refs/remotes/origin/main"])
        self.assertEqual(
            self.handler().preview("HEAD"), "[ctrl+enter] to unselect, [enter] to also close"
        )

        self.select("HEAD")
        self.assertEqual(self.selected(), [])
        self.assertTrue(self.settings.get("git_savvy.log_graph_view.all_branches"))

    def test_detached_head_can_be_selected_without_an_upstream(self) -> None:
        when(self.command).get_branches().thenReturn([branch("topic")])
        self.set_selected(["topic"])
        self.select("HEAD")
        self.assertEqual(self.selected(), ["refs/heads/topic", "HEAD"])
        self.assertEqual(self.upstreams(), [])

    def test_preview_distinguishes_explicit_and_implicit_selection(self) -> None:
        self.set_selected(["topic"])
        handler = self.handler()
        self.assertEqual(
            handler.preview("refs/heads/topic"),
            "[ctrl+enter] to unselect, [enter] to also close"
        )
        self.assertEqual(
            handler.preview("refs/remotes/origin/main"),
            "[ctrl+enter] to select, [enter] to also close"
        )

    def test_head_appears_only_while_there_are_selections(self) -> None:
        self.assertNotIn("HEAD", self.handler().refs)
        self.select("refs/heads/topic")
        self.assertIn("HEAD", self.handler().refs)
        self.select("refs/heads/topic")
        self.assertNotIn("HEAD", self.handler().refs)

    @p.expand([("all_branches",), ("overview",)])
    def test_head_is_hidden_when_saved_selections_are_inactive(self, mode: str) -> None:
        self.set_selected(["HEAD", "topic"])
        self.settings.set(f"git_savvy.log_graph_view.{mode}", True)
        self.assertNotIn("HEAD", self.handler().refs)

    def test_empty_repository_has_a_non_actionable_message(self) -> None:
        when(self.command).get_branches().thenReturn([])
        handler = self.command.input({})
        items, _ = handler.list_items()

        self.assertEqual(items[0].text, "There are no branches available.")
        self.assertFalse(handler.validate(items[0].value))
        self.assertFalse(handler.validate("HEAD"))
        self.assertFalse(handler.validate(None))
        self.assertFalse(handler.validate("missing"))

    def test_cancel_hides_palette_without_changing_selection(self) -> None:
        self.set_selected(["main"])
        handler = self.handler()
        handler.confirm("refs/heads/topic", {"modifier_keys": {"ctrl": True}})
        handler.cancel()

        self.assertEqual(self.selected(), ["main"])
        self.assertIsNone(self.command._input_handler)
        verify(self.window).run_command("hide_overlay")
        verify(self.view, times=0).run_command(...)

    def test_opening_without_arguments_uses_the_input_palette(self) -> None:
        self.command.run(None, selected_index=2)

        verify(self.window).run_command("show_overlay", {
            "overlay": "command_palette",
            "command": "gs_log_graph_edit_branches",
            "args": {"selected_index": 2}
        })
        self.assertIsNone(self.command.input({"branch": "main"}))
        verify(self.command, times=0).get_branches()

    def select(self, ref: str, modifier: str | None = None) -> None:
        handler = self.handler()
        self.assertTrue(handler.want_event())
        self.assertTrue(handler.validate(ref))
        handler.confirm(ref, {"modifier_keys": {modifier: True}} if modifier else {})
        self.command.run(None, branch=ref)

    def handler(self) -> panels.GraphBranchesInputHandler:
        handler = self.command.input({})
        assert handler is not None
        handler.list_items()
        return handler

    def set_selected(self, refs: list[str]) -> None:
        self.settings.set("git_savvy.log_graph_view.branches", refs)

    def selected(self) -> list[str]:
        return self.settings.get("git_savvy.log_graph_view.branches", [])

    def upstreams(self) -> list[str]:
        return self.settings.get("git_savvy.log_graph_view.branch_upstreams", [])


class TestGraphBranchArguments(DeferrableTestCase):
    def setUp(self) -> None:
        self.view = sublime.active_window().new_file()
        self.view.set_scratch(True)
        self.addCleanup(self.view.close)
        self.addCleanup(unstub)
        self.command = mock({
            "view": self.view,
            "git_version": (2, 50, 0),
            "project_settings": {}
        })
        when(self.command).current_state().thenReturn({"remotes": {"origin": "url"}})
        self.settings = self.view.settings()
        self.settings.set("git_savvy.log_graph_view.branches", ["refs/heads/topic"])
        self.settings.set("git_savvy.log_graph_view.branch_upstreams", [
            "refs/remotes/origin/main", "refs/heads/topic"
        ])

    def test_rendering_includes_upstreams_without_duplicate_refs(self) -> None:
        args = gs_log_graph_refresh.build_git_command(self.command)

        self.assertNotIn("HEAD", args)
        self.assertEqual(args.count("refs/heads/topic"), 1)
        self.assertEqual(args.count("refs/remotes/origin/main"), 1)

    @p.expand([(None, 0), ([], 0), (["HEAD"], 1), (["HEAD", "refs/heads/topic"], 1)])
    def test_head_is_only_passed_when_selected(self, branches, expected_head_count: int) -> None:
        self.settings.set("git_savvy.log_graph_view.branches", branches)
        args = gs_log_graph_refresh.build_git_command(self.command)

        self.assertEqual(args.count("HEAD"), expected_head_count)
        self.assertNotIn("--all", args)
        if not branches:
            self.assertNotIn("refs/remotes/origin/main", args)

    @p.expand([("all_branches",), ("overview",)])
    def test_all_branches_and_overview_ignore_the_branch_selection(self, mode: str) -> None:
        self.settings.set(f"git_savvy.log_graph_view.{mode}", True)
        args = gs_log_graph_refresh.build_git_command(self.command)

        self.assertIn("--all", args)
        self.assertNotIn("HEAD", args)
        self.assertNotIn("refs/heads/topic", args)
        self.assertNotIn("refs/remotes/origin/main", args)


class TestGraphFileListing(DeferrableTestCase):
    def setUp(self) -> None:
        self.command = gs_log_graph_edit_files(mock())
        self.addCleanup(unstub)
        self.addCleanup(self.command.list_controlled_files.cache_clear)

    @p.expand([
        ("", []),
        ("normal.md\0", ["normal.md"]),
        (' spaced \0tab\tname\0line\nname\0quote".md\0ü.md\0',
         [" spaced ", "tab\tname", "line\nname", 'quote".md', "ü.md"])
    ])
    def test_preserves_filenames(self, output: str, expected: list[str]) -> None:
        when(self.command).git("ls-tree", ...).thenReturn(output)

        self.assertEqual(self.command.list_controlled_files(0), expected)
        verify(self.command).git("ls-tree", "-r", "-z", "--full-tree", "--name-only", "HEAD")

    def test_caches_until_graph_changes(self) -> None:
        when(self.command).git("ls-tree", ...).thenReturn("first\0", "second\0")

        self.assertEqual(self.command.list_controlled_files(0), ["first"])
        self.assertEqual(self.command.list_controlled_files(0), ["first"])
        self.assertEqual(self.command.list_controlled_files(1), ["second"])
        verify(self.command, times=2).git("ls-tree", ...)


class TestGraphFileInput(DeferrableTestCase):
    def setUp(self) -> None:
        settings_view = sublime.active_window().new_file()
        settings_view.set_scratch(True)
        self.addCleanup(settings_view.close)
        self.settings = settings_view.settings()
        self.window = mock()
        self.view = mock()
        when(self.view).settings().thenReturn(self.settings)
        when(self.view).window().thenReturn(self.window)
        when(self.view).change_count().thenReturn(0)
        self.command = gs_log_graph_edit_files(self.view)
        self.addCleanup(unstub)
        when(self.command).list_controlled_files(0).thenReturn(["README.md", "src/main.py"])
        when(self.view).run_command(...).thenReturn(None)
        when(self.window).run_command(...).thenReturn(None)
        when(panels).enqueue_on_ui(...).thenAnswer(lambda fn, *args: fn(*args))

    def test_opens_the_input_palette(self) -> None:
        self.command.run(None, selected_index=2)

        verify(self.window).run_command("show_overlay", {
            "overlay": "command_palette",
            "command": "gs_log_graph_edit_files",
            "args": {"selected_index": 2}
        })
        self.assertIsNone(self.command.input({"path": "README.md"}))

    def test_lists_selected_paths_first_then_files_and_directories(self) -> None:
        self.set_paths(["src", "deleted.py"])
        handler = self.command.input({"selected_index": 1})
        items, index = handler.list_items()

        self.assertEqual([item.value for item in items], [
            "src", "deleted.py", "README.md", "src/main.py"
        ])
        self.assertEqual([item.text for item in items], [
            "✓ src", "✓ deleted.py", "  README.md", "  src/main.py"
        ])
        self.assertEqual([item.annotation for item in items], ["", "", "", ""])
        self.assertEqual(index, 1)
        self.assertEqual(handler.name(), "path")

    @p.expand([(True, []), (False, ["src"])])
    def test_no_indentation_without_active_selected_paths(self, active: bool, paths: list[str]) -> None:
        self.set_paths(paths, active=active)
        handler = self.command.input({})
        items, _ = handler.list_items()

        self.assertEqual([item.text for item in items], ["README.md", "src", "src/main.py"])
        self.assertEqual([item.value for item in items], ["README.md", "src", "src/main.py"])

    @p.expand([(True, "unselect"), (False, "select")])
    def test_preview_describes_the_highlighted_item(self, active: bool, action: str) -> None:
        self.set_paths(["src"], active=active)
        handler = self.handler()

        self.assertEqual(
            handler.preview("src"), f"[ctrl+enter] to {action}, [enter] to also close"
        )
        self.assertEqual(
            handler.preview("README.md"), "[ctrl+enter] to select, [enter] to also close"
        )

    def test_empty_list_cannot_be_confirmed(self) -> None:
        when(self.command).list_controlled_files(0).thenReturn([])
        handler = self.command.input({})
        items, _ = handler.list_items()

        self.assertEqual(items[0].text, "There are no files available.")
        self.assertFalse(handler.validate(items[0].value))
        self.assertFalse(handler.validate("missing"))

    @p.expand([("src", []), ("README.md", ["src", "README.md"])])
    def test_enter_toggles_and_closes(self, path: str, expected: list[str]) -> None:
        self.set_paths(["src"])
        handler = self.handler()
        self.assertTrue(handler.want_event())
        self.assertTrue(handler.validate(path))
        handler.confirm(path, {})
        self.command.run(None, path=path)

        self.assertEqual(self.settings.get("git_savvy.log_graph_view.paths"), expected)
        verify(self.view).run_command("gs_log_graph_refresh", {
            "assume_complete_redraw": bool(expected)
        })
        verify(panels, times=0).enqueue_on_ui(...)

    @p.expand([
        ("src", "ctrl", 0),
        ("src/main.py", "ctrl", 1),
        ("README.md", "primary", 1)
    ])
    def test_ctrl_enter_toggles_and_reopens(
        self, path: str, modifier: str, selected_index: int
    ) -> None:
        self.set_paths(["src"])
        handler = self.handler()
        handler.confirm(path, {"modifier_keys": {modifier: True}})
        self.command.run(None, path=path)

        self.assertEqual(
            path in self.settings.get("git_savvy.log_graph_view.paths"), path != "src"
        )
        verify(self.view).run_command("gs_log_graph_edit_files", {"selected_index": selected_index})
        self.assertIsNone(self.command._input_handler)

    def test_selecting_with_disabled_filters_starts_fresh(self) -> None:
        self.set_paths(["src"], active=False)
        self.settings.set("git_savvy.log_graph_view.filters", "--author=someone")
        handler = self.handler()
        handler.confirm("src", {})
        self.command.run(None, path="src")

        self.assertEqual(self.settings.get("git_savvy.log_graph_view.paths"), ["src"])
        self.assertTrue(self.settings.get("git_savvy.log_graph_view.apply_filters"))
        self.assertEqual(self.settings.get("git_savvy.log_graph_view.filters"), "")

    def test_selecting_preserves_active_filters(self) -> None:
        self.set_paths([])
        self.settings.set("git_savvy.log_graph_view.filters", "--author=someone")
        handler = self.handler()
        handler.confirm("src", {})
        self.command.run(None, path="src")

        self.assertEqual(self.settings.get("git_savvy.log_graph_view.filters"), "--author=someone")

    def test_cancel_does_not_change_paths_or_leak_modifier_state(self) -> None:
        self.set_paths(["src"])
        handler = self.handler()
        handler.confirm("src", {"modifier_keys": {"ctrl": True}})
        handler.cancel()

        self.assertEqual(self.settings.get("git_savvy.log_graph_view.paths"), ["src"])
        self.command.run(None, path="README.md")
        verify(self.window).run_command("hide_overlay")
        verify(panels, times=1).enqueue_on_ui(...)

    def handler(self):
        handler = self.command.input({})
        handler.list_items()
        return handler

    def set_paths(self, paths: list[str], active: bool = True) -> None:
        self.settings.set("git_savvy.log_graph_view.paths", paths)
        self.settings.set("git_savvy.log_graph_view.apply_filters", active)


class TestGraphInputPalettes(DeferrableTestCase):
    def setUp(self) -> None:
        self.window = sublime.active_window()
        self.view = self.window.new_file()
        self.view.set_scratch(True)
        self.addCleanup(self.view.close)
        self.addCleanup(unstub)
        self.addCleanup(self.window.run_command, "hide_overlay")
        when(gs_log_graph_edit_files).list_controlled_files(...).thenReturn(["README.md", "src/main.py"])
        when(panels.gs_log_graph_edit_branches).get_branches().thenReturn([
            branch("topic", upstream=Upstream("origin", "topic", "origin/topic", ""))
        ])
        when(gs_log_graph_refresh).run(...).thenReturn(None)
        self.view.settings().set("git_savvy.log_graph_view", True)
        self.previews = []
        for handler in (panels.GraphFilesInputHandler, panels.GraphBranchesInputHandler):
            when(handler).preview(...).thenAnswer(
                lambda value: self.previews.append(value) or "Preview"
            )

    @p.expand([
        ("gs_log_graph_edit_files", "paths", "README.md"),
        ("gs_log_graph_edit_branches", "branches", "refs/heads/topic")
    ])
    def test_palette_invokes_handler_and_applies_selection(self, command, setting, value):
        self.view.run_command(command, {"selected_index": 0})
        yield lambda: bool(self.previews)
        self.assertEqual(self.previews[-1], value)

        self.window.run_command("select")
        yield lambda: self.view.settings().get(f"git_savvy.log_graph_view.{setting}") == [value]


def branch(
    name: str, date: int = 0, *, remote: str | None = None,
    upstream: Upstream | None = None, active: bool = False
) -> Branch:
    return Branch(
        name=name,
        remote=remote,
        canonical_name=f"{remote}/{name}" if remote else name,
        commit_hash=FullHash("a" * 40),
        commit_msg="",
        active=active,
        is_remote=remote is not None,
        committerdate=date,
        human_committerdate="now",
        relative_committerdate="now",
        upstream=upstream,
        distance_to_head=None,
        worktree_path=None
    )
