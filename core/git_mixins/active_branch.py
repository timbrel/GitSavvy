import re
import string


class ActiveBranchMixin():

    def get_current_branch_name(self):
        """
        Return the name of the last checkout-out branch.
        """
        stdout = self.git("branch", "--no-color")
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
          7) boolean indicating whether the remote branch is gone
        """
        stdout = self.git("status", "-b", "--porcelain").strip()

        first_line, *addl_lines = stdout.split("\n", 2)
        # Any additional lines will mean files have changed or are untracked.
        clean = len(addl_lines) == 0

        if first_line.startswith("## HEAD (no branch)"):
            return True, False, None, None, clean, None, None, False

        if first_line.startswith("## Initial commit on "):
            return False, True, first_line[21:], clean, None, None, None, False

        valid_punctuation = "".join(c for c in string.punctuation if c not in "~^:?*[\\")
        branch_pattern = "[A-Za-z0-9" + re.escape(valid_punctuation) + "\u263a-\U0001f645]+?"
        branch_suffix = "( \[((ahead (\d+))(, )?)?(behind (\d+))?(gone)?\])?)"
        short_status_pattern = "## (" + branch_pattern + ")(\.\.\.(" + branch_pattern + ")" + branch_suffix + "?$"
        status_match = re.match(short_status_pattern, first_line)

        if not status_match:
            return False, False, None if clean else addl_lines[0], None, clean, None, None, False

        branch, _, remote, _, _, _, ahead, _, _, behind, gone = status_match.groups()

        return False, False, branch, remote, clean, ahead, behind, bool(gone)

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
        detached, initial, branch, remote, clean, ahead, behind, gone = \
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
            elif gone:
                secondary = "The remote branch is gone."

        if delim:
            return delim.join((status, secondary)) if secondary else status
        return status, secondary

    def get_branch_status_short(self):

        if self.in_rebase():
            return "(no branch, rebasing {})".format(self.rebase_branch_name())

        detached, initial, branch, remote, clean, ahead, behind, gone = \
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
        stdout = self.git(
            "log",
            "-n 1",
            "--pretty=format:%h %s",
            "--abbrev-commit",
            throw_on_stderr=False
        ).strip()

        return stdout or "No commits yet."

    def get_upstream_for_active_branch(self):
        """
        Return ref for remote tracking branch.
        """
        return self.git("rev-parse", "--abbrev-ref", "--symbolic-full-name",
                        "@{u}", throw_on_stderr=False).strip()

    def get_active_remote_branch(self):
        """
        Return named tuple of the upstream for active branch.
        """
        upstream = self.get_upstream_for_active_branch()
        for branch in self.get_branches():
            if branch.name_with_remote == upstream:
                return branch
        return None
