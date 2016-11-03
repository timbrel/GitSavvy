class AssertionsMixin:

    def assert_git_status(self, status):
        """
        Assertion of the current git status. `status` should be a list of 4 intergers:
        The lengths of the staged, unstaged, untracked and conflicted entries.
        """
        self.assertEqual(
            [len(x) for x in self.sort_status_entries(self.get_status())],
            status)
