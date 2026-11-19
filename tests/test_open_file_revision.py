import os
from threading import current_thread

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.core.interfaces import show_file_at_commit as module
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.test_git.common import GitRepoTestCase


class TestOpenFileInRepository(GitRepoTestCase):
    def test_opens_latest_revision_without_a_waiting_view(self):
        self.window.run_command("gs_show_file_at_commit", {"filepath": os.path.join(self._temp_dir, "README.md")})
        view = self.window.active_view()
        self.assertEqual(view.substr(sublime.Region(0, view.size())), "README")


class TestOpenFileRevision(DeferrableTestCase):
    def test_constructor_loads_historical_path_off_main_thread(self):
        window = sublime.active_window()
        source = window.new_file()
        source.set_scratch(True)
        self.addCleanup(source.close)
        self.addCleanup(unstub)
        source.settings().set("git_savvy.repo_path", sublime.packages_path())
        path = "/repo/current.py"
        threads = []

        def resolve(*args):
            threads.append(current_thread())
            return "/repo/older.py"

        when(module.GitCommand).to_short_hash("123abcd").thenReturn("123abcd")
        when(module.GitCommand).filename_at_head(path, "123abcd").thenReturn(path)
        when(module.GitCommand).filename_at_commit(path, "123abcd").thenAnswer(resolve)
        when(module.GitCommand).get_file_content_at_commit("/repo/older.py", "123abcd").thenReturn("old text")
        when(module).enqueue_on_worker(...)

        window.run_command("gs_show_file_at_commit", {"filepath": path, "commit_hash": "123abcd"})

        result = window.active_view()
        self.addCleanup(result.close)
        self.assertEqual(result.substr(sublime.Region(0, result.size())), "old text")
        self.assertEqual(len(threads), 1)
        self.assertIsNot(threads[0], current_thread())
