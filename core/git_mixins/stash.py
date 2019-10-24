import re
from collections import namedtuple

Stash = namedtuple("Stash", ("id", "description"))


class StashMixin():

    def get_stashes(self):
        """
        Return a list of stashes in the repo.
        """
        stdout = self.git("stash", "list")
        stashes = []
        for entry in stdout.split("\n"):
            if not entry:
                continue
            num, _, description = re.match("^stash@\\{(\\d+)}: (.*?: )?(.*)", entry).groups()
            stashes.append(Stash(num, description))
        return stashes

    def show_stash(self, id):
        stash_name = "stash@{{{}}}".format(id)
        return self.git("stash", "show", "--no-color", "-p", stash_name)

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
        self.git("stash", "save", "-k", "-u" if include_untracked else None, description)

    def drop_stash(self, id):
        """
        Drop stash with provided id.
        """
        return self.git("stash", "drop", "stash@{{{}}}".format(id))
