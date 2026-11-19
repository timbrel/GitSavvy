from __future__ import annotations

import sublime

from unittesting import DeferrableTestCase

from GitSavvy.core.commands import worktrees
from GitSavvy.core.ui__quick_panel import ActionType
from GitSavvy.core.git_mixins.worktrees import WorktreeLocation
from GitSavvy.core.interfaces import log_graph, log_graph_colorize
from GitSavvy.core.interfaces.multi_selector import GRAPH_MOUSE_TARGET_KEY as TARGET_KEY
from GitSavvy.core.interfaces.log_graph_panels import gs_log_graph_edit_branches
from GitSavvy.core.interfaces.log_graph_renderer import draw_prelude, gs_log_graph_refresh, prelude
from GitSavvy.core.git_mixins.branches import Upstream
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.tests.test_graph_panels import branch
from GitSavvy.tests.test_graph_prelude import prelude_ref_regions
from GitSavvy.core.interfaces.dashboard_mouse import (
    TARGET_KEY as DASHBOARD_TARGET_KEY, gs_dashboard_mouse
)
from GitSavvy.core.interfaces.log_graph_mouse import (
    GraphMouse, GsLogGraphMouseListener, gs_log_graph_mouse
)
from GitSavvy.tests.mockito import mock, spy2, unstub, verify, when


class TestGraphMouseGestures(DeferrableTestCase):
    def setUp(self) -> None:
        self.view = sublime.active_window().new_file()
        self.view.set_scratch(True)
        self.view.run_command("append", {"characters": "* abcdef0 (feature/foo-bar) subject\n"})
        self.view.settings().set("git_savvy.log_graph_view", True)
        self.target = sublime.Region(11, 26)
        self.event = {"x": 15, "y": 0, "button": 1}
        self.command = gs_log_graph_mouse(self.view)
        self.scheduled = []
        when(sublime).set_timeout(...).thenAnswer(
            lambda callback, *args: self.scheduled.append(callback)
        )
        when(log_graph.GsLogGraphCursorListener).is_applicable(...).thenReturn(False)
        when(log_graph_colorize).colorize_dots(self.view).thenReturn(None)
        when(log_graph_colorize).colorize_fixups(self.view).thenReturn(None)
        when(self.view).window_to_text(...).thenReturn(15)
        when(GraphMouse).target_at(15).thenReturn(self.target)
        spy2(self.view.run_command)
        when(self.view).run_command("drag_select", ...).thenReturn(None)
        self.menus = []
        when(GraphMouse).show_menu(...).thenAnswer(lambda *args: self.menus.append(args) or True)

    def tearDown(self) -> None:
        unstub()
        self.view.close()

    def test_left_click_selects_label_without_opening_menu(self) -> None:
        self.command.run(None, self.event)
        self.assertEqual(list(self.view.sel()), [self.target])
        for callback in self.scheduled:
            callback()
        self.assertEqual(self.menus, [])
        verify(self.view).run_command("drag_select", {"event": self.event, "by": "words"})

    def test_left_release_clears_selection_anchor(self) -> None:
        self.command.run(None, self.event)
        self.assertEqual(self.view.settings().get(TARGET_KEY), [11, 26])
        when(self.view).run_command("gs_log_graph_branch_drag", ...).thenReturn(None)
        self.command.run(None, self.event, release=True)
        self.assertIsNone(self.view.settings().get(TARGET_KEY))
        # Keep the clicked-label identity for a subsequent Space multiselect.
        self.assertEqual(self.view.settings().get("git_savvy.mouse_selected_target"), [11, 26])

    def test_registered_command_preserves_gesture_state_across_invocations(self) -> None:
        # Sublime constructs its own View wrapper for the registered command.
        when(sublime.View).window_to_text(...).thenReturn(15)

        for _ in range(2):
            self.view.run_command("gs_log_graph_mouse", {"event": self.event})
            self.assertEqual(self.view.settings().get(TARGET_KEY), [11, 26])
            self.assertEqual(list(self.view.sel()), [self.target])

            self.view.run_command("gs_log_graph_mouse", {"event": {}, "release": True})
            self.assertIsNone(self.view.settings().get(TARGET_KEY))
            self.assertEqual(self.view.settings().get("git_savvy.mouse_selected_target"), [11, 26])

    def test_right_click_selects_label_and_opens_menu_without_drag_tracking(self) -> None:
        self.event["button"] = 2
        self.command.run(None, self.event, context_menu=True)
        self.assertEqual(list(self.view.sel()), [self.target])
        self.assertEqual(self.scheduled, [])
        self.assertEqual(len(self.menus), 1)
        self.assertEqual(self.menus[0], (15, self.target, "abcdef0"))
        verify(self.view, times=0).run_command("drag_select", ...)
        self.assertIsNone(self.view.settings().get(TARGET_KEY))

    def test_right_click_outside_label_opens_normal_context_menu(self) -> None:
        when(GraphMouse).target_at(15).thenReturn(None)
        when(self.view).run_command("context_menu", {"event": self.event}).thenReturn(None)
        selection = list(self.view.sel())
        self.command.run(None, self.event, context_menu=True)
        verify(self.view).run_command("context_menu", {"event": self.event})
        self.assertEqual(list(self.view.sel()), selection)
        self.assertEqual(self.scheduled, [])
        self.assertEqual(self.menus, [])

    def test_right_click_label_without_actions_opens_normal_context_menu(self) -> None:
        when(GraphMouse).show_menu(...).thenReturn(False)
        when(self.view).run_command("context_menu", {"event": self.event}).thenReturn(None)
        self.command.run(None, self.event, context_menu=True)
        verify(self.view).run_command("context_menu", {"event": self.event})
        self.assertEqual(list(self.view.sel()), [self.target])
        self.assertEqual(self.scheduled, [])

    def test_right_click_elsewhere_only_clears_multiselect(self) -> None:
        when(self.view).run_command("context_menu", ...).thenReturn(None)
        for point in (0, 9, 15, 30):
            self.view.settings().set("git_savvy.multi_selection", [[2, 9]])
            when(self.view).window_to_text(...).thenReturn(point)
            when(self.view).run_command("gs_clear_multiselect").thenAnswer(
                lambda command: self.view.settings().erase("git_savvy.multi_selection")
            )
            self.command.run(None, self.event, context_menu=True)
            self.assertIsNone(self.view.settings().get("git_savvy.multi_selection"))
            self.assertEqual(self.menus, [])
            self.assertEqual(self.scheduled, [])
        verify(self.view, times=4).run_command("gs_clear_multiselect")
        verify(self.view, times=0).run_command("context_menu", ...)

    def test_alt_click_uses_native_selection_without_label_anchor(self) -> None:
        for command, target_key in (
            (self.command, TARGET_KEY),
            (gs_dashboard_mouse(self.view), DASHBOARD_TARGET_KEY)
        ):
            for by in ("characters", "words", "lines"):
                self.view.settings().set(target_key, [11, 26])
                self.view.settings().set("git_savvy.mouse_selected_target", [11, 26])
                command.run(None, self.event, text_selection=by)
                self.assertIsNone(self.view.settings().get(target_key))
                self.assertIsNone(self.view.settings().get("git_savvy.mouse_selected_target"))
        self.assertEqual(self.scheduled, [])
        verify(self.view, times=2).run_command("drag_select", {"event": self.event})
        for by in ("words", "lines"):
            verify(self.view, times=2).run_command("drag_select", {"event": self.event, "by": by})


