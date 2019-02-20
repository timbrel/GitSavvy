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

    def _get_branch_status_components(self, lines):
        """
        Return a tuple of:

          0) boolean indicating whether repo is in detached state
          1) active branch name
          2) remote branch name
          3) boolean indicating whether branch is clean
          4) # commits ahead of remote
          5) # commits behind of remote
          6) boolean indicating whether the remote branch is gone
        """

        first_line, *addl_lines = lines
        # Any additional lines will mean files have changed or are untracked.
        clean = len(addl_lines) == 0

        if first_line.startswith("## HEAD (no branch)"):
            return True, None, None, clean, None, None, False

        if (
            first_line.startswith("## No commits yet on ")
            # older git used these
            or first_line.startswith("## Initial commit on ")
        ):
            first_line = first_line[:3] + first_line[21:]

        valid_punctuation = "".join(c for c in string.punctuation if c not in "~^:?*[\\")
        branch_pattern = "[A-Za-z0-9" + re.escape(valid_punctuation) + "\u263a-\U0001f645]+?"
        branch_suffix = r"( \[((ahead (\d+))(, )?)?(behind (\d+))?(gone)?\])?)"
        short_status_pattern = "## (" + branch_pattern + r")(\.\.\.(" + branch_pattern + ")" + branch_suffix + "?$"
        status_match = re.match(short_status_pattern, first_line)

        if not status_match:
            return False, None if clean else addl_lines[0], None, clean, None, None, False

        branch, _, remote, _, _, _, ahead, _, _, behind, gone = status_match.groups()

        return False, branch, remote, clean, ahead, behind, bool(gone)

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
        lines = self._get_status()
        branch_status = self._get_branch_status_components(lines)
        return self._format_branch_status(branch_status, delim)

    def _format_branch_status(self, branch_status, delim=None):
        detached, branch, remote, clean, ahead, behind, gone = branch_status

        secondary = []

        if detached:
            status = "HEAD is in a detached state."

        else:
            tracking = " tracking `{}`".format(remote)
            status = "On branch `{}`{}.".format(branch, tracking if remote else "")

            if ahead and behind:
                secondary.append("You're ahead by {} and behind by {}.".format(ahead, behind))
            elif ahead:
                secondary.append("You're ahead by {}.".format(ahead))
            elif behind:
                secondary.append("You're behind by {}.".format(behind))
            elif gone:
                secondary.append("The remote branch is gone.")

        if self.in_merge():
            secondary.append("Merging {}.".format(self.merge_head()))

        if self.in_rebase():
            secondary.append("Rebasing {}.".format(self.rebase_branch_name()))

        if delim:
            return delim.join([status] + secondary) if secondary else status
        return status, secondary

    def get_branch_status_short(self):
        if self.in_rebase():
            return "(no branch, rebasing {})".format(self.rebase_branch_name())

        lines = self._get_status()
        branch_status = self._get_branch_status_components(lines)
        return self._format_branch_status_short(branch_status)

    def _format_branch_status_short(self, branch_status):
        detached, branch, remote, clean, ahead, behind, gone = branch_status

        dirty = "" if clean else "*"

        if detached:
            return "DETACHED" + dirty

        output = branch + dirty

        if ahead:
            output += "+" + ahead
        if behind:
            output += "-" + behind

        merge_head = self.merge_head() if self.in_merge() else ""
        return output if not merge_head else output + " (merging {})".format(merge_head)

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
