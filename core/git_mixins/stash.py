import re
from collections import namedtuple

Stash = namedtuple("Stash", ("id", "description"))


class StashMixin():

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
