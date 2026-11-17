"""Worktree-local, serialized metadata readers. No object/name answers are cached."""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future, TimeoutError as FutureTimeout
from io import BufferedReader, BufferedWriter
import os
import signal
import subprocess
import sys
import threading
import time
from weakref import WeakValueDictionary

from .exceptions import AbortedGitCall, DetachedView, GitSavvyError
from .utils import STARTUPINFO, proc_has_been_aborted_by_user

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .git_command import _GitCommand


# The in-place plugin reloader reexecutes this module in its existing namespace.
if "shutdown" in globals():
    globals()["shutdown"]()

# Custom batch formats date to Git 1.8.4. Persistence is deliberately limited
# to reftable; files repositories retain their cheap direct metadata reads.
BATCH_CHECK = "--batch-check=%(objectname) %(objecttype)"
# Older maintenance backports suppress fetching but may die instead of returning
# `missing`. 2.45 also clears fetch_if_missing during repository setup.
GIT_SUPPORTS_NO_LAZY_FETCH = (2, 45, 0)
IDLE_SECONDS = 300.0
REQUEST_SECONDS = 10.0
SHUTDOWN_SECONDS = 2.0
MAX_EXPRESSION_BYTES = 65536
BATCH_SIZE = 256
_registry_lock = threading.Lock()
_termination_lock = threading.Lock()
# Running reader threads keep their instances alive; the registry does not keep
# idle/exited readers alive. Failed retirements remain owned until shutdown retries.
_readers: WeakValueDictionary[str, Reader] = WeakValueDictionary()
_retiring: set[Reader] = set()
_enabled = True


def read_object_metadata(
    git: _GitCommand, refs: list[str], *, show_panel_on_error: bool = True
) -> list[str]:
    """Return one batch-check line per expression, preserving input order.

    Successful lines contain an object ID and type, including non-commits.
    Missing/ambiguous lines contain the original expression and that status.
    Callers handle filtering; fatal Git errors fail the request instead.
    """
    if not refs:
        return []

    args = git._add_global_flags("cat-file", [BATCH_CHECK])
    command = [git.git_binary_path, *filter(None, args)]
    window = git.some_window()
    try:
        repo = git.repo_path
    except DetachedView as error:
        GitSavvyError(str(error), show_panel=False, window=window)  # logging only, as in git()
        raise
    except Exception as error:
        raise GitSavvyError(str(error), window=window, show_panel=show_panel_on_error) from None

    reader: Reader | None = None
    try:
        encoded = [ref.encode("utf-8") for ref in refs]
        if any(
            b"\n" in ref or b"\r" in ref or b"\0" in ref
            or len(ref) > MAX_EXPRESSION_BYTES
            for ref in encoded
        ):
            raise ReaderError("Unsupported cat-file expression (newline, NUL or excessive length)")

        if not is_enabled_for(git):
            return git.git(
                "cat-file", BATCH_CHECK, stdin="\n".join(refs) + "\n",
                show_panel_on_error=show_panel_on_error
            ).splitlines()

        environ = dict(git._git_environment(window))
        # Metadata should not fetch an object just to name it. resolve() can
        # fall back to normal rev-parse if resolution itself needs a fetch.
        environ["GIT_NO_LAZY_FETCH"] = "1"
        identity = (tuple(command), tuple(sorted(environ.items())), context_files(git, environ))
        key = os.path.normcase(os.path.realpath(repo))

        lines: list[bytes] = []
        for offset in range(0, len(encoded), BATCH_SIZE):
            batch = encoded[offset:offset + BATCH_SIZE]
            with _registry_lock:
                reader = get_reader(key, repo, command, environ, identity)
                future = reader.submit(batch)

                # Idle expiry can win just before submit; no request was sent.
                if future is None:
                    reader = get_reader(key, repo, command, environ, identity)
                    future = reader.submit(batch)
                assert future is not None

            lines.extend(reader.result(future))

        return [git.strict_decode(line) for line in lines]
    except (ReaderError, OSError, UnicodeError) as error:
        stdout = git.lax_decode(error.stdout) if isinstance(error, ReaderError) else ""
        stderr = git.lax_decode(reader.stderr) if reader is not None else ""
        aborted = reader is not None and proc_has_been_aborted_by_user(reader.proc)
        error_type = AbortedGitCall if aborted else GitSavvyError
        raise error_type(
            "$ cat-file {} ({})\n\n{}\n{}".format(BATCH_CHECK, repo, error, stderr),
            cmd=command, stdout=stdout, stderr=stderr or str(error), window=window,
            show_panel=show_panel_on_error and not aborted
        ) from None


