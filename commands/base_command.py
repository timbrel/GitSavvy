import os
import subprocess
import shutil
from collections import namedtuple

import sublime

from ..common import log

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

    """
    Base class for all Sublime commands that interact with git.
    """

    @property
    def encoding(self):
        return "UTF-8"

    @property
    def git_binary_path(self):
        """
        Return the path to the available `git` binary.
        """

        global git_path
        if not git_path:
            git_path = shutil.which("git")
        return git_path

    @property
    def repo_path(self):
        """
        Return the absolute path to the git repo that contains the file that this
        view interacts with.  Like `file_path`, this can be overridden by setting
        the view's `git_better.repo_path` setting.
        """
        # The below condition will be true if run from a WindowCommand and false from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        repo_path = view.settings().get("git_better.repo_path")

        if not repo_path:
            working_dir = os.path.dirname(self.file_path)
            stdout = self.git("rev-parse", "--show-toplevel", working_dir=working_dir)
            repo_path = stdout.strip()
            view.settings().set("git_better.repo_path", repo_path)

        return repo_path

    @property
    def file_path(self):
        """
        Return the absolute path to the file this view interacts with. In most
        cases, this will be the open file.  However, for views with special
        functionality, this default behavior can be overridden by setting the
        view's `git_better.file_path` setting.
        """
        # The below condition will be true if run from a WindowCommand and false
        # from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        fpath = view.settings().get("git_better.file_path")

        if not fpath:
            fpath = view.file_name()
            view.settings().set("git_better.file_path", fpath)

        return fpath

    def git(self, *args, stdin=None, working_dir=None):
        """
        Run the git command specified in `*args` and return the output
        of the git command as a string.

        If stdin is provided, it should be a string and will be piped to
        the git process.  If `working_dir` is provided, set this as the
        current working directory for the git process; otherwise,
        the `repo_path` value will be used.
        """
        command = (self.git_binary_path, ) + tuple(arg for arg in args if arg)
        log.info("-- " + " ".join(command))

        def raise_error(msg):
            sublime.status_message(
                "Failed to run `git {}`. See console for details.".format(command[1])
            )
            raise GitBetterError(msg)

        try:
            p = subprocess.Popen(command,
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 cwd=working_dir or self.repo_path)
            stdout, stderr = p.communicate(stdin.encode(encoding="UTF-8") if stdin else None)
            stdout, stderr = stdout.decode(), stderr.decode()

        except Exception as e:
            raise_error(e)

        if not p.returncode == 0:
            raise_error("`git {}` failed with following output:\n{}".format(
                command[1], stderr
            ))

        log.info(stdout)

        return stdout

    def get_status(self):
        """
        Return a list of FileStatus objects.  These objects correspond
        to all files that are 1) staged, 2) modified, 3) new, or 4)
        deleted, as well as additional status information that can
        occur mid-merge.
        """
        stdout = self.git("status", "--porcelain", "-z")

        porcelain_entries = stdout.split("\x00").__iter__()
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
        """
        Parse a diff-index entry into an IndexEntry object.  Each input entry
        will have either three NUL-separated fields if the file has been renamed,
        and two if it has not been renamed.

        The first field will always contain the meta_data related to the field,
        which includes: original and new file-system mode, the original and new
        git object-hashes, and a status letter indicating the nature of the
        change.
        """
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
        """
        Return a list of `IndexEntry`s.  Each entry in the list corresponds
        to a file that 1) is in HEAD, and 2) is staged with changes.
        """
        # Return an entry for each file with a difference between HEAD and its
        # counterpart in the current index.  Entries will be separated by `:` and
        # each field will be separated by NUL charachters.
        stdout = self.git("diff-index", "-z", "--cached", "HEAD")

        return [
            self._get_indexed_entry(raw_entry)
            for raw_entry in stdout.split(":")
            if raw_entry
        ]
