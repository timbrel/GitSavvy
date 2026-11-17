"""Real descendants must not outlive a cat-file reader, even after parent exit."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import ast
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from textwrap import dedent
import time
from unittest import TestCase

from GitSavvy.core import cat_file
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p


class TestCatFileDescendants(TestCase):
    def setUp(self) -> None:
        cat_file.shutdown()
        cat_file.start()
        self.temp = tempfile.TemporaryDirectory(prefix="gitsavvy-cat-file-descendant-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(cat_file.start)
        self.addCleanup(cat_file.shutdown)
        self.addCleanup(unstub)
        self.python = shutil.which("python3") or shutil.which("python")
        self.assertIsNotNone(self.python)
        self.python = subprocess.check_output([
            self.python, "-c", "import sys; print(getattr(sys, '_base_executable', sys.executable))"
        ]).decode().strip()

    @p.expand([(stdout, action) for stdout in (True, False) for action in ("timeout", "shutdown")])
    def test_parent_exit_with_inherited_pipes(self, stdout: bool, action: str) -> None:
        unrelated = subprocess.Popen([self.python, "-c", "import time; time.sleep(60)"])
        self.addCleanup(self.reap, unrelated)
        reader, futures, descendant = self.exited_parent(stdout)
        started = time.monotonic()
        if action == "shutdown":
            cat_file._readers[self.temp.name] = reader
            cat_file.shutdown()
        else:
            with self.assertRaises(cat_file.ReaderError):
                reader.result(futures[0])
        for future in futures:
            self.assertTrue(future.done(), "caller settlement must not depend on blocked pipe cleanup")
            with self.assertRaises(cat_file.ReaderError):
                future.result()
        reader.join()
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(reader.thread.is_alive())
        self.assertFalse(reader.err_thread.is_alive())
        self.assertTrue(all(pipe.closed for pipe in (reader.proc.stdin, reader.proc.stdout, reader.proc.stderr)))
        self.assertTrue(descendant.exited(), "job/group must terminate the surviving descendant")
        self.assertIsNone(unrelated.poll(), "unrelated processes must never be terminated")

    @p.expand([(True,), (False,)])
    def test_next_request_replaces_exited_parent_with_surviving_descendant(self, stdout: bool) -> None:
        binary = shutil.which("git")
        subprocess.check_output([binary, "init", "--ref-format=reftable", self.temp.name], stderr=subprocess.PIPE)
        subprocess.check_output([
            binary, "-C", self.temp.name, "-c", "user.name=Test", "-c", "user.email=test@example.com",
            "commit", "--allow-empty", "-m", "first"
        ], stderr=subprocess.PIPE)
        context = Context(self.temp.name, binary)
        expected = cat_file.read_object_metadata(context, ["HEAD"])
        key = os.path.normcase(os.path.realpath(self.temp.name))
        previous = cat_file._readers[key]
        previous.stop()
        previous.join()
        reader, futures, descendant = self.exited_parent(stdout, identity=previous.identity)
        cat_file._readers[key] = reader
        self.assertEqual(expected, cat_file.read_object_metadata(context, ["HEAD"]))
        reader.join()
        self.assertTrue(all(future.done() for future in futures))
        self.assertTrue(descendant.exited())
        self.assertFalse(reader.err_thread.is_alive())
        self.assertNotEqual(reader.proc.pid, cat_file._readers[key].proc.pid)

    def test_callers_settle_before_process_cleanup(self) -> None:
        reader, futures, descendant = self.exited_parent(True)
        entered, release = threading.Event(), threading.Event()
        original = reader.kill

        def delayed_cleanup():
            entered.set()
            if not release.wait(2):
                raise AssertionError("cleanup test was not released")
            original()

        when(reader).kill().thenAnswer(delayed_cleanup)
        with ThreadPoolExecutor(max_workers=1) as pool:
            stopped = pool.submit(reader.stop)
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(all(future.done() for future in futures))
                self.assertFalse(descendant.exited())
            finally:
                release.set()
            stopped.result(timeout=3)
        reader.join()
        self.assertTrue(descendant.exited())

    def test_shutdown_attempts_every_reader_on_cleanup_error(self) -> None:
        first, _, first_child = self.exited_parent(True, name="first")
        second, _, second_child = self.exited_parent(True, name="second")
        cat_file._readers.update(first=first, second=second)
        original_join = first.join

        def failed_join():
            original_join()
            raise cat_file.ReaderError("injected cleanup report")

        when(first).join().thenAnswer(failed_join)
        with self.assertRaisesRegex(cat_file.ReaderError, "injected cleanup report"):
            cat_file.shutdown()
        self.assertTrue(first_child.exited())
        self.assertTrue(second_child.exited())
        self.assertFalse(second.thread.is_alive())
        unstub()
        cat_file.shutdown()  # retries retained ownership rather than abandoning it
        self.assertFalse(cat_file._retiring)

    @p.expand([(stdout, fail) for stdout in (True, False) for fail in (False, True)])
    def test_plugin_unloaded_reaps_descendant_and_runs_remaining_cleanup(self, stdout: bool, fail: bool) -> None:
        if "sublime" not in sys.modules:
            self.skipTest("Sublime plugin-unload integration runs in the editor test suite")
        from GitSavvy.core import app_state, git_activity, theme_generator, worker_occupancy
        from GitSavvy.core.commands import context_menu
        from GitSavvy.github import ci_status

        calls = []
        for module, name in (
            (worker_occupancy, "on_plugin_unload"), (git_activity, "shutdown"), (ci_status, "stop"),
            (context_menu, "stop_context_menu_watcher"), (theme_generator, "stop_watchers"),
            (theme_generator, "shutdown_executor"), (app_state, "save")
        ):
            getattr(when(module), name)().thenAnswer(lambda name=name: calls.append(name))
        reader, futures, descendant = self.exited_parent(stdout)
        cat_file._readers[self.temp.name] = reader
        original_shutdown = cat_file.shutdown
        if fail:
            def reported_failure():
                original_shutdown()
                raise cat_file.ReaderError("injected cleanup report")
            when(cat_file).shutdown().thenAnswer(reported_failure)
        # Compile the actual hook alone: importing git_savvy.py would run its
        # in-place reloader and disrupt the test process before invoking unload.
        source = Path(cat_file.__file__).parent.parent / "git_savvy.py"
        hook = next(node for node in ast.parse(source.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == "plugin_unloaded")
        namespace = {"__package__": "GitSavvy"}
        exec(compile(ast.Module(body=[hook], type_ignores=[]), str(source), "exec"), namespace)
        if fail:
            with self.assertRaisesRegex(cat_file.ReaderError, "injected cleanup report"):
                namespace["plugin_unloaded"]()
        else:
            namespace["plugin_unloaded"]()
        self.assertEqual(["on_plugin_unload", "shutdown", "stop", "stop_context_menu_watcher",
                          "stop_watchers", "shutdown_executor", "save"], calls)
        self.assertTrue(all(future.done() for future in futures))
        self.assertTrue(descendant.exited())
        self.assertFalse(reader.thread.is_alive())
        self.assertFalse(reader.err_thread.is_alive())

    def exited_parent(self, stdout: bool, name: str = "child", **kwargs):
        pidfile = Path(self.temp.name) / (name + ".pid")
        exitfile = Path(self.temp.name) / (name + ".exit")
        code = dedent("""
            import os
            import pathlib
            import subprocess
            import sys
            import time

            sys.stdin.readline()
            child = subprocess.Popen(
                [sys.executable, '-c', 'import time; time.sleep(60)'],
                stdin=subprocess.DEVNULL,
                stderr=sys.stderr,
                stdout={stdout}
            )

            pidfile = pathlib.Path({pidfile!r})
            pending = pidfile.with_suffix('.tmp')
            pending.write_text(str(child.pid))
            pending.replace(pidfile)

            while not pathlib.Path({exitfile!r}).exists():
                time.sleep(.005)

            os._exit(0)
        """).format(
            stdout="sys.stdout" if stdout else "subprocess.DEVNULL",
            pidfile=str(pidfile),
            exitfile=str(exitfile)
        )
        reader = cat_file.Reader(
            self.temp.name, [self.python, "-u", "-c", code], dict(os.environ),
            timeout=.3, **kwargs
        )
        self.addCleanup(reader.join)
        self.addCleanup(reader.stop)
        futures = [reader.submit([b"HEAD"]), reader.submit([b"queued"])]
        deadline = time.monotonic() + 3
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(pidfile.exists())
        descendant = Descendant(int(pidfile.read_text()))
        self.addCleanup(descendant.close)
        # The handle is opened while the parent is still alive, eliminating PID
        # reuse races in both the assertion and emergency test cleanup.
        exitfile.touch()
        self.assertEqual(0, reader.proc.wait(timeout=2))
        return reader, futures, descendant

    @staticmethod
    def reap(proc):
        proc.kill()
        proc.wait(timeout=2)


class Descendant:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.handle = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes as w
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
            self.kernel.OpenProcess.restype = w.HANDLE
            self.kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
            self.kernel.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
            self.kernel.CloseHandle.argtypes = [w.HANDLE]
            self.handle = self.kernel.OpenProcess(0x100001, False, pid)  # SYNCHRONIZE | TERMINATE
            if not self.handle:
                raise ctypes.WinError(ctypes.get_last_error())

    def exited(self) -> bool:
        if self.handle is not None:
            return self.kernel.WaitForSingleObject(self.handle, 0) == 0
        # A killed orphan can briefly remain a zombie under container PID 1.
        try:
            return Path("/proc/{}/stat".format(self.pid)).read_text().split(") ", 1)[1].startswith("Z")
        except FileNotFoundError:
            return True

    def close(self) -> None:
        if not self.exited():
            if self.handle is not None:
                self.kernel.TerminateProcess(self.handle, 1)
                self.kernel.WaitForSingleObject(self.handle, 2000)
            else:
                os.kill(self.pid, signal.SIGKILL)
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


class Context:
    git_version = (2, 45, 0)
    repo_uses_reftable = True

    def __init__(self, repo: str, binary: str) -> None:
        self.repo_path = repo
        self.git_binary_path = binary
        self.git_dir = self.git_common_dir = os.path.join(repo, ".git")
        self.repo_config_path = os.path.join(self.git_dir, "config")

    def some_window(self):
        return None

    def _add_global_flags(self, command, args):
        return [command, *args]

    def _git_environment(self, window):
        return dict(os.environ)

    def strict_decode(self, value):
        return value.decode("utf-8")

    lax_decode = strict_decode
