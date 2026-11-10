import io
import subprocess

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import unstub, verify, when

from GitSavvy.core import git_command
from GitSavvy.core.exceptions import GitSavvyError


class TestGitTimeout(DeferrableTestCase):
    def tearDown(self):
        unstub()

    def test_timeout_policy(self):
        cases = [
            # panel, panel setting, thread default, explicit timeout, resolved timeout, spinner
            (False, [], "unset", "unset", None, True),
            (True, [], "unset", "unset", None, False),
            (None, ["status"], "unset", "unset", None, False),
            (False, ["status"], "unset", "unset", None, True),
            (False, [], None, "unset", None, False),
            (True, [], 10.0, "unset", None, False),
            (False, [], 10.0, "unset", 10.0, False),
            (True, [], None, 0.2, 0.2, False),
            (None, ["status"], "unset", 0.2, 0.2, False),
            (False, [], "unset", None, None, False),
            (False, [], "unset", 0.2, 0.2, False),
        ]
        previous = getattr(git_command.auto_timeout, "value", "unset")
        try:
            for show_panel, settings, thread_default, explicit, expected, spinner in cases:
                with self.subTest(case=(show_panel, settings, thread_default, explicit)):
                    if thread_default == "unset":
                        if hasattr(git_command.auto_timeout, "value"):
                            del git_command.auto_timeout.value
                    else:
                        git_command.auto_timeout.value = thread_default
                    self.assert_timeout(show_panel, settings, explicit, expected, spinner)
        finally:
            if previous == "unset":
                if hasattr(git_command.auto_timeout, "value"):
                    del git_command.auto_timeout.value
            else:
                git_command.auto_timeout.value = previous

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

    def assert_timeout(self, show_panel, settings, explicit, expected, spinner):
        process = FailingProcess(b"", b"")
        process.returncode = 0
        observed = []

        def communicate(stdin=None, timeout=None):
            observed.append(timeout)
            return b"", b""

        process.communicate = communicate
        when(git_command.subprocess).Popen(...).thenReturn(process)
        when(git_command.output_panel).init_panel(...).thenReturn(object())
        when(git_command.output_panel).append_to_panel(...)
        when(git_command.output_panel).start_abortable_command(...)
        when(git_command.output_panel).finish_abortable_command(...)
        when(git_command.output_panel).append_done_message(...)
        when(git_command).communicate_and_log(...).thenAnswer(
            lambda proc, stdin, log, timeout, log_stdout: communicate(stdin, timeout)
        )
        when(git_command).it_runs_on_worker().thenReturn(True)
        when(git_command.command_log).start_command(...).thenReturn(42)
        when(git_command.command_log).finish_command(...)
        command = TimeoutGitCommand()
        command.app_settings = {"show_panel_for": settings}
        kwargs = {} if explicit == "unset" else {"timeout": explicit}
        command.git("status", working_dir=".", show_panel=show_panel, **kwargs)
        self.assertEqual(observed, [expected])
        verify(git_command.command_log).start_command(
            ".", "git status", process, show_spinner=spinner
        )
        unstub()


class TestCommandLogTracking(DeferrableTestCase):
    def tearDown(self):
        unstub()

    def test_tracks_the_managed_process_lifetime(self):
        process = FailingProcess(b"", b"")
        process.returncode = 0
        when(git_command.subprocess).Popen(...).thenReturn(process)
        when(git_command.command_log).start_command(...).thenReturn(42)
        when(git_command.command_log).finish_command(...)

        TimeoutGitCommand().git("status", working_dir=".")

        verify(git_command.command_log).start_command(...)
        verify(git_command.command_log).finish_command(42, False)

    def test_calls_outside_worker_do_not_request_a_spinner(self):
        process = FailingProcess(b"", b"")
        process.returncode = 0
        when(git_command).it_runs_on_worker().thenReturn(False)
        when(git_command.subprocess).Popen(...).thenReturn(process)
        when(git_command.command_log).start_command(...).thenReturn(42)
        when(git_command.command_log).finish_command(...)

        TimeoutGitCommand().git("status", working_dir=".")

        verify(git_command.command_log).start_command(
            ".", "git status", process, show_spinner=False
        )

    def test_tracks_just_the_proc_handoffs_with_a_waiter(self):
        process = FailingProcess(b"", b"")
        when(git_command.subprocess).Popen(...).thenReturn(process)
        when(git_command.command_log).finish_command(...)
        when(git_command.command_log).finish_when_process_exits(...)
        when(git_command.command_log).start_command(...).thenReturn(42)

        returned = TimeoutGitCommand().git("status", working_dir=".", just_the_proc=True)

        self.assertIs(returned, process)
        verify(git_command.command_log).start_command(...)
        verify(git_command.command_log).finish_when_process_exits(42, process)
        verify(git_command.command_log, times=0).finish_command(...)

    def test_git_streaming_registers_the_process_once(self):
        process = StreamingProcess(b"line\n", b"")
        when(git_command.subprocess).Popen(...).thenReturn(process)
        when(git_command.command_log).start_command(...).thenReturn(42)
        when(git_command.command_log).finish_when_process_exits(...)

        self.assertEqual(
            list(TimeoutGitCommand().git_streaming("status", working_dir=".")),
            ["line\n"]
        )

        verify(git_command.command_log, times=1).start_command(...)
        verify(git_command.command_log, times=1).finish_when_process_exits(42, process)
        verify(git_command.command_log, times=0).finish_command(...)


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
    repo_path = "."

    def some_window(self):
        return TimeoutWindow()


class TimeoutWindow:
    def is_valid(self):
        return False

    def id(self):
        return 999_998

    def extract_variables(self):
        return {}

    def active_panel(self):
        return None


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
