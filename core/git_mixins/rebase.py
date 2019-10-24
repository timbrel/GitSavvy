import re

from ..exceptions import GitSavvyError
from ...common import util

NEAREST_NODE_PATTERN = re.compile(r'.*\*.*\[(.*?)(?:[\^\~]+[\d]*)+\]')  # https://regexr.com/3kuv3


class NearestBranchMixin(object):
    """ Provide reusable methods for detecting the nearest of a branch relatives """

    def branch_relatives(self, branch):
        """ Geta  list of all relatives from ``git show-branch`` results """
        branch_tree = self.git("show-branch", "--no-color").splitlines()
        util.debug.add_to_log('nearest_branch: found {} show-branch results'.format(
                              len(branch_tree)))
        relatives = []
        for rel in branch_tree:
            match = re.search(NEAREST_NODE_PATTERN, rel)
            if not match:
                continue
            branch_name = match.group(1)
            if branch_name != branch and branch_name not in relatives:
                relatives.append(branch_name)
        return relatives

    def nearest_branch(self, branch, default="master"):
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
