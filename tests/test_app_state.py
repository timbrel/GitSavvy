import builtins
import json
import os
import tempfile

from unittesting import DeferrableTestCase

from GitSavvy import git_savvy
from GitSavvy.common import ui
from GitSavvy.core import app_state, store
from GitSavvy.core.interfaces import branch, tags
from GitSavvy.tests.mockito import mock, unstub, verify, when


class TestAppState(DeferrableTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_state = app_state._state
        self.old_save_scheduled = app_state._save_scheduled
        self.old_state_path = app_state._state_path
        self.old_sublime = app_state.sublime

        app_state._state = {}
        app_state._save_scheduled = False
        app_state._state_path = lambda: os.path.join(self.temp_dir.name, "state.json")
        app_state.sublime = mock()

    def tearDown(self):
        app_state._state = self.old_state
        app_state._save_scheduled = self.old_save_scheduled
        app_state._state_path = self.old_state_path
        app_state.sublime = self.old_sublime
        self.temp_dir.cleanup()
        unstub()

    def test_get_and_set(self):
        self.assertIsNone(app_state.get("theme"))
        self.assertEqual(app_state.get("theme", "default"), "default")

        app_state.set("theme", "dark")

        self.assertEqual(app_state.get("theme"), "dark")

    def test_schedules_at_most_one_save_per_second(self):
        app_state.set("one", 1)
        app_state.set("two", 2)

        verify(app_state.sublime, times=1).set_timeout_async(
            app_state.save,
            app_state.SAVE_DELAY
        )

    def test_round_trips_state_through_disk(self):
        app_state.set("name", "José")
        app_state.save()

        with open(app_state._state_path(), encoding="utf-8") as file:
            self.assertEqual(json.load(file), {"name": "José"})
        self.assertEqual(app_state._load(), {"name": "José"})

    def test_ignores_a_non_object_state_file(self):
        os.makedirs(os.path.dirname(app_state._state_path()), exist_ok=True)
        with open(app_state._state_path(), "w", encoding="utf-8") as file:
            json.dump(["not", "an", "object"], file)

        self.assertEqual(app_state._load(), {})

    def test_branch_dashboard_uses_global_app_state(self):
        app_state._state[branch.SHOW_REMOTES_KEY] = True
        interface = branch.BranchInterface.__new__(branch.BranchInterface)

        state = interface.initial_state()

        self.assertIs(state["show_remotes"], True)

    def test_tags_dashboard_uses_its_own_global_app_state(self):
        app_state._state[tags.SHOW_REMOTES_KEY] = True
        interface = tags.TagsInterface.__new__(tags.TagsInterface)

        state = interface.initial_state()

        self.assertIs(state["show_remotes"], True)

    def test_dashboard_toggles_update_global_app_state(self):
        branch_interface = mock({"state": {"show_remotes": False}})
        branch_command = object.__new__(branch.gs_branches_toggle_remotes)
        branch_command.interface = branch_interface

        branch_command.run(None)

        self.assertIs(app_state.get(branch.SHOW_REMOTES_KEY), True)

        tags_interface = mock({"state": {"show_remotes": False}})
        tags_command = object.__new__(tags.gs_tags_toggle_remotes)
        tags_command.interface = tags_interface

        tags_command.run(None)

        self.assertIs(app_state.get(tags.SHOW_REMOTES_KEY), True)

    def test_help_toggle_updates_global_app_state(self):
        settings = mock()
        when(settings).get("git_savvy.help_hidden").thenReturn(False)
        view = mock()
        when(view).settings().thenReturn(settings)
        command = object.__new__(ui.gs_interface_toggle_help)
        command.view = view

        command.run(None)

        self.assertIs(app_state.get(ui.HIDE_HELP_MENU_KEY), True)


class TestUnusedGlobalSettingsWarning(DeferrableTestCase):
    def tearDown(self):
        unstub()

    def test_warns_for_each_former_setting_still_present(self):
        settings = mock()
        when(settings).has(...).thenAnswer(lambda key: key in {
            "hide_help_menu",
            "show_remotes_in_tags_dashboard"
        })
        when(builtins).print(...)

        git_savvy.warn_about_unused_global_settings(settings)

        verify(builtins).print(
            'GitSavvy: The "hide_help_menu" setting is no longer used. '
            "GitSavvy now remembers this preference automatically; "
            "you may remove it from your settings file."
        )
        verify(builtins).print(
            'GitSavvy: The "show_remotes_in_tags_dashboard" setting is no longer used. '
            "GitSavvy now remembers this preference automatically; "
            "you may remove it from your settings file."
        )


class TestPersistentRepoState(DeferrableTestCase):
    def setUp(self):
        self.repo_path = "/test/repo-for-persistent-state"
        self.old_app_state = app_state._state
        self.old_save_scheduled = app_state._save_scheduled
        self.old_sublime = app_state.sublime

        app_state._state = {}
        app_state._save_scheduled = False
        app_state.sublime = mock()
        store.state.pop(self.repo_path, None)

    def tearDown(self):
        store.state.pop(self.repo_path, None)
        app_state._state = self.old_app_state
        app_state._save_scheduled = self.old_save_scheduled
        app_state.sublime = self.old_sublime
        unstub()

    def test_only_persists_selected_repo_state(self):
        store.update_state(self.repo_path, {
            "ahead_behind_consecutive_failures": 2,
            "ahead_behind_retry_at": 123.5,
            "last_commit_graph_write": 120.0,
            "last_remote_used": "origin",
            "last_remote_used_for_push": "fork",
            "short_hash_length": 10,
        })

        self.assertEqual(app_state.get(store.PERSISTED_STATE_KEY), {
            self.repo_path: {
                "ahead_behind_consecutive_failures": 2,
                "ahead_behind_retry_at": 123.5,
                "last_commit_graph_write": 120.0,
                "last_remote_used": "origin",
                "last_remote_used_for_push": "fork",
            }
        })

    def test_restores_persistent_repo_state(self):
        app_state._state = {
            store.PERSISTED_STATE_KEY: {
                self.repo_path: {
                    "last_branch_used_to_pull_from": "main",
                    "short_hash_length": 10,
                }
            }
        }

        store._restore_state()

        self.assertEqual(
            store.current_state(self.repo_path).get("last_branch_used_to_pull_from"),
            "main"
        )
        self.assertNotIn("short_hash_length", store.current_state(self.repo_path))
