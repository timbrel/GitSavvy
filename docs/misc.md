# Miscellaneous features

## `git: init`

This command will initialize a new repository, or re-initialize a pre-existing one.  As such, use it carefully - if GitSavvy detects that Git is already initialized, you will be prompted to confirm.

When run, you will asked to confirm the root directory of the new Git repository.  GitSavvy will attempt to auto-detect this for you.

This command will also be suggested to you should you attempt to run a GitSavvy command on a file that is not within a valid Git repository.

## `git: reset`

This command will change the HEAD of the current branch to a previous commit.

When run, you will be asked to select a commit from a list of previous commits. Once you select a commit, the HEAD of the current branch will be changed to the selected commit, and any changes that were committed after the selected commit will appear as unstaged changes in the working directory.

## `git: reset (reflog)`

Like `git: reset`, this command will change the HEAD of the current branch to a previous commit, but uses `git reflog` rather than `git log` as the source of available commits.

## `git: cherry-pick`

This command applies a commit from a different branch to the current branch.

Running the command first prompts for branch selection; then it displays a limited log with commits unique to the chosen branch. Upon selection, the commit is [cherry-picked][1] into the current branch.

[1]: https://git-scm.com/docs/git-cherry-pick