def is_enabled_for(git: _GitCommand) -> bool:
    """Whether this repository/client combination supports persistent metadata."""
    if git.git_version < GIT_SUPPORTS_NO_LAZY_FETCH:
        return False
    try:
        return git.repo_uses_reftable
    except (DetachedView, ValueError):
        # Eligibility is optional. Let the original git() path report failed
        # repository discovery with its usual panel policy and detached logging.
        return False


def start() -> None:
    """Allow lazy reader creation after plugin load."""
    global _enabled
    with _registry_lock:
        _enabled = True


def shutdown() -> None:
    """Stop and join all owned readers, retaining any whose cleanup failed."""
    global _enabled
    with _registry_lock:
        _enabled = False
        readers = set(_readers.values()) | _retiring
        _readers.clear()
        _retiring.update(readers)

    failures = []
    for reader in readers:
        try:
            reader.stop()
        except (OSError, ReaderError) as error:
            failures.append(str(error))

    for reader in readers:
        try:
            reader.join()
        except ReaderError as error:
            failures.append(str(error))
        else:
            with _registry_lock:
                _retiring.discard(reader)

    if failures:
        raise ReaderError("cat-file shutdown: " + "; ".join(failures))


def get_reader(
    key: str, repo: str, command: list[str], environ: dict[str, str], identity: tuple
) -> Reader:
    """Reuse a live reader with matching context. Caller holds the registry lock."""
    if not _enabled:
        raise ReaderError("cat-file service is shut down")

    reader = _readers.get(key)
    if reader is not None and (
        reader.identity != identity or reader.closed or reader.proc.poll() is not None
    ):
        del _readers[key]
        retire(reader)
        reader = None

    if reader is None:
        reader = Reader(repo, command, environ, identity=identity)
        _readers[key] = reader
    return reader


def retire(reader: Reader) -> None:
    """Stop and join a replaced reader. Caller holds the registry lock."""
    # Retain ownership on exceptional cleanup, but do not leave a dead reader
    # occupying the worktree's request slot.
    _retiring.add(reader)
    try:
        reader.stop()
    finally:
        reader.join()
    _retiring.discard(reader)


def context_files(git: _GitCommand, environ: dict[str, str]) -> tuple:
    """Fingerprint local files whose changes invalidate Git's cached state."""
    # Reftable reloads ordinary refs, but Git caches replacements separately.
    # A shared table update must therefore retire the reader, as must changes
    # to local configuration, alternates or cached shallow state. This is not a
    # complete config dependency watcher: reload for global/include-file edits.
    shallow_file = environ.get("GIT_SHALLOW_FILE", os.path.join(git.git_common_dir, "shallow"))
    paths = [
        git.repo_config_path,
        os.path.join(git.git_dir, "config.worktree"),
        os.path.join(git.git_common_dir, "objects", "info", "alternates"),
        os.path.join(git.git_common_dir, "reftable", "tables.list"),
        os.path.join(git.repo_path, shallow_file),
    ]
    return tuple(file_signature(path) for path in paths)


def file_signature(path: str) -> tuple | None:
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class ReaderError(Exception):
    """A transport/lifecycle failure, optionally carrying partial stdout."""

    def __init__(self, message: str, stdout: bytes = b"") -> None:
        super().__init__(message)
        self.stdout = stdout


