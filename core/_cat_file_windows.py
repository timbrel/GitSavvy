"""Windows ownership for cat-file only: assign a suspended child to a private job."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import subprocess
import sys
import time
from typing import Protocol, cast

from .utils import STARTUPINFO


# This module is imported only by the Windows reader. Keep its platform APIs
# out of type checking on POSIX as well.
assert sys.platform == "win32"

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9

CREATE_SUSPENDED = 0x00000004
TH32CS_SNAPTHREAD = 0x00000004
SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
THREAD_QUERY_LIMITED_INFORMATION = 0x0800
THREAD_SUSPEND_RESUME = 0x0002
WAIT_OBJECT_0 = 0
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
RESUME_THREAD_FAILED = 0xffffffff

ERROR_MORE_DATA = 234
ERROR_NO_MORE_FILES = 18
ERROR_INVALID_PARAMETER = 87


class OwnedProcess:
    """Own a suspended child and its descendants through a private Windows job.

    Assignment must succeed before the child's initial thread is resumed.
    terminate() waits for the tree to exit; the reader then reaps the root and
    closes its pipes before calling close() to release the ownership handles.
    """

    def __init__(self, command: list[str], repo: str, environ: dict[str, str]) -> None:
        self.job = checked(kernel32.CreateJobObjectW(None, None))
        self.handle: ProcessHandle | None = None
        proc = None

        try:
            limits = ExtendedLimitInformation()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            checked(kernel32.SetInformationJobObject(
                self.job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                ctypes.byref(limits), ctypes.sizeof(limits)
            ))

            proc = subprocess.Popen(
                command, cwd=repo, env=environ, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=STARTUPINFO,
                creationflags=CREATE_SUSPENDED  # no descendants before assignment
            )
            self.proc = proc

            # CPython's private handle is absent from typeshed. Keep that
            # implementation dependency at this boundary, not in the reader.
            self.handle = cast(ProcessHandle, getattr(proc, "_handle"))
            checked(kernel32.AssignProcessToJobObject(self.job, int(self.handle)))

            # Popen closes the primary thread handle. Discover that still-
            # suspended, unambiguous thread before allowing the child to run.
            resume_initial_thread(proc)
        except BaseException:
            # No unowned fallback: assignment/resume failures must never leave
            # a suspended child or an inheritable job handle behind.
            try:
                if proc is not None:
                    try:
                        self.terminate(2)
                    finally:
                        proc.kill()
                        proc.wait(timeout=2)
                        for pipe in (proc.stdin, proc.stdout, proc.stderr):
                            if pipe is not None:
                                pipe.close()
            finally:
                self.close()
            raise

    def terminate(self, timeout: float) -> None:
        """Terminate all job members and wait for exit, even if the root is gone."""
        # A job is independent of the original PID: descendants remain members
        # after their parent exits. It is unnamed and its handle is noninheritable.
        deadline = time.monotonic() + timeout
        handles = self.process_handles(deadline)

        try:
            checked(kernel32.TerminateJobObject(self.job, 1))

            for handle in handles:
                remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
                if kernel32.WaitForSingleObject(handle, remaining_ms) != WAIT_OBJECT_0:
                    raise TimeoutError("cat-file job member did not terminate")

            while True:
                info = BasicAccountingInformation()
                checked(kernel32.QueryInformationJobObject(
                    self.job, JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION,
                    ctypes.byref(info), ctypes.sizeof(info), None
                ))
                if info.ActiveProcesses == 0:
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("cat-file job did not empty after termination")
                time.sleep(.005)
        finally:
            for handle in handles:
                checked(kernel32.CloseHandle(handle))

    def close(self) -> None:
        """Release the job and the reaped root's CPython process handle."""
        if self.job is not None:
            checked(kernel32.CloseHandle(self.job))
            self.job = None

        if self.handle is not None and self.proc.poll() is not None:
            self.handle.Close()

    def process_handles(self, deadline: float) -> list[int]:
        """Open verified job members; the caller must close the returned handles."""
        # Empty-job accounting can precede process-handle signalling on Windows.
        # Hold handles to the current members so teardown can wait for their exit,
        # not just their removal from the job. Verify membership to avoid PID reuse.
        capacity = 16
        while True:
            buffer = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
            if kernel32.QueryInformationJobObject(
                self.job, JOB_OBJECT_BASIC_PROCESS_ID_LIST, buffer, len(buffer), None
            ):
                break

            error = ctypes.get_last_error()
            if error != ERROR_MORE_DATA:
                raise ctypes.WinError(error)
            if time.monotonic() >= deadline:
                raise TimeoutError("cat-file job membership did not stabilize")
            capacity *= 2

        # JOBOBJECT_BASIC_PROCESS_ID_LIST starts with two DWORD counts,
        # followed by an array of ULONG_PTR process IDs.
        count = w.DWORD.from_buffer(buffer, 4).value
        pids = (ctypes.c_size_t * count).from_buffer(buffer, 8)

        handles: list[int] = []
        try:
            for pid in pids:
                handle = kernel32.OpenProcess(
                    SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid
                )
                if not handle:
                    if ctypes.get_last_error() == ERROR_INVALID_PARAMETER:  # process already gone
                        continue
                    raise ctypes.WinError(ctypes.get_last_error())

                handles.append(handle)
                member = w.BOOL()
                checked(kernel32.IsProcessInJob(handle, self.job, ctypes.byref(member)))
                if not member.value:
                    checked(kernel32.CloseHandle(handles.pop()))
            return handles
        except BaseException:
            for handle in handles:
                checked(kernel32.CloseHandle(handle))
            raise


