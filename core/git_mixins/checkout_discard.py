from GitSavvy.core.git_command import mixin_base


class CheckoutDiscardMixin(mixin_base):

    def discard_all_unstaged(self):
        """
        Any changes that are not staged or committed will be reverted
        to their state in HEAD.  Any new files will be deleted.
        """
        self.git("clean", "-df")
        self.git("checkout", "--", ".")

    def discard_untracked_file(self, *fpaths):
        """
        Given a list of absolute paths or paths relative to the repo's root,
        remove the file or directory from the working tree.
        """
        self.git("clean", "-df", "--", *fpaths)

    def checkout_file(self, *fpaths):
        """
        Given a list of absolute paths or paths relative to the repo's root,
        discard any changes made to the file and revert it in the working
        directory to the state it is in HEAD.
        """
        self.git("checkout", "--", *fpaths)

    def checkout_ref(self, ref, fpath=None):
        """
        Given a ref (local branch, remote branch, tag, etc), check it out.
        """
        if fpath:
            self.git("checkout", ref, "--", fpath)
        else:
            self.git("checkout", ref)