class TestGraphPreludeMouse(DeferrableTestCase):
    def setUp(self) -> None:
        self.view = sublime.active_window().new_file()
        self.view.set_scratch(True)
        self.view.assign_syntax("Packages/GitSavvy/syntax/graph.sublime-syntax")
        settings = self.view.settings()
        settings.set("git_savvy.log_graph_view", True)
        settings.set("git_savvy.repo_path", "/repo")
        settings.set("git_savvy.log_graph_view.all_branches", False)
        settings.set("git_savvy.log_graph_view.apply_filters", True)
        settings.set("git_savvy.log_graph_view.filters", "all")
        draw_prelude(self.view, prelude(self.view))
        self.label = self.view.find("all", 0)
        self.event = {"x": 10, "y": 10, "button": 1}
        self.command = gs_log_graph_mouse(self.view)
        self.scheduled = []
        when(sublime).set_timeout(...).thenAnswer(
            lambda callback, *args: self.scheduled.append(callback)
        )
        when(log_graph.GsLogGraphCursorListener).is_applicable(...).thenReturn(False)
        when(log_graph_colorize).colorize_dots(self.view).thenReturn(None)
        when(log_graph_colorize).colorize_fixups(self.view).thenReturn(None)
        when(gs_log_graph_refresh).run(...).thenReturn(None)
        spy2(self.view.run_command)
        when(self.view).window_to_text(...).thenReturn(self.label.begin())
        when(self.view).run_command("drag_select", ...).thenReturn(None)
        when(self.view).run_command("context_menu", ...).thenReturn(None)
        when(self.view).run_command("gs_log_graph_branch_drag", ...).thenReturn(None)

    def tearDown(self) -> None:
        unstub()
        self.view.close()

    def test_clicking_each_letter_toggles_without_moving_selection(self) -> None:
        selection = list(self.view.sel())
        for point in range(self.label.begin(), self.label.end()):
            before = self.view.settings().get("git_savvy.log_graph_view.all_branches")
            self.view.settings().set("git_savvy.mouse_selected_target", [1, 3])
            when(self.view).window_to_text(...).thenReturn(point)
            self.command.run(None, self.event)
            self.command.run(None, self.event, release=True)

            self.assertEqual(self.view.settings().get("git_savvy.log_graph_view.all_branches"), not before)
            self.assertEqual(list(self.view.sel()), selection)
            self.assertIsNone(self.view.settings().get("git_savvy.mouse_selected_target"))
        self.assertEqual(self.scheduled, [])
        verify(gs_log_graph_refresh, times=3).run(...)
        verify(self.view, times=0).run_command("drag_select", ...)

    def test_multiclicks_on_all_toggle_instead_of_selecting_text(self) -> None:
        selection = list(self.view.sel())
        for by in ("words", "lines"):
            before = self.view.settings().get("git_savvy.log_graph_view.all_branches")
            self.command.run(None, self.event, fallback_selection=by)
            self.assertEqual(self.view.settings().get("git_savvy.log_graph_view.all_branches"), not before)
            self.assertEqual(list(self.view.sel()), selection)
        verify(gs_log_graph_refresh, times=2).run(...)
        verify(self.view, times=0).run_command("drag_select", ...)

    def test_multiclicks_elsewhere_retain_native_selection(self) -> None:
        when(self.view).window_to_text(...).thenReturn(self.label.end() + 2)
        for by in ("words", "lines"):
            self.view.settings().set("git_savvy.mouse_selected_target", [1, 3])
            self.command.run(None, self.event, fallback_selection=by)
            verify(self.view).run_command("drag_select", {"event": self.event, "by": by})
            self.assertIsNone(self.view.settings().get("git_savvy.mouse_selected_target"))
        verify(gs_log_graph_refresh, times=0).run(...)

    def test_nearby_text_and_filter_named_all_do_not_toggle(self) -> None:
        filter_label = self.view.find_by_selector("meta.prelude.all.git_savvy.graph")[1]
        for point in (self.label.begin() - 1, self.label.end(), self.label.end() + 2, filter_label.begin()):
            when(self.view).window_to_text(...).thenReturn(point)
            self.command.run(None, self.event)

        self.assertFalse(self.view.settings().get("git_savvy.log_graph_view.all_branches"))
        verify(gs_log_graph_refresh, times=0).run(...)

    def test_dormant_choice_clicks_select_the_exact_ref_and_its_upstream(self) -> None:
        settings = self.view.settings()
        refs = ["refs/heads/topic", "refs/remotes/origin/main", "refs/tags/topic"]
        when(gs_log_graph_edit_branches).get_branches().thenReturn([
            branch("topic", upstream=Upstream("origin", "main", "origin/main", "")),
            branch("main", remote="origin")
        ])
        selection = list(self.view.sel())
        for index, ref in enumerate(refs):
            settings.set("git_savvy.log_graph_view.all_branches", True)
            settings.set("git_savvy.log_graph_view.branches", ["refs/heads/stale"])
            settings.set("git_savvy.log_graph_view.branch_upstreams", ["refs/remotes/origin/stale"])
            settings.set("git_savvy.log_graph_view.branch_choices", refs)
            draw_prelude(self.view, prelude(self.view))
            target = prelude_ref_regions(self.view)[index]
            when(self.view).window_to_text(...).thenReturn(target.begin())
            self.command.run(None, self.event)

            self.assertFalse(settings.get("git_savvy.log_graph_view.all_branches"))
            self.assertEqual(settings.get("git_savvy.log_graph_view.branches"), [ref])
            self.assertEqual(settings.get("git_savvy.log_graph_view.branch_upstreams"),
                             ["refs/remotes/origin/main"] if index == 0 else [])
            self.assertEqual(list(self.view.sel()), selection)
        verify(gs_log_graph_edit_branches, times=3).get_branches()
        verify(self.view, times=0).run_command("drag_select", ...)

    def test_clicking_selected_ref_removes_only_that_item(self) -> None:
        settings = self.view.settings()
        settings.set("git_savvy.log_graph_view.branches", ["refs/heads/all", "refs/tags/v1"])
        when(gs_log_graph_edit_branches).get_branches().thenReturn([branch("all")])
        draw_prelude(self.view, prelude(self.view))
        target = prelude_ref_regions(self.view)[0]
        when(self.view).window_to_text(...).thenReturn(target.begin())
        self.command.run(None, self.event, fallback_selection="words")

        self.assertFalse(settings.get("git_savvy.log_graph_view.all_branches"))
        self.assertEqual(settings.get("git_savvy.log_graph_view.branches"), ["refs/tags/v1"])
        verify(self.view, times=0).run_command("drag_select", ...)

    def test_all_toggle_restores_branch_after_clicking_it_off(self) -> None:
        settings = self.view.settings()
        settings.set("git_savvy.log_graph_view.branches", ["refs/heads/worker-profiler"])
        when(gs_log_graph_edit_branches).get_branches().thenReturn([branch("worker-profiler")])
        when(gs_log_graph_refresh).run(...).thenAnswer(
            lambda *args, **kwargs: draw_prelude(self.view, prelude(self.view))
        )
        draw_prelude(self.view, prelude(self.view))
        target = prelude_ref_regions(self.view)[0]
        when(self.view).window_to_text(...).thenReturn(target.begin())
        self.command.run(None, self.event)
        self.assertTrue(settings.get("git_savvy.log_graph_view.all_branches"))
        self.assertEqual(self.view.get_regions("gs_log_graph.branches"), [])

        self.assertEqual(settings.get("git_savvy.log_graph_view.branches"), ["refs/heads/worker-profiler"])
        for _ in range(2):
            self.view.run_command("gs_log_graph_toggle_all_setting")
            self.assertEqual(
                [self.view.substr(r) for r in self.view.get_regions("gs_log_graph.branches")],
                ["worker-profiler"]
            )
            self.view.run_command("gs_log_graph_toggle_all_setting")
            self.assertEqual(self.view.get_regions("gs_log_graph.branches"), [])

    def test_comparison_refs_stay_in_place_when_toggled_off_and_on(self) -> None:
        settings = self.view.settings()
        settings.set("git_savvy.log_graph_view.branches", ["topic", "origin/main", "7d00d223^!"])
        when(gs_log_graph_edit_branches).get_branches().thenReturn([
            branch("topic"), branch("main", remote="origin")
        ])
        when(gs_log_graph_refresh).run(...).thenAnswer(
            lambda *args, **kwargs: draw_prelude(self.view, prelude(self.view))
        )
        draw_prelude(self.view, prelude(self.view))
        header = prelude(self.view).text
        targets = prelude_ref_regions(self.view)
        selected = ["topic", "origin/main", "7d00d223^!"]
        for index in (1, 2, 0, 2, 0, 1):
            name = ["topic", "origin/main", "7d00d223^!"][index]
            if name in selected:
                selected.remove(name)
            else:
                selected.append(name)
            when(self.view).window_to_text(...).thenReturn(targets[index].begin())
            self.command.run(None, self.event, fallback_selection="words")
            self.assertEqual(prelude(self.view).text, header)
            self.assertEqual(prelude_ref_regions(self.view), targets)
            self.assertCountEqual(
                [self.view.substr(r) for r in self.view.get_regions("gs_log_graph.branches")], selected
            )
            self.assertEqual(settings.get("git_savvy.log_graph_view.all_branches"), not selected)
        verify(self.view, times=0).run_command("drag_select", ...)

    def test_ref_separators_and_filter_text_are_not_click_targets(self) -> None:
        settings = self.view.settings()
        settings.set("git_savvy.log_graph_view.all_branches", True)
        settings.set("git_savvy.log_graph_view.apply_filters", True)
        settings.set("git_savvy.log_graph_view.filters", "topic")
        refs = ["refs/heads/topic", "refs/tags/v1"]
        settings.set("git_savvy.log_graph_view.branch_choices", refs)
        draw_prelude(self.view, prelude(self.view))
        regions = prelude_ref_regions(self.view)
        for point in (regions[0].begin() - 1, regions[0].end(), regions[1].end(),
                      self.view.find("topic", regions[0].end()).begin()):
            when(self.view).window_to_text(...).thenReturn(point)
            self.command.run(None, self.event, fallback_selection="words")
        verify(self.view, times=0).run_command("gs_log_graph_edit_branches", ...)
        verify(self.view, times=4).run_command("drag_select", ...)

    @p.expand([(True,), (False,)])
    def test_shift_click_edits_branch_choices_without_selecting_text(self, all_mode: bool) -> None:
        settings = self.view.settings()
        settings.set("git_savvy.log_graph_view.all_branches", all_mode)
        self.view.run_command("append", {"characters": "* abcdef0 (topic, origin/main) a subject\n"})
        when(gs_log_graph_edit_branches).get_branches().thenReturn([branch("topic"), branch("main", remote="origin")])
        local, remote = "refs/heads/topic", "refs/remotes/origin/main"
        selection = list(self.view.sel())
        for label, expected in (("topic", [local]), ("origin/main", [local, remote]),
                                ("topic", [remote]), ("origin/main", [])):
            when(self.view).window_to_text(...).thenReturn(self.view.find(label, 0).end() - 1)
            self.command.run(None, self.event, toggle_branch_choice=True)
            self.assertEqual(settings.get("git_savvy.log_graph_view.branch_choices"), expected)
            self.assertEqual(settings.get("git_savvy.log_graph_view.branches"), expected)
            self.assertEqual(settings.get("git_savvy.log_graph_view.all_branches"), all_mode or not expected)
        self.assertEqual(list(self.view.sel()), selection)
        verify(self.view, times=0).run_command("gs_log_graph_branch_drag", ...)
        verify(self.view, times=0).run_command("drag_select", ...)

    def test_shift_multiclick_bindings_toggle_choices_or_extend_native_selection(self) -> None:
        bindings = sublime.decode_value(sublime.load_resource("Packages/GitSavvy/Default.sublime-mousemap"))
        self.view.run_command("append", {"characters": "* abcdef0 (topic) subject\n"})
        self.view.settings().set("git_savvy.log_graph_view.all_branches", True)
        when(gs_log_graph_edit_branches).get_branches().thenReturn([branch("topic")])
        for count, by in ((2, "words"), (3, "lines")):
            binding = next(
                b for b in reversed(bindings)
                if b["button"] == "button1" and b.get("modifiers") == ["shift"] and b.get("count") == count
                and any(c["key"] == "setting.git_savvy.log_graph_view" for c in b.get("context", []))
            )
            self.assertEqual(binding["press_command"], "gs_log_graph_mouse")
            for label in ("topic", "subject"):
                when(self.view).window_to_text(...).thenReturn(self.view.find(label, 0).begin())
                self.command.run(None, self.event, **binding["press_args"])

            self.assertEqual(self.view.settings().get("git_savvy.log_graph_view.branch_choices"),
                             ["refs/heads/topic"] if count == 2 else [])
            self.assertTrue(self.view.settings().get("git_savvy.log_graph_view.all_branches"))
            verify(self.view).run_command("drag_select", {"event": self.event, "extend": True, "by": by})

    @p.expand([(True,), (False,)])
    def test_shift_click_removes_prelude_choice_even_before_redraw(self, all_mode: bool) -> None:
        settings = self.view.settings()
        remaining = "refs/heads/main"
        settings.set("git_savvy.log_graph_view.branches", ["refs/heads/topic", remaining])
        settings.set("git_savvy.log_graph_view.all_branches", all_mode)
        when(gs_log_graph_edit_branches).get_branches().thenReturn([branch("topic"), branch("main")])
        draw_prelude(self.view, prelude(self.view))
        when(self.view).window_to_text(...).thenReturn(prelude_ref_regions(self.view)[0].begin())
        for by in (None, "words", "lines"):
            self.command.run(None, self.event, toggle_branch_choice=True, fallback_selection=by)
            self.assertEqual(settings.get("git_savvy.log_graph_view.branch_choices"), [remaining])
            self.assertEqual(settings.get("git_savvy.log_graph_view.branches"), [remaining])
            self.assertEqual(settings.get("git_savvy.log_graph_view.all_branches"), all_mode)

    def test_shift_click_elsewhere_extends_selection(self) -> None:
        self.view.run_command("append", {"characters": "* abcdef0 (HEAD, tag: topic) topic\n"})
        for label in ("all", "abcdef0", "HEAD", "topic)", "topic\n"):
            when(self.view).window_to_text(...).thenReturn(self.view.find(label, 0, sublime.LITERAL).begin())
            self.command.run(None, self.event, toggle_branch_choice=True)
        verify(self.view, times=5).run_command("drag_select", {"event": self.event, "extend": True})
        verify(self.view, times=0).run_command("gs_log_graph_edit_branches", ...)

    def test_controls_and_explicit_text_selection_bypass_graph_handler(self) -> None:
        when(GraphMouse).press(...).thenRaise(AssertionError("Unexpected graph interaction"))

        for by in (None, "words", "lines"):
            self.command.run(None, self.event, fallback_selection=by)
            self.command.run(None, self.event, text_selection=by or "characters")

        self.view.settings().set("git_savvy.log_graph_view.branches", ["refs/heads/topic"])
        when(gs_log_graph_edit_branches).get_branches().thenReturn([branch("topic")])
        draw_prelude(self.view, prelude(self.view))
        when(self.view).window_to_text(...).thenReturn(prelude_ref_regions(self.view)[0].begin())
        self.command.run(None, self.event)

        verify(GraphMouse, times=0).press(...)

    def test_right_click_retains_native_context_menu(self) -> None:
        self.command.run(None, {**self.event, "button": 2}, context_menu=True)
        verify(self.view).run_command("context_menu", ...)
        verify(gs_log_graph_refresh, times=0).run(...)

    def test_alt_click_still_selects_text(self) -> None:
        self.command.run(None, self.event, text_selection="characters")
        verify(self.view).run_command("drag_select", {"event": self.event})
        verify(gs_log_graph_refresh, times=0).run(...)


