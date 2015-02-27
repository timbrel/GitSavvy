class CheckoutDiscardMixin():

    def discard_all_unstaged(self):
        """
        Any changes that are not staged or committed will be reverted
        to their state in HEAD.  Any new files will be deleted.
        """
        self.git("clean", "-df")
        self.git("checkout", "--", ".")

    def checkout_file(self, fpath):
        """
        Given an absolute path or path relative to the repo's root, discard
        any changes made to the file and revert it in the working directory
        to the state it is in HEAD.
        """
        self.git("checkout", "--", fpath)
