import re

from GitSavvy.core.git_command import mixin_base
from GitSavvy.core.utils import cache_in_store_as

from typing import List, NamedTuple, Union
StashId = Union[str, int]


class Stash(NamedTuple):
    id: str
    description: str


class StashMixin(mixin_base):

    @cache_in_store_as("stashes")
    def get_stashes(self):
        # type: () -> List[Stash]
        """
        Return a list of stashes in the repo.
        """
        stdout = self.git("stash", "list")
        stashes = []
        for entry in stdout.split("\n"):
            if not entry:
                continue
            match = re.match("^stash@\\{(\\d+)}: (.*?: )?(.*)", entry)
            assert match
            num, _, description = match.groups()
            stashes.append(Stash(num, description))

        return stashes

    def show_stash(self, id):
        # type: (StashId) -> str
        show_diffstat = self.savvy_settings.get("show_diffstat", True)
        stash_name = "stash@{{{}}}".format(id)
        return self.git("stash", "show", "--no-color", "--stat" if show_diffstat else None, "-p", stash_name)

    def apply_stash(self, id):
        # type: (StashId) -> None
        """
        Apply stash with provided id.
        """
        self.git("stash", "apply", "stash@{{{}}}".format(id))

    def pop_stash(self, id):
        # type: (StashId) -> None
        """
        Pop stash with provided id.
        """
        self.git("stash", "pop", "stash@{{{}}}".format(id))

    def create_stash(self, description, include_untracked=False):
        # type: (str, bool) -> None
        """
        Create stash with provided description from working files.
        """
        self.git("stash", "save", "-k", "-u" if include_untracked else None, description)

    def drop_stash(self, id):
        # type: (StashId) -> str
        """
        Drop stash with provided id.
        """
        return self.git("stash", "drop", "stash@{{{}}}".format(id))
