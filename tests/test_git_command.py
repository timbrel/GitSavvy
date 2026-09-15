import subprocess

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import unstub, when

from GitSavvy.core import git_command
from GitSavvy.core.exceptions import GitSavvyError


class TestGitTimeout(DeferrableTestCase):
    def tearDown(self):
        unstub()

    def test_kills_and_reaps_the_process(self):
        process = TimedOutProcess()
        when(git_command.subprocess).Popen(...).thenReturn(process)
        when(git_command).try_kill_proc(process).thenAnswer(
            lambda process: setattr(process, "got_killed", True)
        )

        with self.assertRaises(GitSavvyError):
            TimeoutGitCommand().git(
                "status",
                working_dir=".",
                timeout=0.2,
                show_panel_on_error=False
            )

        self.assertTrue(process.got_killed)
        self.assertEqual(process.communicate_calls, [0.2, None])


class TimeoutGitCommand(git_command.mixin_base):
    project_settings = {}
    app_settings = {"show_panel_for": []}
    git_binary_path = "git"

    def some_window(self):
        return TimeoutWindow()


class TimeoutWindow:
    def extract_variables(self):
        return {}


class TimedOutProcess:
    def __init__(self):
        self.communicate_calls = []
        self.got_killed = False

    def communicate(self, stdin=None, timeout=None):
        self.communicate_calls.append(timeout)
        if len(self.communicate_calls) == 1:
            raise subprocess.TimeoutExpired("git status", timeout)
        return b"", b""
