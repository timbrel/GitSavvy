import re

from ..exceptions import GitSavvyError
from ...common import util


MYPY = False
if MYPY:
    from typing import List


EXTRACT_BRANCH_NAME = re.compile(r'^[^[]+\[(.*?)(?:[\^\~]+[\d]*)*\]')


class NearestBranchMixin(object):
    """ Provide reusable methods for detecting the nearest of a branch relatives """

    def branch_relatives(self, branch):
        # type: (str) -> List[str]
        """ Get list of all relatives from ``git show-branch`` results """
        output = self.git("show-branch", "--no-color")

        prelude, body = re.split(r'^-+$', output, flags=re.M)

        match = re.search(r'^(\s+)\*', prelude, re.M)
        if not match:
            print("branch {} not found in header information".format(branch))
            return []

        branch_column = len(match.group(1))
        relatives = []  # type: List[str]
        for line in filter(None, body.splitlines()):  # type: str
            if line[branch_column] != ' ':
                match = EXTRACT_BRANCH_NAME.match(line)
                if match:
                    branch_name = match.group(1)
                    if branch_name != branch and branch_name not in relatives:
                        relatives.append(branch_name)

        return relatives

    def nearest_branch(self, branch, default="master"):
        # type: (str, str) -> str
        """
        Find the nearest commit in current branch history that exists
        on a different branch and return that branch name.

        We filter these branches through a list of known ancestors which have
        an initial branch point with current branch, and pick the first one
        that matches both.

        If no such branch is found, returns the given default ("master" if not
        specified).

        Solution snagged from:
        http://stackoverflow.com/a/17843908/484127
        http://stackoverflow.com/questions/1527234
        """
        try:
            relatives = self.branch_relatives(branch)
        except GitSavvyError:
            return default

        if not relatives:
            util.debug.add_to_log('nearest_branch: No relatives found. '
                                  'Possibly on a root branch!')
            return default

        util.debug.add_to_log('nearest_branch: found {} relatives: {}'.format(
                              len(relatives), relatives))

        return relatives[0]