class TestGraphMouseBindings(DeferrableTestCase):
    def test_multiclick_bindings_try_graph_actions_before_native_selection(self) -> None:
        bindings = sublime.decode_value(sublime.load_resource("Packages/GitSavvy/Default.sublime-mousemap"))
        for count, by in ((2, "words"), (3, "lines")):
            binding = next(
                binding for binding in reversed(bindings)
                if binding["button"] == "button1" and binding.get("modifiers", []) == []
                and binding.get("count") == count
                and any(c["key"] == "setting.git_savvy.log_graph_view" for c in binding.get("context", []))
            )
            self.assertEqual(binding["press_command"], "gs_log_graph_mouse")
            self.assertEqual(binding["press_args"], {"fallback_selection": by})
            self.assertEqual(binding["args"], {"release": True})

    def test_right_click_priority(self) -> None:
        bindings = sublime.decode_value(sublime.load_resource("Packages/GitSavvy/Default.sublime-mousemap"))
        for multi, dragging, expected in (
            (False, False, "gs_log_graph_mouse"),
            (True, False, "gs_log_graph_mouse"),
            (False, True, "gs_log_graph_branch_drag"),
            (True, True, "gs_log_graph_branch_drag")
        ):
            contexts = {
                "setting.git_savvy.log_graph_view": True,
                "git_savvy.has_multi_selection": multi,
                "git_savvy.graph_branch_drag_active": dragging
            }
            binding = next(
                binding for binding in reversed(bindings)
                if binding["button"] == "button2" and binding.get("modifiers", []) == []
                and all(contexts.get(c["key"]) == c["operand"] for c in binding.get("context", []))
            )
            command = binding.get("press_command")
            if command == "noop":
                command = binding["command"]
            self.assertEqual(command, expected)


