from unittesting import DeferrableTestCase

import sublime

from GitSavvy.core.interfaces import multi_selector
from GitSavvy.core.interfaces.dashboard_mouse import GsDashboardMouseListener
from GitSavvy.core.interfaces.log_graph_mouse import GsLogGraphMouseListener
from GitSavvy.tests.mockito import unstub, verify, when


class TestMouseLabelMultiselect(DeferrableTestCase):
    def setUp(self) -> None:
        self.view = sublime.active_window().new_file()
        self.view.set_scratch(True)
        self.view.run_command("append", {"characters": "* abcdef0 (topic) first\n* 1234567 second\n"})
        self.label = sublime.Region(11, 16)

    def tearDown(self) -> None:
        self.view.close()
        unstub()

    def test_space_on_clicked_label_adds_and_advances(self) -> None:
        for interface in ("graph", "branch", "tags", "status"):
            with self.subTest(interface=interface):
                command, navigation, args, wanted = self.prepare(interface, self.label)
                command.run(None)
                self.assertEqual(self.view.settings().get("git_savvy.multi_selection"), [wanted])
                verify(self.view).run_command(navigation, *args)
                self.assertIsNone(self.view.settings().get("git_savvy.mouse_selected_target"))
                unstub()

    def test_space_on_text_selection_does_not_advance(self) -> None:
        for interface in ("graph", "branch", "tags", "status"):
            for selection, mouse_target in (
                (self.label, None),
                (sublime.Region(11, 21), [11, 16]),
                (sublime.Region(11, 38), [11, 16])
            ):
                with self.subTest(interface=interface, selection=selection):
                    command, navigation, args, _ = self.prepare(interface, selection)
                    self.view.settings().set("git_savvy.mouse_selected_target", mouse_target)
                    command.run(None)
                    verify(self.view, times=0).run_command(navigation, *args)
                    self.assertEqual(list(self.view.sel()), [sublime.Region(selection.b)])
                    unstub()

    def test_keyboard_reselection_is_not_the_original_mouse_click(self) -> None:
        for interface in ("graph", "branch", "tags", "status"):
            with self.subTest(interface=interface):
                command, navigation, args, wanted = self.prepare(interface, self.label)
                listener = (
                    GsLogGraphMouseListener() if interface == "graph"
                    else GsDashboardMouseListener()
                )
                self.view.sel().clear()
                self.view.sel().add(sublime.Region(self.label.b))
                listener.on_selection_modified(self.view)
                self.view.sel().clear()
                self.view.sel().add(self.label)
                listener.on_selection_modified(self.view)
                command.run(None)
                self.assertEqual(self.view.settings().get("git_savvy.multi_selection"), [wanted])
                verify(self.view, times=0).run_command(navigation, *args)
                self.assertEqual(list(self.view.sel()), [sublime.Region(self.label.b)])
                unstub()

    def test_space_toggling_clicked_label_off_does_not_advance(self) -> None:
        for interface in ("graph", "branch", "tags", "status"):
            command, navigation, args, wanted = self.prepare(interface, self.label)
            self.view.settings().set("git_savvy.multi_selection", [wanted])
            command.run(None)
            self.assertEqual(self.view.settings().get("git_savvy.multi_selection"), [])
            verify(self.view, times=0).run_command(navigation, *args)
            unstub()

    def prepare(self, interface: str, selection: sublime.Region) -> tuple:
        settings = self.view.settings()
        settings.set("git_savvy.multi_selection", [])
        settings.set("git_savvy.interface", interface)
        for kind, flag in (
            ("graph", "log_graph"), ("branch", "branch"), ("tags", "tags"), ("status", "status")
        ):
            settings.set("git_savvy.{}_view".format(flag), interface == kind)
        self.view.sel().clear()
        self.view.sel().add(selection)
        settings.set("git_savvy.mouse_selected_target", [self.label.a, self.label.b])
        if interface == "graph":
            command = multi_selector.gs_log_graph_multiselect(self.view)
            navigation = "gs_log_graph_navigate"
            args = ({"natural_movement": True},)
            wanted = [2, 9]
        else:
            command = multi_selector.gs_dashboard_multiselect(self.view)
            navigation = {
                "branch": "gs_branches_navigate_branch",
                "tags": "gs_tags_navigate_tag",
                "status": "gs_status_navigate_file"
            }[interface]
            args = ()
            wanted = [self.label.a, self.label.b]
            when(self.view).find_by_selector(...).thenReturn([self.label])
        when(self.view).run_command(navigation, *args).thenReturn(None)
        return command, navigation, args, wanted


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
