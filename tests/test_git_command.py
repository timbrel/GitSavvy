import io
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


class TestGitStdoutFormatter(DeferrableTestCase):
    def tearDown(self):
        unstub()

    def test_formats_stdout_in_the_panel_but_returns_it_raw(self):
        panel = object()
        logged = []
        when(git_command.output_panel).init_panel(...).thenReturn(panel)
        when(git_command.output_panel).append_to_panel(panel, ...).thenAnswer(
            lambda panel, message: logged.append(message)
        )
        when(git_command.output_panel).start_abortable_command(...)
        when(git_command.output_panel).finish_abortable_command(...)
        when(git_command.output_panel).append_done_message(...)
        when(git_command.subprocess).Popen(...).thenReturn(
            StreamingProcess(b"keep\ndrop\n", b"warning\n")
        )

        stdout = TimeoutGitCommand().git(
            "status",
            working_dir=".",
            show_panel=True,
            stdout_formatter=lambda: hide_drop_and_shout
        )

        self.assertEqual(stdout, "keep\ndrop\n")
        self.assertEqual(logged[0], "$ git status\n")
        self.assertEqual(sorted(logged[1:]), ["KEEP\n", "warning\n"])

    def test_formats_stdout_in_the_error_message(self):
        when(git_command.subprocess).Popen(...).thenReturn(
            FailingProcess(b"keep\ndrop\n", b"error\n")
        )

        with self.assertRaises(GitSavvyError) as cm:
            TimeoutGitCommand().git(
                "status",
                working_dir=".",
                show_panel_on_error=False,
                stdout_formatter=lambda: hide_drop_and_shout
            )

        self.assertEqual(cm.exception.message, "$ git status\n\nKEEP\nerror\n")
        self.assertEqual(cm.exception.stdout, "keep\ndrop\n")


def hide_drop_and_shout(line):
    return "" if line == "drop\n" else line.upper()


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


class StreamingProcess:
    returncode = 0

    def __init__(self, stdout, stderr):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FailingProcess:
    returncode = 1

    def __init__(self, stdout, stderr):
        self.output = stdout, stderr

    def communicate(self, stdin=None, timeout=None):
        return self.output
