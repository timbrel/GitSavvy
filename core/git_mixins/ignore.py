import os


class IgnoreMixin():

    def add_ignore(self, path_or_pattern):
        """
        Add the provided relative path or pattern to the repo's `.gitignore` file.
        """
        with open(os.path.join(self.repo_path, ".gitignore"), "at") as ignore_file:
            ignore_file.write(os.linesep + "# added by GitSavvy" + os.linesep + path_or_pattern + os.linesep)