class Reader:
    """Own a process tree and serialize metadata transactions on one worker.

    `condition` protects the queue, active future, closed flag and stderr EOF.
    The worker alone writes stdin and reads stdout; a second thread drains
    stderr. Neither thread acquires the registry lock. `kill_lock` serializes
    process termination, outside the condition lock.

    `closed` means no more submissions, not that cleanup has finished. stop()
    settles callers before terminating the process tree; the worker then reaps
    the child and closes pipes in cleanup(). join() waits for that work and
    reports any deferred cleanup error. To force shutdown, call stop() then join().
    """

    def __init__(
        self, repo: str, command: list[str], environ: dict[str, str], *,
        identity: tuple = (), idle: float = IDLE_SECONDS, timeout: float = REQUEST_SECONDS
    ) -> None:
        self.repo = repo
        self.identity = identity
        self.idle = idle
        self.timeout = timeout

        self.condition = threading.Condition()
        self.pending: deque[tuple[list[bytes], Future[list[bytes]]]] = deque()
        self.closed = False
        self.stderr_eof = False
        self.active: Future[list[bytes]] | None = None

        self.cleanup_error: Exception | None = None
        self.kill_lock = threading.Lock()
        self.kill_requested = False
        self.stderr = b""

        if sys.platform == "win32":
            # Unlike fire-and-forget taskkill, job ownership lets us wait for
            # the whole process tree to exit before replacing an idle or failed
            # reader, without launching another process just for teardown.
            from ._cat_file_windows import OwnedProcess
            self.owned_process = OwnedProcess(command, repo, environ)
            self.proc = self.owned_process.proc
        else:
            self.proc = subprocess.Popen(
                command, cwd=repo, env=environ, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, startupinfo=STARTUPINFO,
                start_new_session=True
            )

        # Popen's types allow absent/text/unbuffered pipes. This reader always
        # owns three buffered binary pipes; establish that invariant once.
        assert isinstance(self.proc.stdin, BufferedWriter)
        assert isinstance(self.proc.stdout, BufferedReader)
        assert isinstance(self.proc.stderr, BufferedReader)
        self.stdin = self.proc.stdin
        self.stdout = self.proc.stdout
        self.stderr_pipe = self.proc.stderr

        self.err_thread = threading.Thread(
            target=self.drain_stderr, name="GitSavvyCatFileStderr", daemon=True
        )
        self.thread = threading.Thread(target=self.run, name="GitSavvyCatFile", daemon=True)

        try:
            self.err_thread.start()
            self.thread.start()
            print("GitSavvy: {} cat-file service started".format(self.repo))
        except BaseException:
            self.cleanup(ReaderError("cat-file could not start its reader threads"))
            raise

    def submit(self, refs: list[bytes]) -> Future[list[bytes]] | None:
        """Queue a transaction, or return None if it was never accepted."""
        with self.condition:
            if self.closed:
                return None
            if len(self.pending) >= 16:
                raise ReaderError("cat-file request queue is full")

            future: Future[list[bytes]] = Future()
            self.pending.append((refs, future))
            self.condition.notify()
            return future

    def result(self, future: Future[list[bytes]]) -> list[bytes]:
        """Wait with a deadline; failure/cancellation stops the whole reader."""
        try:
            return future.result(timeout=self.timeout)
        except FutureTimeout:
            self.stop()
            self.join()
            raise ReaderError("cat-file request timed out after {} seconds".format(self.timeout)) from None
        except BaseException:
            # Cancellation of the waiting caller also retires the transaction.
            self.stop()
            self.join()
            raise

    def stop(self, failure: ReaderError | None = None) -> None:
        """Reject submissions and settle callers, then terminate owned processes.

        Safe from the worker or a waiting caller. Does not wait for pipe cleanup;
        callers outside the worker use join() to observe its completion.
        """
        with self.condition:
            self.closed = True
            failure = failure or ReaderError("cat-file service stopped")

            if self.active is not None and not self.active.done():
                self.active.set_exception(failure)

            while self.pending:
                _, future = self.pending.popleft()
                if future.set_running_or_notify_cancel():
                    future.set_exception(failure)

            self.condition.notify_all()

        # Callers are settled before touching process/pipe cleanup.
        self.kill()

    def kill(self) -> None:
        """Terminate the owned process tree once; failed attempts may be retried."""
        with self.kill_lock:
            if self.kill_requested:
                return

            if sys.platform == "win32":
                self.owned_process.terminate(SHUTDOWN_SECONDS)
            else:
                # Descendants retain our session/process-group identity even
                # after the direct child exits. Never substitute a PID-only kill.
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

            self.kill_requested = True

    def join(self) -> None:
        """Wait for worker cleanup and report failures. Never call on the worker."""
        self.thread.join(SHUTDOWN_SECONDS)
        if self.thread.is_alive():
            raise ReaderError("cat-file worker did not shut down")
        if self.cleanup_error is not None:
            raise ReaderError("cat-file cleanup failed: {}".format(self.cleanup_error))

    def run(self) -> None:
        """Take, exchange and settle transactions until stopped, idle or failed."""
        failure = ReaderError("cat-file service stopped")
        last_used = time.monotonic()

        try:
            while True:
                with self.condition:
                    if not self.pending and not self.closed:
                        self.condition.wait_for(
                            lambda: self.pending or self.closed or self.stderr_eof,
                            timeout=max(0, self.idle - (time.monotonic() - last_used))
                        )

                    if self.closed or not self.pending:
                        self.closed = True
                        break

                    refs, active = self.pending.popleft()
                    if not active.set_running_or_notify_cancel():
                        continue
                    self.active = active

                lines = self.exchange(refs)
                last_used = time.monotonic()

                with self.condition:
                    if not active.done():
                        active.set_result(lines)
                    self.active = None
        except (OSError, ReaderError, ValueError) as error:
            failure = error if isinstance(error, ReaderError) else ReaderError(str(error))
        finally:
            self.cleanup(failure)

    def cleanup(self, failure: ReaderError) -> None:
        """Reap and close on the worker, saving errors for join() to report.

        Also used by the constructor if reader threads could not be started.
        No more stdout reads may occur; join the stderr drainer before closing
        buffers so pipe closure cannot block on a live reader's buffer lock.
        """
        try:
            self.stop(failure)
            self.proc.wait(timeout=SHUTDOWN_SECONDS)

            if self.err_thread.ident is not None:
                self.err_thread.join(SHUTDOWN_SECONDS)
            if self.err_thread.is_alive():
                raise ReaderError("cat-file stderr reader did not shut down")

            # Only the transaction worker reads stdout, and stderr's reader
            # has exited. Never close a buffer with a live blocking reader.
            for pipe in (self.stdin, self.stdout, self.stderr_pipe):
                try:
                    pipe.close()
                except OSError:
                    pass  # Closing buffered stdin can repeat a broken pipe.
        except (OSError, ReaderError, subprocess.TimeoutExpired) as error:
            self.cleanup_error = error
        finally:
            if sys.platform == "win32":
                try:
                    self.owned_process.close()
                except OSError as error:
                    self.cleanup_error = error

            if self.proc.poll() is not None:
                with _termination_lock:
                    print("GitSavvy: {} cat-file service terminated".format(self.repo))

    def exchange(self, refs: list[bytes]) -> list[bytes]:
        """Write/read each expression in order. Only the worker touches these pipes."""
        lines: list[bytes] = []
        for ref in refs:
            self.stdin.write(ref + b"\n")
            self.stdin.flush()

            line = self.stdout.readline(MAX_EXPRESSION_BYTES + 256)
            if not line.endswith(b"\n"):
                raise ReaderError(
                    "cat-file closed or returned an incomplete response",
                    b"".join(line + b"\n" for line in lines) + line
                )

            line = line.rstrip(b"\r\n")
            oid, _, kind = line.partition(b" ")
            if not (
                line == ref + b" missing"
                or line == ref + b" ambiguous"
                or (kind in (b"commit", b"tree", b"blob", b"tag")
                    and len(oid) in (40, 64) and all(c in b"0123456789abcdef" for c in oid))
            ):
                raise ReaderError("Invalid cat-file metadata response", line)
            lines.append(line)
        return lines

    def drain_stderr(self) -> None:
        """Keep a bounded diagnostic tail and wake the worker when stderr closes."""
        while True:
            chunk = self.stderr_pipe.read1(4096)
            if not chunk:
                # Also reap a child that dies while idle, rather than retaining
                # its handles until another caller or the five-minute deadline.
                with self.condition:
                    self.stderr_eof = True
                    self.condition.notify_all()
                return
            self.stderr = (self.stderr + chunk)[-65536:]
