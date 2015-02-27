import os
import shutil

import sublime

git_path = None


class FileAndRepo():

    """
    Base class that provides active git path, file path, and repo path.
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
        git_path = (git_path or
                    sublime.load_settings("GitSavvy.sublime-settings").get("gitPath") or
                    shutil.which("git")
                    )

        if not git_path:
            msg = ("Your Git binary cannot be found.  If it is installed, add it "
                   "to your PATH environment variable, or add a `gitPath` setting "
                   "in the `User/GitSavvy.sublime-settings` file.")
            sublime.error_message(msg)
            raise ValueError("Git binary not found.")

        return git_path

    @property
    def repo_path(self):
        """
        Return the absolute path to the git repo that contains the file that this
        view interacts with.  Like `file_path`, this can be overridden by setting
        the view's `git_savvy.repo_path` setting.
        """
        # The below condition will be true if run from a WindowCommand and false
        # from a TextCommand.
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

    def get_rel_path(self, abs_path=None):
        """
        Return the file path relative to the repo root.
        """
        path = abs_path or self.file_path
        return os.path.relpath(path, start=self.repo_path)
