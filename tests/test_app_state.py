import json
import os
import tempfile
import unittest
from unittest.mock import Mock

from GitSavvy.core import app_state, store


class TestAppState(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_state = app_state._state
        self.old_save_scheduled = app_state._save_scheduled
        self.old_state_path = app_state._state_path
        self.old_sublime = app_state.sublime

        app_state._state = {}
        app_state._save_scheduled = False
        app_state._state_path = lambda: os.path.join(self.temp_dir.name, "state.json")
        app_state.sublime = Mock()

    def tearDown(self):
        app_state._state = self.old_state
        app_state._save_scheduled = self.old_save_scheduled
        app_state._state_path = self.old_state_path
        app_state.sublime = self.old_sublime
        self.temp_dir.cleanup()

    def test_get_and_set(self):
        self.assertIsNone(app_state.get("theme"))
        self.assertEqual(app_state.get("theme", "default"), "default")

        app_state.set("theme", "dark")

        self.assertEqual(app_state.get("theme"), "dark")

    def test_schedules_at_most_one_save_per_second(self):
        app_state.set("one", 1)
        app_state.set("two", 2)

        app_state.sublime.set_timeout_async.assert_called_once_with(
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


class TestPersistentRepoState(unittest.TestCase):
    def setUp(self):
        self.repo_path = "/test/repo-for-persistent-state"
        self.old_app_state = app_state._state
        self.old_save_scheduled = app_state._save_scheduled
        self.old_sublime = app_state.sublime

        app_state._state = {}
        app_state._save_scheduled = False
        app_state.sublime = Mock()
        store.state.pop(self.repo_path, None)

    def tearDown(self):
        store.state.pop(self.repo_path, None)
        app_state._state = self.old_app_state
        app_state._save_scheduled = self.old_save_scheduled
        app_state.sublime = self.old_sublime

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
