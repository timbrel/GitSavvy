import re
import string


class ActiveBranchMixin():

    def get_current_branch_name(self):
        """
        Return the name of the last checkout-out branch.
        """
        stdout = self.git("branch")
        try:
            correct_line = next(line for line in stdout.split("\n") if line.startswith("*"))
            return correct_line[2:]
        except StopIteration:
            return None

    def _get_branch_status_components(self):
        """
        Return a tuple of:

          0) boolean indicating whether repo is in detached state
          1) boolean indicating whether this is initial commit
          2) active branch name
          3) remote branch name
          4) boolean indicating whether branch is clean
          5) # commits ahead of remote
          6) # commits behind of remote
        """
        stdout = self.git("status", "-b", "--porcelain").strip()

        first_line, *addl_lines = stdout.split("\n", 2)
        # Any additional lines will mean files have changed or are untracked.
        clean = len(addl_lines) == 0

        if first_line.startswith("## HEAD (no branch)"):
            return True, False, None, None, clean, None, None

        if first_line.startswith("## Initial commit on "):
            return False, True, first_line[21:], clean, None, None, None

        valid_punctuation = "".join(c for c in string.punctuation if c not in "~^:?*[\\")
        branch_pattern = "[A-Za-z0-9" + re.escape(valid_punctuation) + "]+?"
        short_status_pattern = "## (" + branch_pattern + ")(\.\.\.(" + branch_pattern + ")( \[((ahead (\d+))(, )?)?(behind (\d+))?\])?)?$"
        status_match = re.match(short_status_pattern, first_line)

        if not status_match:
            return False, False, addl_lines[0], None, clean, None, None

        branch, _, remote, _, _, _, ahead, _, _, behind = status_match.groups()

        return False, False, branch, remote, clean, ahead, behind

    def get_branch_status(self, delim=None):
        """
        Return a tuple of:

          1) the name of the active branch
          2) the status of the active local branch
             compared to its remote counterpart.

        If no remote or tracking branch is defined, do not include remote-data.
        If HEAD is detached, provide that status instead.

        If a delimeter is provided, join tuple components with it, and return
        that value.
        """
        detached, initial, branch, remote, clean, ahead, behind = \
            self._get_branch_status_components()

        secondary = ""

        if detached:
            status = "HEAD is in a detached state."

        elif initial:
            status = "Initial commit on `{}`.".format(branch)

        else:
            tracking = " tracking `{}`".format(remote)
            status = "On branch `{}`{}.".format(branch, tracking if remote else "")

            if ahead and behind:
                secondary = "You're ahead by {} and behind by {}.".format(ahead, behind)
            elif ahead:
                secondary = "You're ahead by {}.".format(ahead)
            elif behind:
                secondary = "You're behind by {}.".format(behind)

        if delim:
            return delim.join((status, secondary)) if secondary else status
        return status, secondary

    def get_branch_status_short(self):
        detached, initial, branch, remote, clean, ahead, behind = \
            self._get_branch_status_components()

        dirty = "" if clean else "*"

        if detached:
            return "DETACHED" + dirty

        output = branch + dirty

        if ahead:
            output += "+" + ahead
        if behind:
            output += "-" + behind

        return output

    def get_commit_hash_for_head(self):
        """
        Get the SHA1 commit hash for the commit at HEAD.
        """
        return self.git("rev-parse", "HEAD").strip()

    def get_latest_commit_msg_for_head(self):
        """
        Get last commit msg for the commit at HEAD.
        """
        return self.git("log", "-n 1", "--pretty=format:%h %s", "--abbrev-commit").strip()
