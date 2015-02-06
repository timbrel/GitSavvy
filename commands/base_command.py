import os
import re
import subprocess
import shutil
from collections import namedtuple, OrderedDict

import sublime

from ..common import log, github

Stash = namedtuple("Stash", ["id", "description"])
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


class GitSavvyError(Exception):
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
        the view's `git_savvy.repo_path` setting.
        """
        # The below condition will be true if run from a WindowCommand and false from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        repo_path = view.settings().get("git_savvy.repo_path")

        if not repo_path:
            file_path = self.file_path
            working_dir = file_path and os.path.dirname(self.file_path)
            if not working_dir:
                window_folders = sublime.active_window().folders()
                working_dir = window_folders[0] if window_folders else None
            stdout = self.git("rev-parse", "--show-toplevel", working_dir=working_dir)
            repo_path = stdout.strip()
            view.settings().set("git_savvy.repo_path", repo_path)

        return repo_path

    @property
    def file_path(self):
        """
        Return the absolute path to the file this view interacts with. In most
        cases, this will be the open file.  However, for views with special
        functionality, this default behavior can be overridden by setting the
        view's `git_savvy.file_path` setting.
        """
        # The below condition will be true if run from a WindowCommand and false
        # from a TextCommand.
        view = self.window.active_view() if hasattr(self, "window") else self.view
        fpath = view.settings().get("git_savvy.file_path")

        if not fpath:
            fpath = view.file_name()
            view.settings().set("git_savvy.file_path", fpath)

        return fpath

    def git(self, *args, stdin=None, working_dir=None, show_panel=False):
        """
        Run the git command specified in `*args` and return the output
        of the git command as a string.

        If stdin is provided, it should be a string and will be piped to
        the git process.  If `working_dir` is provided, set this as the
        current working directory for the git process; otherwise,
        the `repo_path` value will be used.
        """
        command = (self.git_binary_path, ) + tuple(arg for arg in args if arg)
        command_str = " ".join(command)

        def raise_error(msg):
            if type(msg) == str and "fatal: Not a git repository" in msg:
                sublime.set_timeout_async(
                    lambda: sublime.active_window().run_command("gs_offer_init"))
                return

            sublime.status_message(
                "Failed to run `git {}`. See log for details.".format(command[1])
            )
            log.panel(msg)
            raise GitSavvyError(msg)

        try:
            p = subprocess.Popen(command,
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE,
                                 cwd=working_dir or self.repo_path,
                                 env=os.environ)
            stdout, stderr = p.communicate(stdin.encode(encoding="UTF-8") if stdin else None)
            stdout, stderr = stdout.decode(), stderr.decode()

        except Exception as e:
            raise_error(e)

        if not p.returncode == 0:
            raise_error("`{}` failed with following output:\n{}\n{}".format(
                command_str, stdout, stderr
            ))

        if show_panel:
            log.panel("> {}\n{}\n{}".format(command_str, stdout, stderr))

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

    def get_branch_status(self):
        """
        Return a string that gives:

          1) the name of the active branch
          2) the status of the active local branch
             compared to its remote counterpart.

        If no remote or tracking branch is defined, do not include remote-data.
        If HEAD is detached, provide that status instead.
        """
        stdout = self.git("status")
        line_one, line_two, *_ = stdout.split("\n")

        if "HEAD detached at " in line_one:
            return ("HEAD is in a detached state at" +
                    line_one.strip().replace("HEAD detached at ", ""))

        branch_name = line_one.strip().replace("On branch ", "")
        remote_status = line_two.strip().replace("Your branch is ", "").replace("'", "`")

        return "Local branch `{}`, {}".format(branch_name, remote_status)

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
        Create and return a read-only view.
        """
        window = self.window if hasattr(self, "window") else self.view.window()
        view = window.new_file()
        view.settings().set("git_savvy.{}_view".format(name), True)
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

    def open_file_on_remote(self, fpath, start_line=None, end_line=None):
        """
        Assume the remote git repo is GitHub and open the URL corresponding
        to the provided file at path `fpath` at HEAD.
        """
        default_name, default_remote_url = self.get_remotes().popitem(last=False)

        github.open_file_in_browser(
            fpath,
            default_remote_url,
            self.get_commit_hash_for_head(),
            start_line=start_line,
            end_line=end_line
        )

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

    def fetch(self, remote=None):
        """
        If provided, fetch all changes from `remote`.  Otherwise, fetch
        changes from all remotes.
        """
        self.git("fetch", remote)

    def get_remote_branches(self):
        """
        Return a list of all known branches on remotes.
        """
        stdout = self.git("branch", "-r", "--no-color", "--no-column")
        return [branch.strip() for branch in stdout.split("\n") if branch]

    def get_current_branch_name(self):
        """
        Return the name of the last checkout-out branch.
        """
        stdout = self.git("branch")
        try:
            correct_line = next(line for line in stdout.split("\n") if line.startswith("*"))
            return correct_line[2:]
        except StopIteration:
            return None

    def pull(self, remote=None, branch=None):
        """
        Pull from the specified remote and branch if provided, otherwise
        perform default `git pull`.
        """
        self.git("pull", remote, branch)

    def push(self, remote=None, branch=None):
        """
        Push to the specified remote and branch if provided, otherwise
        perform default `git push`.
        """
        self.git("push", remote, branch)

    def add_ignore(self, path_or_pattern):
        """
        Add the provided relative path or pattern to the repo's `.gitignore` file.
        """
        with open(os.path.join(self.repo_path, ".gitignore"), "at") as ignore_file:
            ignore_file.write(os.linesep + "# added by GitSavvy" + os.linesep + path_or_pattern + os.linesep)

    def get_stashes(self):
        """
        Return a list of stashes in the repo.
        """
        stdout = self.git("stash", "list")
        return [
            Stash(*re.match("^stash@\{(\d+)}: .*?: (.*)", entry).groups())
            for entry in stdout.split("\n") if entry
        ]

    def apply_stash(self, id):
        """
        Apply stash with provided id.
        """
        self.git("stash", "apply", "stash@{{{}}}".format(id))

    def pop_stash(self, id):
        """
        Pop stash with provided id.
        """
        self.git("stash", "pop", "stash@{{{}}}".format(id))

    def create_stash(self, description, include_untracked=False):
        """
        Create stash with provided description from working files.
        """
        self.git("stash", "save", "-u" if include_untracked else None, description)

    def drop_stash(self, id):
        """
        Drop stash with provided id.
        """
        self.git("stash", "drop", "stash@{{{}}}".format(id))
