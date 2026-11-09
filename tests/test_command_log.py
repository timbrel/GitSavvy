import heapq
import os
import shutil
import subprocess
import threading

from unittesting import DeferrableTestCase

from GitSavvy.core import command_log
from GitSavvy.core.commands.command_log import GsCommandLogEventListener
from GitSavvy.core.utils import STARTUPINFO
from GitSavvy.tests.mockito import unstub, verify, when


class TestCommandLog(DeferrableTestCase):
    def tearDown(self):
        reset_command_log()
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

    def test_derives_running_visibility_from_timestamps(self):
        command = command_record(1, "/repo", "git status", 10, None)

        self.assertEqual(command_log.render_commands([command], now=10.099), ("", []))
        text, annotations = command_log.render_commands([command], now=10.1)
        self.assertIn("  (D) [     ] $ git status", text)
        self.assertEqual(annotations, [])
        self.assertEqual(command_log.render_commands([command], now=10.099), ("", []))

    def test_completed_commands_appear_without_waiting_for_the_threshold(self):
        command = command_record(1, "/repo", "git status", 0, 0.047)

        text, _ = command_log.render_commands([command], now=0.047)

        self.assertIn("  (D) [ 47ms] $ git status", text)

    def test_state_changes_replace_records_without_changing_previous_renders(self):
        log = command_log.CommandLog()
        process = Process()
        log.start(1, "/repo", "git status", "D", process, 0)
        rendered = list(log.commands.values())
        self.assertIs(rendered[0], log.commands[1])

        log.finish(1, 2, aborted=True)
        finished = log.commands[1]
        self.assertIsNot(finished, rendered[0])
        self.assertTrue(rendered[0].is_running)
        self.assertFalse(rendered[0].aborted)
        self.assertIs(rendered[0].proc, process)
        self.assertFalse(finished.is_running)
        self.assertTrue(finished.aborted)
        self.assertIsNone(finished.proc)
        with self.assertRaises(AttributeError):
            finished.aborted = False

    def test_groups_by_working_dir_and_orders_newest_first(self):
        text, annotations = command_log.render_commands([
            command_record(1, "/older", "git status", 0, 0.063),
            command_record(2, "/newer", "git fetch", 0, 1.2, thread_name="W"),
            command_record(3, "/older", "git diff", 0, 12.6),
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
            command_record(1, "/repo", "git status", 1, None),
        ], now=1.999)

        self.assertIn("  (D) [     ] $ git status", text)

    def test_abort_annotation_appears_only_after_one_second(self):
        command = command_record(1, "/repo", "git status", 10, None)
        for elapsed, expected_ids in [(0.1, []), (0.2, []), (0.999, []), (1.0, [1]), (1.1, [1])]:
            with self.subTest(elapsed=elapsed):
                text, annotations = command_log.render_commands([command], now=10 + elapsed)
                prefix = "> (D)" if expected_ids else "  (D)"
                self.assertIn(prefix, text)
                self.assertEqual([annotation.execution_id for annotation in annotations], expected_ids)

    def test_formats_running_and_aborted_commands(self):
        text, annotations = command_log.render_commands([
            command_record(1, "/repo", "git status", 1, None, thread_name="W"),
            command_record(2, "/repo", "git stash list", 2, 2.2, aborted=True),
        ], now=2.5)

        self.assertEqual(
            text,
            "             -- /repo --\n"
            "  (D) [xxxxx] $ git stash list\n"
            "> (W) [ 1.5s] $ git status\n"
        )
        self.assertEqual([annotation.execution_id for annotation in annotations], [1])
        annotation = annotations[0]
        self.assertEqual(text[annotation.start], ">")

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
        verify(command_log.sublime, times=0).set_timeout(...)

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
            command_record(7, "/repo", "git status", 0, None),
            command_record(8, "/repo", "git status", 0, None),
        ], now=2)

        self.assertEqual([annotation.execution_id for annotation in annotations], [8, 7])

    def test_annotation_aborts_a_global_execution_id_from_any_panel(self):
        panel = Panel()
        when(command_log).abort_command(42)

        command_log._draw_abort_annotations(
            panel,
            [command_log.Annotation(42, 5)]
        )
        panel.on_navigate("42")

        verify(command_log).abort_command(42)
        self.assertEqual(panel.annotations, [
            '<a href="42" style="display: inline-block; padding: 1px 0.6em; '
            'color: #aaaa00; background-color: #00202b; text-decoration: none">Abort</a>'
        ])
        self.assertEqual(panel.regions[0].begin(), 5)
        self.assertTrue(panel.regions[0].empty())

    def test_annotation_chooses_readable_scope_color_without_a_fill(self):
        cases = [
            ("#ffaa00", "#00202b", "#00202b", "#ffffff", "#ffaa00"),
            ("#000000", "#aaaa00", "#00202b", "#ffffff", "#aaaa00"),
            ("#ffffff", "#000088", "#ffffff", "#000000", "#000088"),
            ("#00202b", "#00202b", "#00202b", "#ffffff", "#ffffff"),
            ("#000000", "#aaaa0000", "#00202b", "#ffffff", "#ffffff"),
        ]
        for foreground, background, panel_bg, panel_fg, expected in cases:
            with self.subTest(foreground=foreground, background=background):
                color = command_log._annotation_text_color(
                    {"foreground": foreground, "background": background},
                    {"foreground": panel_fg, "background": panel_bg}
                )
                self.assertEqual(color, expected)

    def test_timer_ticks_preserve_abort_annotations_and_command_text(self):
        log = command_log._commands
        log.start(1, "/répo", "git status", "D", Process(), 0)
        log.start(2, "/other", "git fetch", "W", Process(), 0)
        panel = Panel()
        window = Window(1, panel)
        window.visible = True
        when(command_log.sublime).windows().thenReturn([window])
        when(command_log.sublime).set_timeout(...)
        when(command_log).replace_view_content(...).thenAnswer(
            lambda panel, text, region=None: panel.replace_content(text, region)
        )
        when(command_log)._draw_abort_annotations(...)

        for now in (1.1, 1.2, 1.2):
            when(command_log.time).monotonic().thenReturn(now)
            command_log._render()
            expected, _ = command_log.render_commands(log.commands.values(), now)
            self.assertEqual(panel.text, expected)

        verify(command_log, times=1)._draw_abort_annotations(...)
        self.assertEqual(len(panel.writes), 3)  # Full render, then two timer digits.
        self.assertIsNone(panel.writes[0][1])
        for text, region in panel.writes[1:]:
            self.assertEqual(text, "2")
            self.assertEqual(region.size(), 1)

    def test_text_update_falls_back_when_line_count_or_width_changes(self):
        running = command_record(1, "/repo", "git fetch", 0, None)
        finished = command_record(2, "/other", "git status", 0, 0.047)
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