class TestGraphLabelSelection(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_dismissing_action_menu_releases_selection_anchor(self) -> None:
        view = sublime.active_window().new_file()
        view.set_scratch(True)
        self.addCleanup(view.close)
        view.run_command("append", {"characters": "* abcdef0 (topic) subject\n* 1234567 next\n"})
        target = sublime.Region(11, 16)
        view.sel().clear()
        view.sel().add(target)
        view.settings().set("git_savvy.log_graph_view", True)
        view.settings().set(TARGET_KEY, [target.a, target.b])
        command = GraphMouse(view)
        when(command).target_at(11).thenReturn(target)
        branch = mock({"name": "topic"})
        when(command).branch_at(11).thenReturn(branch)
        when(command).branch_actions(view.window(), branch).thenReturn([
            ("Rename 'topic'", lambda: self.fail("Cancelled menu ran an action"))
        ])
        captured = []
        when(view).show_popup_menu(...).thenAnswer(lambda *args: captured.append(args))

        command.show_menu(11, target, "abcdef0")
        captured[0][1](-1)
        # The popup can swallow mouse-up, so no mouse release command follows.
        view.run_command("move", {"by": "lines", "forward": True})
        GsLogGraphMouseListener().on_selection_modified(view)
        self.assertTrue(view.sel()[0].empty(), repr(view.sel()[0]))
        self.assertEqual(view.rowcol(view.sel()[0].b)[0], 1)
        self.assertIsNone(view.settings().get(TARGET_KEY))

    def test_horizontal_drag_preserves_initial_label_in_both_directions(self) -> None:
        view = sublime.active_window().new_file()
        view.set_scratch(True)
        try:
            view.run_command("append", {"characters": "before feature/foo-bar after"})
            target = sublime.Region(7, 22)
            listener = GsLogGraphMouseListener()
            for selection, expected in (
                (sublime.Region(15, 28), sublime.Region(7, 28)),
                (sublime.Region(18, 0), sublime.Region(22, 0))
            ):
                view.settings().set("git_savvy.log_graph_mouse_target", [target.a, target.b])
                view.sel().clear()
                view.sel().add(selection)
                listener.on_selection_modified(view)
                self.assertEqual(list(view.sel()), [expected])
                # Further native tracking must still include the original label.
                view.sel().clear()
                view.sel().add(selection)
                listener.on_selection_modified(view)
                self.assertEqual(list(view.sel()), [expected])
        finally:
            view.close()


class TestGraphCheckoutActions(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_branch_menu_fetches_branches_once(self) -> None:
        view = sublime.active_window().new_file()
        view.set_scratch(True)
        self.addCleanup(view.close)
        view.run_command("append", {"characters": "* abcdef0 (feature) subject\n"})
        target = sublime.Region(11, 18)
        command = GraphMouse(view)
        when(view).find_by_selector(...).thenReturn([target])
        when(view).show_popup_menu(...).thenReturn(None)
        when(command).current_state().thenReturn({})
        when(command).get_branches().thenReturn([mock({
            "name": "feature", "is_remote": False, "active": False,
            "upstream": None, "commit_hash": "abcdef0"
        })])

        self.assertTrue(command.show_menu(11, target, "abcdef0"))
        verify(command, times=1).get_branches()

    def test_branch_without_upstream(self) -> None:
        menu = self.branch_menu(active=False, upstream=None)
        self.assertEqual([
            "Checkout here", "Checkout in a worktree", "-", "Push...", "-", "Rename 'feature'", "Delete 'feature'"
        ], [description for description, _ in menu])
        menu[-2][1]()
        verify(self.window).run_command("gs_rename_branch", {"branch": "feature"})
        menu[-1][1]()
        verify(self.window).run_command("gs_delete_branch", {"branch": "feature", "undo_owner": 77})
        menu[0][1]()
        verify(self.window).run_command("gs_checkout_branch", {"branch": "feature"})
        menu[1][1]()
        verify(self.window).run_command("gs_checkout_in_worktree", {"start_point": "feature"})

    def test_current_branch_with_upstream(self) -> None:
        menu = self.branch_menu(
            active=True, upstream=mock({"remote": "origin", "branch": "master", "canonical_name": "origin/master"}),
            worktree_path="/repo/current", is_current=True
        )
        self.assertEqual([
            "Checkout in a new worktree", "-", "Push", "Merge into...", "Pull", "Fetch origin/master",
            "-", "Rename 'feature'", "Delete 'feature'"
        ], [description for description, _ in menu])
        menu[0][1]()
        verify(self.window).run_command("gs_checkout_in_worktree", {"start_point": "abcdef0"})
        menu[3][1]()
        verify(self.window).run_command("gs_merge_current_branch_into", {"source": "feature"})
        menu[4][1]()
        verify(self.window).run_command("gs_pull", {})
        menu[5][1]()
        verify(self.window).run_command("gs_fetch", {"remote": "origin", "refspec": "master"})

    def test_current_branch_in_bisect_does_not_offer_merge_into(self) -> None:
        menu = self.branch_menu(active=True, upstream=None, in_bisect=True)
        self.assertNotIn("Merge into...", [description for description, _ in menu])

    def test_available_branch_with_upstream(self) -> None:
        menu = self.branch_menu(active=False, upstream=mock({
            "remote": "fork", "branch": "topic/nested",
            "canonical_name": "fork/topic/nested", "status": ""
        }))
        self.assertEqual([
            "Checkout here", "Checkout in a worktree", "-", "Push",
            "Update feature from fork/topic/nested (ff-only)", "Fetch fork/topic/nested",
            "-", "Rename 'feature'", "Delete 'feature'"
        ], [description for description, _ in menu])
        menu[4][1]()
        verify(self.window).run_command("gs_fetch", {"remote": "fork", "refspec": "topic/nested:feature"})
        menu[5][1]()
        verify(self.window).run_command("gs_fetch", {"remote": "fork", "refspec": "topic/nested"})

    def test_branch_behind_upstream_offers_move(self) -> None:
        menu = self.branch_menu(active=False, upstream=mock({
            "remote": "origin", "branch": "master", "canonical_name": "origin/master",
            "status": "behind 2"
        }))
        self.assertEqual(menu[4][0], "Move feature to origin/master")
        menu[4][1]()
        verify(self.window).run_command("gs_move_branch", {
            "branch": "feature", "target": "origin/master", "previous_tip": "abcdef0"
        })

    def test_diverged_branch_still_offers_fetch_update(self) -> None:
        menu = self.branch_menu(active=False, upstream=mock({
            "remote": "origin", "branch": "master", "canonical_name": "origin/master",
            "status": "ahead 1, behind 2"
        }))
        self.assertEqual(menu[4][0], "Update feature from origin/master (ff-only)")
        menu[4][1]()
        verify(self.window).run_command("gs_fetch", {"remote": "origin", "refspec": "master:feature"})

    def test_sha_menu_shows_create_before_checkout_actions(self) -> None:
        view = sublime.active_window().new_file()
        view.set_scratch(True)
        self.addCleanup(view.close)
        view.run_command("append", {"characters": "* abcdef0 subject\n"})
        target = sublime.Region(2, 9)
        view.sel().clear()
        view.sel().add(target)
        view.settings().set("git_savvy.log_graph_view", True)
        command = GraphMouse(view)
        when(command).target_at(2).thenReturn(target)
        when(command).branch_at(2).thenReturn(None)
        when(command).fixup_actions(2, "abcdef0").thenReturn([])
        when(command).cherry_pick_and_revert_actions(...).thenReturn([])
        when(view).match_selector(2, "string.other.rebase-hint.git-savvy").thenReturn(False)
        when(view).match_selector(2, "constant.numeric.graph.commit-hash.git-savvy").thenReturn(True)
        captured = []
        when(view).show_popup_menu(...).thenAnswer(lambda *args: captured.append(args))
        window = mock()
        when(view).window().thenReturn(window)
        when(window).active_view().thenReturn(view)
        when(window).run_command(...).thenReturn(None)

        command.show_menu(2, target, "abcdef0")

        labels, on_done, _ = captured[0]
        self.assertEqual([
            "Create branch at abcdef0", "Create tag...", "-",
            "Checkout here", "Checkout in a worktree", "-"
        ], labels[:6])
        on_done(3)
        verify(window).run_command("gs_checkout_branch", {"branch": "abcdef0", "detach": True})
        on_done(4)
        verify(window).run_command(
            "gs_checkout_in_worktree", {"start_point": "abcdef0"}
        )

    def test_fixup_hint_uses_its_own_actions_and_shared_menu(self) -> None:
        view = sublime.active_window().new_file()
        view.set_scratch(True)
        self.addCleanup(view.close)
        view.run_command("append", {"characters": "* abcdef0 fixup! subject\n"})
        target = sublime.Region(10, 15)
        handler = GraphMouse(view)
        when(handler).branch_at(10).thenReturn(None)
        when(handler).get_branches().thenRaise(AssertionError("Unexpected branch query"))
        when(handler).commit_actions(...).thenRaise(AssertionError("Unexpected commit actions"))
        when(view).match_selector(10, "string.other.rebase-hint.git-savvy").thenReturn(True)
        args = {"base_commit": "1234567", "fixes": []}
        when(handler).fixup_actions(10, "abcdef0").thenReturn(
            [], [("Apply fix", lambda: view.run_command("gs_rebase_apply_fixup", args))]
        )
        captured = []
        when(view).show_popup_menu(...).thenAnswer(lambda *args: captured.append(args))
        when(view).run_command("gs_rebase_apply_fixup", args).thenReturn(None)

        self.assertFalse(handler.show_menu(10, target, "abcdef0"))
        self.assertEqual(captured, [])
        self.assertTrue(handler.show_menu(10, target, "abcdef0"))
        labels, on_done, point = captured[0]
        self.assertEqual(labels, ["Apply fix"])
        self.assertEqual(point, 10)
        on_done(0)
        verify(view).run_command("gs_rebase_apply_fixup", args)

    def test_two_selected_commits_offer_direct_diff(self) -> None:
        view = sublime.active_window().new_file()
        view.set_scratch(True)
        self.addCleanup(view.close)
        view.run_command("append", {"characters": "* abcdef0 newer\n* 1234567 older\n"})
        view.settings().set("git_savvy.log_graph_view", True)
        view.settings().set("git_savvy.multi_selection", [[2, 9], [18, 25]])
        view.sel().clear()
        view.sel().add(sublime.Region(2, 9))
        command = GraphMouse(view)
        when(command).target_at(2).thenReturn(sublime.Region(2, 9))
        when(command).branch_at(2).thenReturn(None)
        when(command).cherry_pick_and_revert_actions(...).thenReturn([])
        when(view).match_selector(2, "string.other.rebase-hint.git-savvy").thenReturn(False)
        when(view).match_selector(2, "constant.numeric.graph.commit-hash.git-savvy").thenReturn(True)
        captured = []
        when(view).show_popup_menu(...).thenAnswer(lambda *args: captured.append(args))
        window = mock()
        when(view).window().thenReturn(window)
        when(window).active_view().thenReturn(view)
        when(window).run_command(...).thenReturn(None)

        when(window).focus_view(view).thenReturn(None)
        when(view).window_to_text(...).thenReturn(2)
        when(log_graph_colorize).colorize_dots(view).thenReturn(None)
        when(log_graph_colorize).colorize_fixups(view).thenReturn(None)
        when(view).run_command("gs_rebase_squash_commits", ...).thenReturn(None)

        command.press(2, {"x": 2, "y": 0, "button": 2}, context_menu=True)

        self.assertEqual(view.settings().get("git_savvy.multi_selection"), [[2, 9], [18, 25]])
        labels, on_done, _ = captured[0]
        self.assertEqual(["Diff 1234567..abcdef0", "-"], labels[:2])
        on_done(0)
        verify(window).run_command("gs_diff", {
            "base_commit": "1234567", "target_commit": "abcdef0",
            "in_cached_mode": False, "disable_stage": True
        })
        on_done(2)
        verify(view).run_command("gs_rebase_squash_commits", {
            "base_commit": "1234567", "commits": ["abcdef0"]
        })

    def test_cached_worktree_location_detaches_even_without_branch_worktreepath(self) -> None:
        menu = self.branch_menu(active=False, upstream=None, worktree_path="/repo/feature")
        self.assertEqual([
            "Open worktree in a new window", "Checkout in a new worktree", "-", "Push...",
            "-", "Rename 'feature'", "Delete 'feature'"
        ], [description for description, _ in menu])
        menu[1][1]()
        verify(self.window).run_command("gs_checkout_in_worktree", {"start_point": "abcdef0"})
        menu[0][1]()
        verify(self.window).run_command("gs_open_worktree_in_new_window", {"worktree": "/repo/feature"})

    def test_branch_checked_out_elsewhere_offers_fetch_but_not_pull(self) -> None:
        menu = self.branch_menu(
            active=False, upstream=mock({
                "remote": "origin", "branch": "master", "canonical_name": "origin/master"
            }), worktree_path="/repo/feature"
        )
        self.assertEqual([
            "Open worktree in a new window", "Checkout in a new worktree", "-", "Push", "Fetch origin/master",
            "-", "Rename 'feature'", "Delete 'feature'"
        ], [description for description, _ in menu])

    def test_open_worktree_command_only_opens_the_existing_checkout(self) -> None:
        command = worktrees.gs_open_worktree_in_new_window(mock())
        when(command).get_worktrees().thenRaise(AssertionError("Redundant worktree query"))
        when(command).create_new_worktree(...).thenRaise(AssertionError("Existing worktree"))
        when(command).open_worktree_in_new_window("/repo/feature").thenReturn(None)

        worktrees.gs_open_worktree_in_new_window.run.__wrapped__(command, worktree="/repo/feature")

        verify(command).open_worktree_in_new_window("/repo/feature")

    def test_worktree_checkout_defaults_to_resolved_head(self) -> None:
        command = worktrees.gs_checkout_in_worktree(mock())
        when(command).resolve_commitish("HEAD").thenReturn("abcdef0")
        when(command).create_new_worktree("abcdef0").thenReturn("/repo/new")
        when(command).open_worktree_in_new_window("/repo/new", then=command.show_graph).thenReturn(None)

        worktrees.gs_checkout_in_worktree.run.__wrapped__(command)

        verify(command).resolve_commitish("HEAD")
        verify(command).create_new_worktree("abcdef0")
        verify(command).open_worktree_in_new_window("/repo/new", then=command.show_graph)

    def test_worktree_checkout_passes_available_branch_to_git(self) -> None:
        self.check_worktree_checkout("feature")

    def test_worktree_checkout_passes_sha_to_git(self) -> None:
        self.check_worktree_checkout("abcdef0")

    def branch_menu(
        self, *, active: bool, upstream: object, worktree_path: str | None = None,
        is_current: bool = False, in_bisect: bool = False
    ) -> list[ActionType]:
        self.window = mock()
        when(self.window).run_command(...).thenReturn(None)
        view = mock()
        when(view).id().thenReturn(77)
        command = GraphMouse(view)
        when(command).in_bisect().thenReturn(in_bisect)
        when(command).current_state().thenReturn({
            "worktree_locations": [WorktreeLocation(worktree_path, "feature", None, is_current)]
            if worktree_path else []
        })
        when(command).get_worktree_locations().thenRaise(AssertionError("Discovery in menu"))
        when(command).get_worktrees().thenRaise(AssertionError("Git query in menu"))
        branch = mock({
            "name": "feature", "is_remote": False, "active": active,
            "upstream": upstream, "worktree_path": None, "commit_hash": "abcdef0"
        })
        when(command).get_branches().thenRaise(AssertionError("Redundant branch lookup"))
        return command.branch_actions(self.window, branch)

    def check_worktree_checkout(self, start_point: str) -> None:
        command = worktrees.gs_checkout_in_worktree(mock())
        when(command).get_worktrees().thenRaise(AssertionError("Redundant worktree query"))
        when(command).create_new_worktree(start_point).thenReturn("/repo/new")
        when(command).open_worktree_in_new_window("/repo/new", then=command.show_graph).thenReturn(None)

        worktrees.gs_checkout_in_worktree.run.__wrapped__(command, start_point)

        verify(command).create_new_worktree(start_point)
        verify(command).open_worktree_in_new_window("/repo/new", then=command.show_graph)
