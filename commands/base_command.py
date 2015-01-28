import os
import re
import subprocess
import shutil
from collections import namedtuple, OrderedDict
from webbrowser import open as open_in_browser

import sublime

from ..common import log

FileStatus = namedtuple("FileStatus", ["path", "path_alt", "index_status", "working_status"])
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


class GitGadgetError(Exception):
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
        the view's `git_gadget.repo_path` setting.
        """
        # The below condition will be true if run from a WindowCommand and false from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        repo_path = view.settings().get("git_gadget.repo_path")

        if not repo_path:
            working_dir = os.path.dirname(self.file_path)
            stdout = self.git("rev-parse", "--show-toplevel", working_dir=working_dir)
            repo_path = stdout.strip()
            view.settings().set("git_gadget.repo_path", repo_path)

        return repo_path

    @property
    def file_path(self):
        """
        Return the absolute path to the file this view interacts with. In most
        cases, this will be the open file.  However, for views with special
        functionality, this default behavior can be overridden by setting the
        view's `git_gadget.file_path` setting.
        """
        # The below condition will be true if run from a WindowCommand and false
        # from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        fpath = view.settings().get("git_gadget.file_path")

        if not fpath:
            fpath = view.file_name()
            view.settings().set("git_gadget.file_path", fpath)

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
            raise GitGadgetError(msg)

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
            raise_error("`git {}` failed with following output:\n{}\n".format(
                command[1], stdout, stderr
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
            index_status = entry[0]
            working_status = entry[1].strip() or None
            path = entry[3:]
            path_alt = porcelain_entries.__next__() if index_status == "R" else None
            entries.append(FileStatus(path, path_alt, index_status, working_status))

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

    def get_read_only_view(self, name):
        """
        Return a read-only diff view.  If one already exists, return that.
        Otherwise, return a new view.
        """
        view_type = "git_gadget.{}_view".format(name)

        for view in self.window.views():
            if view.settings().get(view_type) == True:
                break
        else:
            view = self.window.new_file()
            view.settings().set(view_type, True)
            view.set_scratch(True)
            view.set_read_only(True)

        return view

    def stage_file(self, fpath):
        """
        Given an absolute path or path relative to the repo's root, stage
        the file.
        """
        self.git("add", "-f", "--", fpath)

    def unstage_file(self, fpath):
        """
        Given an absolute path or path relative to the repo's root, unstage
        the file.
        """
        self.git("reset", "HEAD", fpath)

    def checkout_file(self, fpath):
        """
        Given an absolute path or path relative to the repo's root, discard
        any changes made to the file and revert it in the working directory
        to the state it is in HEAD.
        """
        self.git("checkout", "--", fpath)

    def open_file_on_remote(self, fpath):
        """
        Assume the remote git repo is GitHub and open the URL corresponding
        to the provided file at path `fpath` at HEAD.
        """
        default_name, default_remote_url = self.get_remotes().popitem(last=False)

        if default_remote_url.startswith("git@"):
            url = default_remote_url.replace(":", "/").replace("git@", "http://")[:-4]
        elif default_remote_url.startswith("http"):
            url = default_remote_url[:-4]
        else:
            return

        url += "/blob/{commit_hash}/{path}".format(
            commit_hash=self.get_commit_hash_for_head(),
            path=fpath
        )

        open_in_browser(url)

    def get_commit_hash_for_head(self):
        """
        Get the SHA1 commit hash for the commit at HEAD.
        """
        return self.git("rev-parse", "HEAD").strip()

    def get_remotes(self):
        """
        Get a list of remotes, provided as tuples of remote name and remote
        url/resource.
        """
        entries = self.git("remote", "-v").splitlines()
        print(entries)
        return OrderedDict(re.match("([a-zA-Z_-]+)\t([^ ]+)", entry).groups() for entry in entries)

    def add_all_tracked_files(self):
        """
        Add to index all files that have been deleted or modified, but not
        those that have been created.
        """
        return self.git("add", "-u")

    def add_all_files(self):
        """
        Add to index all files that have been deleted, modified, or
        created.
        """
        return self.git("add", "-A")

    def unstage_all_files(self):
        """
        Remove all staged files from the index.
        """
        return self.git("reset")

    def discard_all_unstaged(self):
        """
        Any changes that are not staged or committed will be reverted
        to their state in HEAD.  Any new files will be deleted.
        """
        self.git("clean", "-df")
        self.git("checkout", "--", ".")