class TestCommandLogRendering(DeferrableTestCase):
    def setUp(self):
        reset_command_log()
        self.now = 0.0
        self.callbacks = []
        self.scheduled = 0
        self.paints = []
        self.window = Window(1, Panel())
        self.windows = [self.window]
        self.render = command_log.render_commands
        when(command_log.time).monotonic().thenAnswer(lambda: self.now)
        when(command_log.sublime).windows().thenAnswer(lambda: self.windows)
        when(command_log.sublime).set_timeout(...).thenAnswer(self.schedule)
        when(command_log).render_commands(...).thenAnswer(self.record_render)
        when(command_log).replace_view_content(...).thenAnswer(
            lambda panel, text, region=None: panel.replace_content(text, region)
        )
        # The status-bar spinner has its own lifecycle and tests.
        when(command_log.git_activity).start(...)
        when(command_log.git_activity).finish(...)

    def tearDown(self):
        reset_command_log()
        unstub()

    def test_hidden_commands_only_update_history(self):
        limit = command_log.FINISHED_COMMAND_LIMIT
        for _ in range(limit + 2):
            execution_id = command_log.start_command(".", "git status", Process())
            command_log.finish_command(execution_id, aborted=False)

        self.assertEqual(self.scheduled, 0)
        self.assertEqual(self.paints, [])
        verify(command_log.sublime, times=0).windows()
        self.open_panel()
        self.assertEqual(self.window.panel.text.count("$ git status"), limit)
        self.assertEqual(self.callbacks, [])

    def test_preparation_runs_once_without_opening_the_panel(self):
        cases = [
            ("missing", True, False, False),
            ("existing", True, True, False),
            ("closed", False, False, False),
            ("already visible", True, True, True),
        ]
        for window_id, (name, valid, exists, visible) in enumerate(cases, 1):
            with self.subTest(name=name):
                window = Window(window_id, Panel() if exists else None)
                window.visible = visible
                self.windows = [window]
                when(window).is_valid().thenReturn(valid)
                when(command_log).ensure_panel(window).thenAnswer(
                    lambda window: setattr(window, "panel", Panel())
                )
                scheduled, paints = self.scheduled, len(self.paints)

                command_log.prepare_panel(window)
                command_log.prepare_panel(window)
                self.advance_to(self.now)
                if valid:
                    command_log.prepare_panel(window)

                verify(command_log, times=int(valid and not exists)).ensure_panel(window)
                self.assertEqual(window.visible, visible)
                self.assertEqual(len(self.paints) - paints, int(visible))
                self.assertEqual(self.scheduled - scheduled, 1 + int(visible))

    def test_visible_panels_share_timestamp_driven_redraws(self):
        other_window = Window(2, Panel())
        self.windows.append(other_window)
        command_log.start_command(".", "git status", Process())
        self.advance_to(0.05)
        self.window.visible = other_window.visible = True
        command_log.panel_visibility_changed()
        command_log.panel_visibility_changed()
        self.advance_to(self.now)
        self.assertEqual(self.window.panel.text, "")
        self.assertEqual(other_window.panel.text, "")

        cases = [
            (0.099, 1, ""),
            (0.102, 2, "[     ]"),
            (0.999, 2, "[     ]"),
            (1.002, 3, "[ 1.0s]"),
            (1.103, 4, "[ 1.1s]"),
        ]
        for now, paints, timing in cases:
            with self.subTest(now=now):
                self.advance_to(now)
                self.assertEqual(len(self.paints), paints)
                self.assertEqual(len(self.callbacks), 1)
                self.assertEqual(self.window.panel.text, other_window.panel.text)
                self.assertIn(timing, self.window.panel.text)
        self.assertEqual(len(self.window.panel.annotations), 1)

    def test_earlier_paint_reschedules_a_new_commands_appearance(self):
        self.now = -0.02
        command_log.start_command(".", "git status", Process())
        self.now = 0.0
        self.open_panel()  # The first command will appear at 80ms.
        scheduled = self.scheduled

        command_log.start_command(".", "git diff", Process())
        self.assertEqual(self.scheduled, scheduled)
        self.advance_to(0.081)
        self.assertIn("git status", self.window.panel.text)
        self.assertNotIn("git diff", self.window.panel.text)
        self.assertEqual(len(self.paints), 2)

        self.advance_to(0.102)
        self.assertIn("git diff", self.window.panel.text)
        self.assertEqual(len(self.paints), 3)

    def test_superseded_callback_cannot_paint_at_a_reused_deadline(self):
        command_log.start_command(".", "git status", Process())
        self.open_panel()
        self.advance_to(0.2)

        command_log.start_command(".", "git diff", Process())
        self.advance_to(0.302)
        self.assertIn("git diff", self.window.panel.text)
        paints = len(self.paints)

        # Both the superseded callback and the new one target the 1s transition.
        self.advance_to(1.002)
        self.assertEqual(len(self.paints), paints + 1)

    def test_fast_completions_share_one_redraw_and_stop_scheduling(self):
        self.open_panel()
        first = command_log.start_command(".", "git status", Process())
        second = command_log.start_command(".", "git diff", Process())
        self.advance_to(0.047)

        command_log.finish_command(first, aborted=False)
        scheduled = self.scheduled
        command_log.finish_command(second, aborted=False)
        self.assertEqual(self.scheduled, scheduled)
        self.advance_to(self.now)

        self.assertEqual(len(self.paints), 2)
        self.assertIn("[ 47ms] $ git status", self.window.panel.text)
        self.assertIn("[ 47ms] $ git diff", self.window.panel.text)
        self.advance_to(2)
        self.assertEqual(len(self.paints), 2)
        self.assertEqual(self.callbacks, [])

    def test_start_during_a_paint_is_not_lost(self):
        started = []

        def replace_content(panel, text, region=None):
            panel.replace_content(text, region)
            if not started:
                started.append(command_log.start_command(".", "git status", Process()))

        when(command_log).replace_view_content(...).thenAnswer(replace_content)
        self.open_panel()
        self.assertEqual(self.window.panel.text, "")

        self.advance_to(0.102)
        self.assertIn("git status", self.window.panel.text)
        self.assertEqual(len(self.paints), 2)

    def test_hidden_panels_stop_redrawing_even_if_the_hide_event_is_missed(self):
        command_log.start_command(".", "git status", Process())
        listener = GsCommandLogEventListener()
        cases = [
            ("hide_panel", None),
            ("show_panel", {"panel": "output.other"}),
            ("show_panel", {"panel": "output." + command_log.PANEL_NAME, "toggle": True}),
            (None, None),
        ]
        for command, args in cases:
            with self.subTest(command=command, args=args):
                self.open_panel()
                paints = len(self.paints)
                self.window.visible = False
                if command:
                    listener.on_post_window_command(self.window, command, args)
                self.advance_to(self.now + 2)
                self.assertEqual(len(self.paints), paints)
                self.assertEqual(self.callbacks, [])

                scheduled = self.scheduled
                execution_id = command_log.start_command(".", "git diff", Process())
                command_log.finish_command(execution_id, aborted=False)
                self.assertEqual(self.scheduled, scheduled)

    def test_new_or_stale_panels_get_full_renders(self):
        command_log.start_command(".", "git status", Process())
        self.advance_to(1.1)
        self.open_panel()
        other_window = Window(2, Panel())

        for state in ("new", "reopened", "recreated"):
            with self.subTest(state=state):
                if state == "reopened":
                    other_window.visible = False
                    command_log.panel_visibility_changed()
                    stale_text = other_window.panel.text
                    self.advance_to(2.1)
                    self.assertEqual(other_window.panel.text, stale_text)
                    self.assertNotEqual(self.window.panel.text, stale_text)
                elif state == "recreated":
                    other_window.panel = Panel()
                existing_annotations = self.window.panel.annotations

                self.open_panel(other_window)

                self.assertEqual(other_window.panel.text, self.window.panel.text)
                self.assertEqual(other_window.panel.writes[-1], (self.window.panel.text, None))
                self.assertEqual(len(other_window.panel.annotations), 1)
                self.assertIs(self.window.panel.annotations, existing_annotations)

    def test_redraws_continue_until_the_last_window_closes(self):
        other_window = Window(2, Panel())
        self.open_panel()
        self.open_panel(other_window)
        command_log.start_command(".", "git status", Process())

        command_log.window_closed(1)
        self.windows.remove(self.window)
        self.advance_to(1.1)
        self.assertEqual(self.window.panel.text, "")
        self.assertIn("git status", other_window.panel.text)

        paints = len(self.paints)
        command_log.window_closed(2)
        self.windows.clear()
        scheduled = self.scheduled
        command_log.start_command(".", "git diff", Process())
        self.advance_to(2)
        self.assertEqual(self.scheduled, scheduled)
        self.assertEqual(len(self.paints), paints)
        self.assertEqual(self.callbacks, [])

    def open_panel(self, window=None):
        window = window or self.window
        if window not in self.windows:
            self.windows.append(window)
        window.visible = True
        command_log.panel_visibility_changed()
        self.advance_to(self.now)

    def schedule(self, callback, delay=0):
        self.scheduled += 1
        heapq.heappush(self.callbacks, (self.now + delay / 1000, self.scheduled, callback))

    def advance_to(self, now):
        while self.callbacks and self.callbacks[0][0] <= now:
            self.now, _, callback = heapq.heappop(self.callbacks)
            callback()
        self.now = now

    def record_render(self, commands, now):
        self.paints.append(now)
        return self.render(commands, now)


def reset_command_log():
    with command_log._lock:
        command_log._commands.commands.clear()
        command_log._has_visible_panel = False
        command_log._pending_render = None
        command_log._prepared_windows.clear()
    command_log._last_render = ("", [])
    command_log._rendered_panels.clear()


def command_record(
    execution_id,
    working_dir,
    command,
    started_at,
    finished_at,
    aborted=False,
    thread_name="D"
):
    return command_log.Command(
        execution_id,
        working_dir,
        command,
        thread_name,
        started_at,
        proc=None,
        finished_at=finished_at,
        aborted=aborted
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
        self.visible = False

    def id(self):
        return self.window_id

    def is_valid(self):
        return True

    def active_panel(self):
        return "output.{}".format(command_log.PANEL_NAME) if self.visible else None

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

    def style_for_scope(self, scope):
        return {"foreground": "#000000", "background": "#aaaa00"}

    def style(self):
        return {"foreground": "#ffffff", "background": "#00202b"}

    def add_regions(self, key, regions, **kwargs):
        self.key = key
        self.regions = regions
        self.annotations = kwargs["annotations"]
        self.on_navigate = kwargs["on_navigate"]

    def erase_regions(self, key):
        self.erased_key = key