def resume_initial_thread(proc: subprocess.Popen[bytes]) -> None:
    """Find and resume the sole initial thread of our still-suspended child."""
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise ctypes.WinError(ctypes.get_last_error())

    try:
        entry = ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        thread_ids = []
        more = kernel32.Thread32First(snapshot, ctypes.byref(entry))

        while more:
            if entry.th32OwnerProcessID == proc.pid:
                thread_ids.append(entry.th32ThreadID)
            more = kernel32.Thread32Next(snapshot, ctypes.byref(entry))

        if ctypes.get_last_error() != ERROR_NO_MORE_FILES:
            raise ctypes.WinError(ctypes.get_last_error())
        if len(thread_ids) != 1:
            raise OSError("cat-file suspended child has no unambiguous initial thread")

        thread = checked(kernel32.OpenThread(
            THREAD_QUERY_LIMITED_INFORMATION | THREAD_SUSPEND_RESUME, False, thread_ids[0]
        ))
        try:
            if proc.poll() is not None or kernel32.GetProcessIdOfThread(thread) != proc.pid:
                raise OSError("cat-file initial thread no longer belongs to the live child")

            previous_count = kernel32.ResumeThread(thread)
            if previous_count == RESUME_THREAD_FAILED:
                raise ctypes.WinError(ctypes.get_last_error())
            if previous_count != 1:
                raise OSError("cat-file initial thread was not suspended exactly once")
        finally:
            checked(kernel32.CloseHandle(thread))
    finally:
        checked(kernel32.CloseHandle(snapshot))


def checked(value):
    """Check APIs whose failure sentinel is NULL/FALSE, preserving last-error."""
    if not value:
        raise ctypes.WinError(ctypes.get_last_error())
    return value


class ProcessHandle(Protocol):
    """The CPython subprocess.Handle operations used for deterministic cleanup."""

    def __int__(self) -> int: ...
    def Close(self) -> None: ...


class BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", w.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", w.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", w.DWORD),
        ("SchedulingClass", w.DWORD)
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong)
    ]


class ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", BasicLimitInformation),
        ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t)
    ]


class BasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", w.DWORD),
        ("TotalProcesses", w.DWORD),
        ("ActiveProcesses", w.DWORD),
        ("TotalTerminatedProcesses", w.DWORD)
    ]


class ThreadEntry32(ctypes.Structure):
    _fields_ = [
        ("dwSize", w.DWORD),
        ("cntUsage", w.DWORD),
        ("th32ThreadID", w.DWORD),
        ("th32OwnerProcessID", w.DWORD),
        ("tpBasePri", w.LONG),
        ("tpDeltaPri", w.LONG),
        ("dwFlags", w.DWORD)
    ]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
for name, restype, argtypes in (
    ("CreateJobObjectW", w.HANDLE, [ctypes.c_void_p, w.LPCWSTR]),
    ("SetInformationJobObject", w.BOOL, [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]),
    ("AssignProcessToJobObject", w.BOOL, [w.HANDLE, w.HANDLE]),
    ("TerminateJobObject", w.BOOL, [w.HANDLE, w.UINT]),
    ("QueryInformationJobObject", w.BOOL, [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p]),
    ("CloseHandle", w.BOOL, [w.HANDLE]),
    ("CreateToolhelp32Snapshot", w.HANDLE, [w.DWORD, w.DWORD]),
    ("Thread32First", w.BOOL, [w.HANDLE, ctypes.POINTER(ThreadEntry32)]),
    ("Thread32Next", w.BOOL, [w.HANDLE, ctypes.POINTER(ThreadEntry32)]),
    ("OpenThread", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
    ("GetProcessIdOfThread", w.DWORD, [w.HANDLE]),
    ("ResumeThread", w.DWORD, [w.HANDLE]),
    ("OpenProcess", w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]),
    ("IsProcessInJob", w.BOOL, [w.HANDLE, w.HANDLE, ctypes.POINTER(w.BOOL)]),
    ("WaitForSingleObject", w.DWORD, [w.HANDLE, w.DWORD]),
):
    function = getattr(kernel32, name)
    function.restype = restype
    function.argtypes = argtypes
