"""Real-process coverage for the persistent metadata reader and its caller seam."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import io
import importlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from textwrap import dedent
import time
from unittest import TestCase

from GitSavvy.core import cat_file
from GitSavvy.core.exceptions import GitSavvyError
from GitSavvy.core.git_command import GitCommand
from GitSavvy.tests.mockito import unstub, when
from GitSavvy.tests.parameterized import parameterized as p


FORMATS = [(h, r) for h in ("sha1", "sha256") for r in ("files", "reftable")]
PERSISTENT_FORMATS = [(h, "reftable") for h in ("sha1", "sha256")]


class TestPersistentCommitRefs(TestCase):
    def setUp(self) -> None:
        cat_file.shutdown()
        cat_file.start()
        self.temp = tempfile.TemporaryDirectory(prefix="gitsavvy-cat-file-")
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(cat_file.start)
        self.addCleanup(cat_file.shutdown)
        self.addCleanup(unstub)
        self.binary = GitCommand().git_binary_path
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.git = RepoGit(str(self.repo), self.binary)
        # Failures still construct the real GitSavvyError, without opening a panel.
        when(GitSavvyError).show_error_panel().thenReturn(None)

    @p.expand(PERSISTENT_FORMATS)
    def test_resolve_commit_refs_mutations_and_worktree_identity(self, hash_format, ref_format) -> None:
        self.create_repo(hash_format, ref_format)
        first = self.commit("first")
        self.assertEqual({}, self.git.resolve_commit_refs([]))
        self.assertEqual(0, len(cat_file._readers))
        self.assertEqual({"HEAD": first}, self.git.resolve_commit_refs(["HEAD", "missing", "HEAD"]))
        reader = self.reader()
        self.run_git("update-ref", "refs/heads/missing", first)
        self.assertEqual({"missing": first}, self.git.resolve_commit_refs(["missing"]))
        second = self.commit("second")
        self.assertEqual({"HEAD": second, "HEAD@{1}": first},
                         self.git.resolve_commit_refs(["HEAD", "HEAD@{1}", "HEAD^{tree}"]))
        self.run_git("update-ref", "refs/heads/missing", second)
        self.assertEqual({"missing": second}, self.git.resolve_commit_refs(["missing"]))
        self.run_git("update-ref", "-d", "refs/heads/missing")
        self.assertEqual({}, self.git.resolve_commit_refs(["missing"]))
        self.run_git("pack-refs", "--all")
        self.run_git("gc")
        self.assertEqual({"HEAD": second}, self.git.resolve_commit_refs(["HEAD"]))
        self.assertIsNotNone(reader.proc.poll())
        reader = self.reader()
        self.assertEqual({"HEAD": second}, self.git.resolve_commit_refs(["HEAD"]))
        self.assertIs(reader, self.reader())

        linked = Path(self.temp.name) / "linked"
        self.run_git("worktree", "add", "--detach", str(linked), first)
        other = RepoGit(str(linked), self.binary)
        self.assertEqual({"HEAD": first}, other.resolve_commit_refs(["HEAD"]))
        self.assertEqual({"HEAD": second}, self.git.resolve_commit_refs(["HEAD"]))
        self.assertEqual(2, len(cat_file._readers))
        self.assertEqual(2, len({r.proc.pid for r in cat_file._readers.values()}))

    @p.expand(FORMATS)
    def test_resolve_uses_live_metadata_for_full_ids(self, hash_format, ref_format) -> None:
        self.create_repo(hash_format, ref_format)
        first = self.commit("first")
        if ref_format == "files":
            when(cat_file).context_files(...).thenRaise(AssertionError("Unexpected freshness checks"))
        self.assertEqual(first, self.git.resolve("HEAD"))
        reader = self.reader() if ref_format == "reftable" else None
        second = self.commit("second")
        self.assertEqual(second, self.git.resolve("HEAD"))
        self.run_git("tag", "-a", "annotated", "-m", "tag", first)
        for ref in ("HEAD~1", "HEAD^{tree}", "annotated", "annotated^{}"):
            self.assertEqual(self.run_git("rev-parse", "--verify", ref).strip(), self.git.resolve(ref))
        self.assertEqual(first, self.git.resolve("HEAD@{1}"))
        self.assertEqual(self.run_git("rev-parse", "--short", "HEAD").strip(),
                         self.git.resolve("HEAD", short=True))
        self.assertIsNone(self.git.resolve("absent", on_error="ignore"))
        self.assertEqual("f" * len(first), self.git.resolve("f" * len(first)))
        if reader is not None:
            self.assertIsNotNone(reader.proc.poll())
            reader = self.reader()
            self.assertEqual(second, self.git.resolve("HEAD"))
            self.assertIs(reader, self.reader())
        else:
            self.assertEqual(0, len(cat_file._readers))

    def test_files_backend_does_not_reuse_stale_packed_refs(self) -> None:
        self.create_repo(ref_format="files")
        first = self.commit("first")
        second = self.commit("second")
        self.run_git("config", "core.checkStat", "minimal")
        self.run_git("config", "core.trustCtime", "false")
        self.run_git("update-ref", "refs/heads/topic", first)
        self.run_git("pack-refs", "--all")
        self.assertEqual(first, self.git.resolve("topic"))
        packed = self.repo / ".git" / "packed-refs"
        before = packed.stat()
        self.run_git("update-ref", "refs/heads/topic", second)
        self.run_git("pack-refs", "--all")
        os.utime(packed, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertEqual(before.st_size, packed.stat().st_size)
        self.assertEqual(second, self.git.resolve("topic"))
        self.assertEqual(0, len(cat_file._readers))

    @p.expand(PERSISTENT_FORMATS)
    def test_replacement_edits_retire_cached_object_state(self, hash_format, ref_format) -> None:
        self.create_repo(hash_format, ref_format)
        first = self.commit("first")
        (self.repo / "tracked.txt").write_text("second tree")
        self.run_git("add", "tracked.txt")
        second = self.commit("second")
        self.run_git("checkout", "--detach", first)
        self.assertEqual(self.run_git("rev-parse", "HEAD^{tree}").strip(), self.git.resolve("HEAD^{tree}"))
        reader = self.reader()
        self.run_git("replace", first, second)
        self.assertEqual(self.run_git("rev-parse", "HEAD^{tree}").strip(), self.git.resolve("HEAD^{tree}"))
        self.assertIsNotNone(reader.proc.poll())

    def test_shallow_boundary_change_retires_cached_parents(self) -> None:
        self.create_repo()
        first = self.commit("first")
        second = self.commit("second")
        self.assertEqual(first, self.git.resolve("HEAD~1"))
        reader = self.reader()
        (self.repo / ".git" / "shallow").write_text(second + "\n")
        self.assertIsNone(self.git.resolve("HEAD~1", on_error="ignore"))
        self.assertIsNotNone(reader.proc.poll())

    def test_alternates_change_retires_cached_object_sources(self) -> None:
        self.create_repo()
        self.commit("first")
        other = Path(self.temp.name) / "objects-source"
        self.run_git("init", "--object-format=sha1", str(other))
        source = other / "blob.txt"
        source.write_text("alternate object")
        blob = self.run_git("-C", str(other), "hash-object", "-w", str(source)).strip()
        self.assertEqual([blob + " missing"], cat_file.read_object_metadata(self.git, [blob]))
        reader = self.reader()
        (self.repo / ".git" / "objects" / "info" / "alternates").write_bytes(
            os.fsencode((other / ".git" / "objects").as_posix()) + b"\n"
        )
        self.assertEqual([blob + " blob"], cat_file.read_object_metadata(self.git, [blob]))
        self.assertIsNotNone(reader.proc.poll())

    def test_resolution_does_not_fetch_missing_promised_blobs(self) -> None:
        self.create_repo()
        (self.repo / "tracked.txt").write_text("promised blob")
        self.run_git("add", "tracked.txt")
        self.commit("first")
        blob = self.run_git("rev-parse", "HEAD:tracked.txt").strip()
        self.run_git("config", "uploadpack.allowFilter", "true")
        clone = Path(self.temp.name) / "partial"
        self.run_git("clone", "--ref-format=reftable", "--filter=blob:none", "--no-checkout",
                     self.repo.as_uri(), str(clone))
        self.repo = clone
        self.git = RepoGit(str(clone), self.binary)
        for ref in (blob, "HEAD:tracked.txt"):
            self.assertEqual(blob, self.git.resolve(ref))
            probe = subprocess.run(
                [self.binary, "-C", str(clone), "--no-lazy-fetch", "cat-file", "-e", blob],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            self.assertNotEqual(0, probe.returncode, "metadata resolution must not fetch the blob")

    @p.expand(FORMATS)
    def test_operation_heads_refresh_without_answer_cache(self, hash_format, ref_format) -> None:
        self.create_repo(hash_format, ref_format)
        first = self.commit("first")
        read_heads = self.git.read_cherry_pick_and_revert_heads
        self.assertEqual((None, None), read_heads())
        reader = self.reader() if ref_format == "reftable" else None
        for ref, expected in (("CHERRY_PICK_HEAD", (first, None)), ("REVERT_HEAD", (None, first))):
            self.run_git("update-ref", ref, first)
            self.assertEqual(expected, read_heads())
            self.run_git("update-ref", "-d", ref)
            self.assertEqual((None, None), read_heads())
        if reader:
            self.assertIsNotNone(reader.proc.poll())
            reader = self.reader()
            self.assertEqual((None, None), read_heads())
            self.assertIs(reader, self.reader())
        else:
            self.assertEqual(0, len(cat_file._readers), "files operation heads must not start a reader")

    @p.expand(PERSISTENT_FORMATS)
    def test_invalid_reflog_selectors_are_omitted_without_killing_shared_reader(self, hash_format, ref_format) -> None:
        self.create_repo(hash_format, ref_format)
        first = self.commit("first")
        self.git.resolve_commit_refs(["HEAD"])
        old = self.reader()
        when(GitSavvyError).show_error_panel().thenRaise(AssertionError("Unexpected error panel"))
        self.assertEqual(
            {"HEAD": first, "HEAD@{0}": first},
            self.git.resolve_commit_refs(["HEAD", "HEAD@{999999}", "absent@{0}", "HEAD@{0}"])
        )
        self.assertEqual(
            {"HEAD@{0}": first},
            self.git.resolve_reflog_refs(["HEAD@{999999}", "HEAD@{0}"])
        )
        self.assertIsNone(old.proc.poll())
        self.assertTrue(old.thread.is_alive())
        self.assertEqual({"HEAD": first}, self.git.resolve_commit_refs(["HEAD"]))
        self.assertIs(old, self.reader())

    def test_forced_child_death_and_concurrent_transactions(self) -> None:
        self.create_repo()
        first = self.commit("first")
        second = self.commit("second")
        self.git.resolve_commit_refs(["HEAD"])
        old = self.reader()
        old.proc.kill()
        old.proc.wait(timeout=2)
        old.thread.join(2)
        self.assertFalse(old.thread.is_alive())
        self.assertFalse(old.err_thread.is_alive())
        self.git.resolve_commit_refs(["HEAD"])
        reader = self.reader()
        with ThreadPoolExecutor(max_workers=8) as pool:
            queries = [[b"HEAD"], [b"HEAD~1"], [b"absent"]] * 12
            results = list(pool.map(lambda refs: reader.result(reader.submit(refs)), queries))
        self.assertEqual([[second.encode() + b" commit"], [first.encode() + b" commit"],
                          [b"absent missing"]] * 12, results)

    def test_context_changes_retire_the_reader(self) -> None:
        self.create_repo()
        first = self.commit("first")
        self.git.resolve_commit_refs(["HEAD"])
        old = self.reader()
        self.run_git("config", "core.abbrev", "12")
        self.assertEqual({"HEAD": first}, self.git.resolve_commit_refs(["HEAD"]))
        self.assertTrue(old.closed)
        self.assertIsNotNone(old.proc.poll())
        old = self.reader()
        self.git._project_settings["env"] = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.abbrev",
                                              "GIT_CONFIG_VALUE_0": "13"}
        self.assertEqual({"HEAD": first}, self.git.resolve_commit_refs(["HEAD"]))
        self.assertIsNotNone(old.proc.poll())
        old = self.reader()
        self.git._project_settings["global_pre_flags"] = {"cat-file": ["-c", "core.abbrev=14"]}
        self.git.resolve_commit_refs(["HEAD"])
        self.assertIsNotNone(old.proc.poll())
        self.assertEqual(self.binary, self.reader().proc.args[0])
        self.assertIn("core.abbrev=14", self.reader().proc.args)
        old = self.reader()
        alternate_spelling = os.path.join(os.path.dirname(self.binary), ".", os.path.basename(self.binary))
        self.git._project_settings["git_path"] = alternate_spelling
        self.git.resolve_commit_refs(["HEAD"])
        self.assertEqual(alternate_spelling, self.reader().proc.args[0])
        self.assertIsNotNone(old.proc.poll())

    def test_idle_reader_is_restarted_on_next_lookup(self) -> None:
        self.create_repo()
        first = self.commit("first")
        reader_type = cat_file.Reader
        when(cat_file).Reader(...).thenAnswer(lambda *a, **kw: reader_type(*a, idle=.05, **kw))
        self.assertEqual({"HEAD": first}, self.git.resolve_commit_refs(["HEAD"]))
        old = self.reader()
        old.thread.join(2)
        self.assertFalse(old.thread.is_alive())
        self.assertEqual({"HEAD": first}, self.git.resolve_commit_refs(["HEAD"]))
        self.assertNotEqual(old.proc.pid, self.reader().proc.pid)

    def test_idle_expiry_between_lookup_and_submit_replaces_reader(self) -> None:
        self.create_repo()
        first = self.commit("first")
        self.assertEqual({"HEAD": first}, self.git.resolve_commit_refs(["HEAD"]))
        old = self.reader()

        def expire_before_submit(refs):
            old.stop()
            return None  # the batch was never accepted

        when(old).submit(...).thenAnswer(expire_before_submit)
        self.assertEqual({"HEAD": first}, self.git.resolve_commit_refs(["HEAD"]))
        self.assertIsNot(old, self.reader())
        self.assertFalse(old.thread.is_alive())
        self.assertFalse(old.err_thread.is_alive())
        self.assertIsNotNone(old.proc.poll())

    def test_unload_rejects_queries_until_restart(self) -> None:
        self.create_repo()
        first = self.commit("first")
        self.git.resolve_commit_refs(["HEAD"])
        old = self.reader()
        capture = io.StringIO()
        with redirect_stdout(capture):
            cat_file.shutdown()
            cat_file.shutdown()
        self.assertEqual("GitSavvy: " + str(self.repo) + " cat-file service terminated\n", capture.getvalue())
        self.assertFalse(old.thread.is_alive())
        self.assertFalse(old.err_thread.is_alive())
        self.assertTrue(all(pipe.closed for pipe in (old.proc.stdin, old.proc.stdout, old.proc.stderr)))
        with self.assertRaises(GitSavvyError):
            self.git.resolve_commit_refs(["HEAD"])
        cat_file.start()
        self.assertEqual({"HEAD": first}, self.git.resolve_commit_refs(["HEAD"]))

    def test_module_reload_reaps_owned_reader(self) -> None:
        self.create_repo()
        first = self.commit("first")
        self.git.resolve_commit_refs(["HEAD"])
        old = self.reader()
        importlib.reload(cat_file)
        self.assertFalse(old.thread.is_alive())
        self.assertFalse(old.err_thread.is_alive())
        self.assertIsNotNone(old.proc.poll())
        self.assertEqual({"HEAD": first}, self.git.resolve_commit_refs(["HEAD"]))

    def test_line_protocol_rejects_injection_before_start(self) -> None:
        self.create_repo()
        for ref in ("HEAD\nHEAD", "HEAD\rHEAD", "HEAD\0", "a" * 65537):
            with self.assertRaises(GitSavvyError):
                self.git.resolve_commit_refs([ref])
        self.assertEqual(0, len(cat_file._readers))

    def reader(self):
        return cat_file._readers[os.path.normcase(os.path.realpath(self.repo))]

    def create_repo(self, hash_format="sha1", ref_format="reftable") -> None:
        self.run_git("init", "--initial-branch=main", "--object-format=" + hash_format,
                     "--ref-format=" + ref_format)
        self.run_git("config", "user.name", "Cat File Test")
        self.run_git("config", "user.email", "cat-file@example.com")

    def commit(self, message):
        self.run_git("commit", "--allow-empty", "-m", message)
        return self.run_git("rev-parse", "HEAD").strip()

    def run_git(self, *args):
        return subprocess.check_output([self.binary, "-C", str(self.repo), *args],
                                       stderr=subprocess.PIPE).decode()


class TestCatFileTransport(TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="gitsavvy-cat-file-protocol-")
        self.addCleanup(self.temp.cleanup)
        self.python = shutil.which("python3") or shutil.which("python")
        self.assertIsNotNone(self.python)

    def test_idle_expiry_and_inflight_request_not_expired(self) -> None:
        reader = self.script(
            """
            import time

            for line in sys.stdin:
                time.sleep(.15)
                print('a' * 40 + ' commit', flush=True)
            """,
            idle=.05
        )
        capture = io.StringIO()
        with redirect_stdout(capture):
            self.assertEqual([b"a" * 40 + b" commit"], reader.result(reader.submit([b"HEAD"])))
            reader.thread.join(2)
            self.assertFalse(reader.thread.is_alive())
            self.assertIsNone(reader.submit([b"HEAD"]))
            reader.stop()
            reader.join()
        self.assertEqual("GitSavvy: " + self.temp.name + " cat-file service terminated\n", capture.getvalue())

    def test_partial_response_and_eof_are_failures(self) -> None:
        for output in ("", "a" * 40 + " commit", "bad response\n"):
            reader = self.script("""
                sys.stdin.readline()
                sys.stdout.write({output!r})
                sys.stdout.flush()
            """.format(output=output))
            with self.assertRaises(cat_file.ReaderError):
                reader.result(reader.submit([b"HEAD"]))
            self.assertIsNotNone(reader.proc.poll())
            self.assertFalse(reader.err_thread.is_alive())

    def test_timeout_settles_queued_callers_and_bounds_stderr(self) -> None:
        reader = self.script(
            """
            import time

            sys.stdin.readline()
            sys.stderr.write('x' * 1000000)
            sys.stderr.flush()
            time.sleep(30)
            """,
            timeout=.3
        )
        first = reader.submit([b"HEAD"])
        second = reader.submit([b"other"])
        started = time.monotonic()
        with self.assertRaisesRegex(cat_file.ReaderError, "timed out"):
            reader.result(first)
        with self.assertRaises(cat_file.ReaderError):
            second.result(timeout=2)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(b"x" * 65536, reader.stderr)
        self.assertFalse(reader.thread.is_alive())
        self.assertFalse(reader.err_thread.is_alive())

    def test_blocked_stdin_write_is_bounded(self) -> None:
        reader = self.script("""
            import time

            time.sleep(30)
        """, timeout=.3)
        started = time.monotonic()
        with self.assertRaisesRegex(cat_file.ReaderError, "timed out"):
            reader.result(reader.submit([b"a" * 65536]))
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(reader.thread.is_alive())

    def test_shutdown_cancels_inflight_and_queued(self) -> None:
        reader = self.script("""
            import time

            sys.stdin.readline()
            time.sleep(30)
        """)
        futures = [reader.submit([b"HEAD"]) for _ in range(3)]
        reader.stop()
        reader.join()
        for future in futures:
            with self.assertRaises(cat_file.ReaderError):
                future.result(timeout=1)
        self.assertIsNotNone(reader.proc.poll())

    def test_cancelled_queued_request_does_not_break_other_callers(self) -> None:
        reader = self.script(
            """
            import time

            for line in sys.stdin:
                time.sleep(.05)
                print('a' * 40 + ' commit', flush=True)
            """
        )
        first = reader.submit([b"HEAD"])
        cancelled = reader.submit([b"cancelled"])
        self.assertTrue(cancelled.cancel())
        last = reader.submit([b"HEAD"])
        expected = [b"a" * 40 + b" commit"]
        self.assertEqual(expected, reader.result(first))
        self.assertEqual(expected, reader.result(last))
        self.assertTrue(cancelled.cancelled())

    def test_queue_is_bounded(self) -> None:
        reader = self.script("""
            import time

            sys.stdin.readline()
            time.sleep(30)
        """)
        with self.assertRaisesRegex(cat_file.ReaderError, "queue is full"):
            for _ in range(18):
                reader.submit([b"HEAD"])

    def test_partial_writes_stderr_pressure_and_large_serial_batch(self) -> None:
        reader = self.script(
            r"""
            for line in sys.stdin:
                sys.stderr.write('x' * 100000)
                sys.stderr.flush()

                sys.stdout.write('a' * 20)
                sys.stdout.flush()

                sys.stdout.write('a' * 20 + ' commit\n')
                sys.stdout.flush()
            """
        )
        self.assertEqual([b"a" * 40 + b" commit"] * 256,
                         reader.result(reader.submit([b"HEAD"] * 256)))

    def script(self, code, **kwargs):
        command = [self.python, "-u", "-c", "import sys\n" + dedent(code)]
        reader = cat_file.Reader(self.temp.name, command, dict(os.environ), **kwargs)
        self.addCleanup(reader.join)
        self.addCleanup(reader.stop)
        return reader


class RepoGit(GitCommand):
    def __init__(self, repo: str, binary: str) -> None:
        self.root = repo
        self._project_settings = {"git_path": binary}

    def get_repo_path(self):
        return self.root
