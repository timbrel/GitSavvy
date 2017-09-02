import difflib
import re

from ..exceptions import GitSavvyError
from ...common import util

NEAREST_NODE_PATTERN = re.compile(r'.*\*.*\[(.*?)(?:(?:[\^\~]+[\d]*){1})\]')  # http://regexr.com/3gm03


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
            branch_name = match.groups()[0]
            if branch_name != branch and branch_name not in relatives:
                relatives.append(branch_name)
        return relatives

    def _nearest_from_relatives(self, relatives, branch):
        """
        Find the nearest branch from the "branch-out nodes" of all relatives.
        """
        util.debug.add_to_log('nearest_branch: filtering branches that share branch-out nodes')
        diff = difflib.Differ()
        branch_commits = self.git("rev-list", "--first-parent", branch).splitlines()
        max_revisions = 100
        for relative in relatives:
            util.debug.add_to_log('nearest_branch: Getting common commits with {}'.format(relative))
            relative_commits = self.git("rev-list", "-{}".format(max_revisions),
                                        "--first-parent", relative).splitlines()

            # Enumerate over branch vs relative commit hashes and look for a common one
            common = None
            for line in diff.compare(branch_commits, relative_commits):
                if not line.startswith(' '):
                    util.debug.add_to_log('nearest_branch: commit differs {}'.format(line))
                    continue
                common = line.strip()
                util.debug.add_to_log('nearest_branch: found common commit {}'.format(common))
                break

            if not common:
                util.debug.add_to_log('nearest_branch: No common commit found with {}'.format(relative))
                continue

            # Found common "branch-out node", get reachable branches for commit
            branches = self.git("branch", "--contains", common, "--merged").splitlines()
            cleaned_branch_names = [b[2:].strip() for b in branches]
            util.debug.add_to_log('nearest_branch: got valid branches {}'.format(cleaned_branch_names))
            if relative in cleaned_branch_names:
                return relative

        return None

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

        nearest = self._nearest_from_relatives(relatives, branch)
        if not nearest:
            util.debug.add_to_log('nearest_branch: No valid nearest found. '
                                  'Possibly on a root / detached branch!')
            return default

        util.debug.add_to_log('nearest_branch: Found best candidate {}'.format(nearest))
        # if same as branch, return default instead
        if branch == nearest:
            return default
        return nearest
