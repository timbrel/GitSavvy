import os
import subprocess
import shutil
from collections import namedtuple

from ..common import log

GitResponse = namedtuple("GitResponse", ["success", "stdout", "stderr"])
FileStatus = namedtuple("FileStatus", ["path", "path_alt", "status", "status_alt"])
IndexedEntry = namedtuple("IndexEntry", [
    "src_path",
    "dst_path",
    "src_mode",
    "dst_mode",
    "src_hash",
    "dst_hash",
    "status",
    "status_score"
    ])
IndexedEntry.__new__.__defaults__ = (None, ) * 8

git_path = None


class GitBetterError(Exception):
    pass


class BaseCommand():

    @property
    def encoding(self):
        return "UTF-8"

    @property
    def git_binary_path(self):
        global git_path
        if not git_path:
            git_path = shutil.which("git")
        return git_path

    @property
    def repo_path(self):
        # The below condition will be true if run from a WindowCommand and false from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        repo_path = view.settings().get("git_better.repo_path")

        if not repo_path:
            working_dir = os.path.dirname(self.file_path)
            cmd = self.git("rev-parse", "--show-toplevel", working_dir=working_dir)
            repo_path = cmd.stdout.strip()
            view.settings().set("git_better.repo_path", repo_path)

        return repo_path

    @property
    def file_path(self):
        # The below condition will be true if run from a WindowCommand and false from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        fpath = view.settings().get("git_better.file_path")

        if not fpath:
            fpath = view.file_name()
            view.settings().set("git_better.file_path", fpath)

        return fpath

    def git(self, *args, stdin=None, working_dir=None):
        command = (self.git_binary_path, ) + tuple(arg for arg in args if arg)
        log.info("-- " + " ".join(command))

        try:
            p = subprocess.Popen(command,
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 cwd=working_dir or self.repo_path)
            stdout, stderr = p.communicate(stdin.encode(encoding="UTF-8") if stdin else None)
            stdout, stderr = stdout.decode(), stderr.decode()

        except Exception as e:
            raise GitBetterError("Git command failed: {}".format(e))

        log.info(stdout)

        return GitResponse(p.returncode == 0, stdout, stderr)

    def get_status(self):
        cmd = self.git("status", "--porcelain", "-z")
        if not cmd.success:
            return None

        porcelain_entries = cmd.stdout.split("\x00").__iter__()
        entries = []

        for entry in porcelain_entries:
            if not entry:
                continue
            status = entry[0]
            status_alt = entry[1].strip() or None
            path = entry[3:]
            path_alt = porcelain_entries.__next__() if status == "R" else None
            entries.append(FileStatus(path, path_alt, status, status_alt))

        return entries

    def _get_indexed_entry(self, raw_entry):
        parts = [part for part in raw_entry.split("\x00") if part]
        if len(parts) == 2:
            meta_data, src_path = parts
            dst_path = src_path
        elif len(parts) == 3:
            meta_data, src_path, dst_path = parts

        src_mode, dst_mode, src_hash, dst_hash, status = meta_data

        status_score = status[1:]
        if status_score:
            status = status[0]

        return IndexedEntry(
            src_path,
            dst_path,
            src_mode,
            dst_mode,
            src_hash,
            dst_hash,
            status,
            status_score
            )

    def get_indexed(self):
        cmd = self.git("diff-index", "-z", "--cached", "HEAD")
        if not cmd.success:
            return None

        entries = cmd.stdout.split(":")

        return [self._get_indexed_entry(raw_entry) for raw_entry in entries if raw_entry]
