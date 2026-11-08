import os
import shutil
import subprocess
import threading

from unittesting import DeferrableTestCase

from GitSavvy.core import command_log
from GitSavvy.core.utils import STARTUPINFO
from GitSavvy.tests.mockito import ANY, unstub, verify, when


class TestCommandLog(DeferrableTestCase):
    def tearDown(self):
        with command_log._lock:
            command_log._commands.commands.clear()
        command_log._render_due_at.clear()
        command_log._rendered_panels.clear()
        unstub()

    def test_keeps_all_running_and_the_most_recently_finished(self):
        limit = command_log.FINISHED_COMMAND_LIMIT
        log = command_log.CommandLog()
        for execution_id in range(1, limit + 3):
            working_dir = "/repo-a" if execution_id % 2 else "/repo-b"
            log.start(
                execution_id,
                working_dir,
                f"git command-{execution_id}",
                "D",
                Process(),
                execution_id
            )

        for execution_id in range(1, limit + 2):
            log.finish(execution_id, 100 + execution_id, aborted=False)

        self.assertEqual(set(log.commands), set(range(2, limit + 3)))
        self.assertTrue(log.commands[limit + 2].is_running)

    def test_retention_uses_completion_order_not_start_order(self):
        limit = command_log.FINISHED_COMMAND_LIMIT
        log = command_log.CommandLog()
        for execution_id in range(1, limit + 2):
            log.start(execution_id, "/repo", "git status", "D", Process(), execution_id)

        for execution_id in range(2, limit + 2):
            log.finish(execution_id, execution_id, aborted=False)
        log.finish(1, limit + 2, aborted=False)

        self.assertIn(1, log.commands)
        self.assertNotIn(2, log.commands)

    def test_delays_running_commands_but_always_shows_completed_commands(self):
        log = command_log.CommandLog()
        log.start(1, "/repo", "git status", "D", Process(), 0)

        self.assertEqual(log.snapshots_for_render(), [])
        self.assertTrue(log.reveal(1))
        self.assertEqual([command.execution_id for command in log.snapshots_for_render()], [1])

        log.start(2, "/repo", "git diff", "D", Process(), 0)
        log.finish(2, 0.1, aborted=False)
        self.assertEqual(
            [command.execution_id for command in log.snapshots_for_render()],
            [1, 2]
        )

    def test_groups_by_working_dir_and_orders_newest_first(self):
        text, annotations = command_log.render_commands([
            snapshot(1, "/older", "git status", 0, 0.063),
            snapshot(2, "/newer", "git fetch", 0, 1.2, thread_name="W"),
            snapshot(3, "/older", "git diff", 0, 12.6),
        ], now=20)

        self.assertEqual(
            text,
            "             -- /older --\n"
            "  (D) [  13s] $ git diff\n"
            "  (D) [ 63ms] $ git status\n"
            "\n"
            "             -- /newer --\n"
            "  (W) [ 1.2s] $ git fetch\n"
        )
        self.assertEqual(annotations, [])

    def test_running_timing_is_blank_until_one_second(self):
        text, _ = command_log.render_commands([
            snapshot(1, "/repo", "git status", 1, None),
        ], now=1.999)

        self.assertIn("> (D) [     ] $ git status", text)

    def test_formats_running_and_aborted_commands(self):
        text, annotations = command_log.render_commands([
            snapshot(1, "/repo", "git status", 1, None, thread_name="W"),
            snapshot(2, "/repo", "git stash list", 2, 2.2, aborted=True),
        ], now=2.5)

        self.assertEqual(
            text,
            "             -- /repo --\n"
            "  (D) [xxxxx] $ git stash list\n"
            "> (W) [ 1.5s] $ git status\n"
        )
        self.assertEqual([annotation.execution_id for annotation in annotations], [1])

    def test_selected_process_reports_activity_until_completion(self):
        when(command_log.sublime).set_timeout(...)
        when(command_log.git_activity).start(...)
        when(command_log.git_activity).finish(...)

        execution_id = command_log.start_command(
            ".", "git status", Process(), show_spinner=True
        )
        command_log.finish_command(execution_id, aborted=True)

        verify(command_log.git_activity).start(execution_id)
        verify(command_log.git_activity).finish(execution_id)

    def test_start_captures_the_calling_thread_name(self):
        when(command_log.sublime).set_timeout(...)

        execution_id = command_log.start_command(".", "git status", Process())

        command = command_log._commands.commands[execution_id]
        self.assertEqual(command.thread_name, threading.current_thread().name[:1])
        verify(command_log.sublime).set_timeout(ANY(), 100)

    def test_handoff_waiter_preserves_process_identity_and_output(self):
        proc = waiting_git_process()
        execution_id = command_log.start_command(
            os.getcwd(), "git hash-object --stdin", proc
        )
        command_log.finish_when_process_exits(execution_id, proc)

        self.assertIs(command_log._commands.commands[execution_id].proc, proc)
        stdout, _ = proc.communicate(b"command log observer test\n")
        self.assertEqual(len(stdout.strip()), 40)
        yield lambda: not command_log._commands.commands[execution_id].is_running

    def test_handoff_waiter_records_abort_without_stealing_pipes(self):
        proc = waiting_git_process()
        execution_id = command_log.start_command(
            os.getcwd(), "git hash-object --stdin", proc
        )
        command_log.finish_when_process_exits(execution_id, proc)

        command_log.abort_command(execution_id)
        yield lambda: not command_log._commands.commands[execution_id].is_running

        self.assertTrue(command_log._commands.commands[execution_id].aborted)
        proc.communicate()

    def test_identical_running_commands_keep_distinct_abort_ids(self):
        _, annotations = command_log.render_commands([
            snapshot(7, "/repo", "git status", 0, None),
            snapshot(8, "/repo", "git status", 0, None),
        ], now=2)

        self.assertEqual([annotation.execution_id for annotation in annotations], [8, 7])

    def test_annotation_aborts_a_global_execution_id_from_any_panel(self):
        panel = Panel()
        when(command_log).abort_command(42)

        command_log._draw_abort_annotations(
            panel,
            [command_log.Annotation(42, 5, 20)]
        )
        panel.on_navigate("42")

        verify(command_log).abort_command(42)
        self.assertEqual(panel.annotations, ['<a href="42">Abort</a>'])

    def test_prepares_a_missing_panel_without_showing_it(self):
        window = Window(1, None)
        when(command_log).enqueue_on_ui(...).thenAnswer(lambda callback: callback())
        when(command_log).ensure_panel(window)
        when(command_log).show(...)

        command_log.prepare_panel(window)

        verify(command_log).ensure_panel(window)
        verify(command_log, times=0).show(...)

    def test_preparing_an_existing_panel_does_nothing(self):
        window = Window(1, Panel())
        when(command_log).enqueue_on_ui(...).thenAnswer(lambda callback: callback())
        when(command_log).ensure_panel(...)

        command_log.prepare_panel(window)

        verify(command_log, times=0).ensure_panel(...)

    def test_queued_preparation_ignores_a_closed_window(self):
        window = Window(1, None)
        when(window).is_valid().thenReturn(False)
        when(command_log).enqueue_on_ui(...).thenAnswer(lambda callback: callback())
        when(command_log).ensure_panel(...)

        command_log.prepare_panel(window)

        verify(command_log, times=0).ensure_panel(...)

    def test_each_visible_window_receives_global_updates(self):
        first_panel = Panel()
        second_panel = Panel()
        first_window = Window(1, first_panel)
        second_window = Window(2, second_panel)
        when(command_log.sublime).windows().thenReturn([first_window, second_window])
        when(command_log)._render(...)

        command_log._render_visible_panels()

        verify(command_log)._render(1, first_panel)
        verify(command_log)._render(2, second_panel)

    def test_each_panel_renders_the_same_application_history(self):
        log = command_log._commands
        log.start(1, "/repo", "git status", "D", Process(), 0)
        log.finish(1, 0.047, aborted=False)
        first_panel = Panel()
        second_panel = Panel()
        rendered = []
        when(command_log).replace_view_content(...).thenAnswer(
            lambda panel, text: rendered.append((panel, text))
        )

        command_log._render(1, first_panel)
        command_log._render(2, second_panel)

        self.assertEqual(rendered[0][1], rendered[1][1])
        self.assertIn("  (D) [ 47ms] $ git status", rendered[0][1])

    def test_timer_ticks_preserve_abort_annotations_and_command_text(self):
        log = command_log._commands
        log.start(1, "/répo", "git status", "D", Process(), 0)
        log.start(2, "/other", "git fetch", "W", Process(), 0)
        log.reveal_all_running()
        panel = Panel()
        when(command_log.time).monotonic().thenReturn(1.1, 1.2, 1.2)
        when(command_log.sublime).set_timeout(...)
        when(command_log).replace_view_content(...).thenAnswer(
            lambda panel, text, region=None: panel.replace_content(text, region)
        )
        when(command_log)._draw_abort_annotations(...)

        for now in (1.1, 1.2, 1.2):
            command_log._render(1, panel)
            expected, _ = command_log.render_commands(log.snapshots_for_render(), now)
            self.assertEqual(panel.text, expected)

        verify(command_log, times=1)._draw_abort_annotations(...)
        self.assertEqual(len(panel.writes), 3)  # Full render, then two timer digits.
        self.assertIsNone(panel.writes[0][1])
        for text, region in panel.writes[1:]:
            self.assertEqual(text, "2")
            self.assertEqual(region.size(), 1)

    def test_text_update_falls_back_when_line_count_or_width_changes(self):
        running = snapshot(1, "/repo", "git fetch", 0, None)
        finished = snapshot(2, "/other", "git status", 0, 0.047)
        cases = [
            ([running], 9999, [running], 10000),
            ([running], 2, [running, finished], 2),
            ([running, finished], 2, [running], 2),
        ]
        when(command_log).replace_view_content(...).thenAnswer(
            lambda panel, text, region=None: panel.replace_content(text, region)
        )
        for before, before_time, after, after_time in cases:
            with self.subTest(before_time=before_time, after_count=len(after)):
                panel = Panel()
                panel.text, _ = command_log.render_commands(before, before_time)
                expected, _ = command_log.render_commands(after, after_time)

                preserved = command_log._update_panel_text(panel, panel.text, expected)

                self.assertFalse(preserved)
                self.assertEqual(panel.text, expected)
                self.assertEqual(panel.writes, [(expected, None)])

    def test_blank_phase_schedules_only_the_one_second_transition(self):
        command = snapshot(1, "/repo", "git status", 0, None)
        when(command_log.sublime).set_timeout(...)

        command_log._schedule_next_render(17, [command], 0.1)
        command_log._schedule_next_render(17, [command], 0.5)

        verify(command_log.sublime, times=1).set_timeout(...)
        self.assertEqual(command_log._render_due_at[17], 1.0)

    def test_finishing_cancels_a_pending_blank_phase_render(self):
        command_log._render_due_at[17] = 1.0

        command_log._schedule_next_render(17, [], 0.2)
        command_log._animate(17, 1.0)

        self.assertNotIn(17, command_log._render_due_at)

    def test_closing_a_window_preserves_global_command_records(self):
        command_log._commands.start(1, "/repo", "git status", "D", Process(), 0)
        command_log._render_due_at[17] = 1.0
        command_log._rendered_panels[17] = (Panel(), "old render", [])

        command_log.window_closed(17)

        self.assertIn(1, command_log._commands.commands)
        self.assertNotIn(17, command_log._render_due_at)
        self.assertNotIn(17, command_log._rendered_panels)


