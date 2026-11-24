"""Native Windows job startup failures must never run or leak an unowned child."""
from __future__ import annotations

import ctypes
import gc
from ctypes import wintypes as w
import os
from pathlib import Path
import shutil
import tempfile
from unittest import TestCase, skipUnless

from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p


@skipUnless(os.name == "nt", "Windows Job Object API")
class TestCatFileWindowsOwnership(TestCase):
    def setUp(self) -> None:
        from GitSavvy.core import _cat_file_windows as windows
        self.windows = windows
        self.temp = tempfile.TemporaryDirectory(prefix="gitsavvy-cat-file-job-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(unstub)
        self.python = shutil.which("python3") or shutil.which("python")
        self.python = windows.subprocess.check_output([
            self.python, "-c", "import sys; print(getattr(sys, '_base_executable', sys.executable))"
        ]).decode().strip()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetCurrentProcess.restype = w.HANDLE
        self.kernel.GetProcessHandleCount.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        self.kernel.GetHandleInformation.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]

    @p.expand([("AssignProcessToJobObject",), ("Thread32First",), ("OpenThread",), ("ResumeThread",)])
    def test_startup_failure_reaps_suspended_child_and_closes_handles(self, api: str) -> None:
        marker = Path(self.temp.name) / "must-not-run"
        command = [self.python, "-c", "from pathlib import Path; Path({!r}).touch()".format(str(marker))]
        processes = []
        popen = self.windows.subprocess.Popen

        def record_process(*args, **kwargs):
            proc = popen(*args, **kwargs)
            processes.append(proc)
            return proc

        when(self.windows.subprocess).Popen(...).thenAnswer(record_process)
        # Mockito supports dynamic method names via getattr on its invocation builder.
        def fail(*args):
            raise OSError("injected " + api)

        getattr(when(self.windows.kernel32), api)(...).thenAnswer(fail)
        before = self.handle_count()
        for _ in range(3):
            with self.assertRaisesRegex(OSError, "injected " + api):
                self.windows.OwnedProcess(command, self.temp.name, dict(os.environ))
        self.assertFalse(marker.exists(), "the suspended child must never execute before successful ownership")
        self.assertEqual(3, len(processes))
        for proc in processes:
            self.assertIsNotNone(proc.poll())
            self.assertTrue(all(pipe.closed for pipe in (proc.stdin, proc.stdout, proc.stderr)))
            self.assertTrue(proc._handle.closed, "Popen process handle must also be closed")
        processes.clear()
        del proc
        unstub()
        gc.collect()  # CPython's closed buffered objects retain OS-backed locks until collection.
        self.assertEqual(before, self.handle_count(), "job/snapshot/thread/pipe/process handles must all close")

    def test_process_creation_failure_closes_the_job(self) -> None:
        when(self.windows.subprocess).Popen(...).thenRaise(FileNotFoundError("injected creation failure"))
        before = self.handle_count()
        with self.assertRaisesRegex(FileNotFoundError, "injected creation failure"):
            self.windows.OwnedProcess([self.python], self.temp.name, dict(os.environ))
        unstub()
        self.assertEqual(before, self.handle_count())

    def test_ambiguous_initial_thread_fails_closed(self) -> None:
        marker = Path(self.temp.name) / "must-not-run"
        processes = []
        popen = self.windows.subprocess.Popen

        def record_process(*args, **kwargs):
            proc = popen(*args, **kwargs)
            processes.append(proc)
            return proc

        def first_thread(snapshot, entry):
            entry._obj.th32OwnerProcessID = processes[0].pid
            entry._obj.th32ThreadID = 1
            return True

        def second_thread(snapshot, entry):
            if entry._obj.th32ThreadID == 1:
                entry._obj.th32ThreadID = 2
                return True
            ctypes.set_last_error(18)
            return False

        when(self.windows.subprocess).Popen(...).thenAnswer(record_process)
        when(self.windows.kernel32).Thread32First(...).thenAnswer(first_thread)
        when(self.windows.kernel32).Thread32Next(...).thenAnswer(second_thread)
        when(self.windows.kernel32).OpenThread(...).thenRaise(AssertionError("Must not select an arbitrary thread"))
        with self.assertRaisesRegex(OSError, "unambiguous"):
            self.windows.OwnedProcess(
                [self.python, "-c", "from pathlib import Path; Path({!r}).touch()".format(str(marker))],
                self.temp.name, dict(os.environ)
            )
        self.assertFalse(marker.exists())
        self.assertIsNotNone(processes[0].poll())
        self.assertTrue(processes[0]._handle.closed)
        self.assertTrue(all(pipe.closed for pipe in (processes[0].stdin, processes[0].stdout, processes[0].stderr)))

    def test_job_handle_is_private_and_all_handles_close_after_exit(self) -> None:
        before = self.handle_count()
        owned = self.windows.OwnedProcess([self.python, "-c", "pass"], self.temp.name, dict(os.environ))
        try:
            flags = w.DWORD()
            self.assertTrue(self.kernel.GetHandleInformation(owned.job, ctypes.byref(flags)))
            self.assertEqual(0, flags.value & 1, "HANDLE_FLAG_INHERIT must be absent")
            owned.proc.wait(timeout=2)
            owned.terminate(2)  # valid even after the root has exited
        finally:
            owned.terminate(2)
            owned.proc.wait(timeout=2)
            for pipe in (owned.proc.stdin, owned.proc.stdout, owned.proc.stderr):
                pipe.close()
            owned.close()
        del pipe, owned
        gc.collect()
        self.assertEqual(before, self.handle_count())

    def handle_count(self) -> int:
        gc.collect()
        count = w.DWORD()
        self.assertTrue(self.kernel.GetProcessHandleCount(self.kernel.GetCurrentProcess(), ctypes.byref(count)))
        return count.value
