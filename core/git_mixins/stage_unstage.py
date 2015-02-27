class StageUnstageMixin():

    def stage_file(self, fpath):
        """
        Given an absolute path or path relative to the repo's root, stage
        the file.
        """
        self.git("add", "-f", "--all", "--", fpath)

    def unstage_file(self, fpath):
        """
        Given an absolute path or path relative to the repo's root, unstage
        the file.
        """
        self.git("reset", "HEAD", fpath)

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
