from GitSavvy.core.git_command import mixin_base


class StageUnstageMixin(mixin_base):

    def stage_file(self, *fpath, force=True):
        # type: (str, bool) -> None
        """
        Given an absolute path or path relative to the repo's root, stage
        the file.
        """
        # Ensure we don't run "add --all" without any paths which
        # would add everything
        if not fpath:
            return

        self.git(
            "add",
            "-f" if force else None,
            "--all",
            "--",
            *fpath
        )

    def unstage_file(self, *fpath):
        # type: (str) -> None
        """
        Given an absolute path or path relative to the repo's root, unstage
        the file.
        """
        # Ensure we don't run "reset" without any paths which
        # would unstage everything
        if not fpath:
            return

        self.git("reset", "HEAD", "--", *fpath)

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

    def intent_to_add(self, *file_paths: str) -> None:
        self.git("add", "--intent-to-add", "--", *file_paths)

    def undo_intent_to_add(self, *file_paths: str) -> None:
        # Ensure we don't run "reset" without any paths which
        # would unstage everything
        if not file_paths:
            return

        self.git("reset", "--", *file_paths)