def snapshot(
    execution_id,
    working_dir,
    command,
    started_at,
    finished_at,
    aborted=False,
    thread_name="D"
):
    return command_log.CommandSnapshot(
        execution_id,
        working_dir,
        command,
        thread_name,
        started_at,
        finished_at,
        aborted
    )


def waiting_git_process():
    git_path = shutil.which("git")
    if not git_path:
        raise RuntimeError("git is required for command-log process tests")

    return subprocess.Popen(
        [git_path, "hash-object", "--stdin"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        startupinfo=STARTUPINFO,
        start_new_session=os.name != "nt"
    )


class Process:
    pass


class Window:
    def __init__(self, window_id, panel):
        self.window_id = window_id
        self.panel = panel

    def id(self):
        return self.window_id

    def is_valid(self):
        return True

    def active_panel(self):
        return "output.{}".format(command_log.PANEL_NAME)

    def find_output_panel(self, name):
        return self.panel


class Panel:
    def __init__(self):
        self.text = ""
        self.writes = []

    def replace_content(self, text, region=None):
        self.writes.append((text, region))
        if region is None:
            self.text = text
        else:
            self.text = self.text[:region.begin()] + text + self.text[region.end():]

    def add_regions(self, key, regions, **kwargs):
        self.key = key
        self.regions = regions
        self.annotations = kwargs["annotations"]
        self.on_navigate = kwargs["on_navigate"]

    def erase_regions(self, key):
        self.erased_key = key
