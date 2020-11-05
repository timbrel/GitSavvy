# Interacting with remotes

GitSavvy provides a few mechanisms for interact with remotes.

## `git: fetch`

This updates the local history of remote branches, and downloads all commit objects referenced in that history. If the repository has multiple remotes you will prompted to indicate the remote from which you'd like to fetch, when running this command from the palette.

## `git: checkout remote branch as local`

Assuming you've recently fetched, this command allows you to create a local branch (e.g. `feature-branch-one`) with the same history as a remote branch.  Upon running the command, you will be presented with a list of remote branches and, once selected, the local branch will be created and checked out.

## `git: pull`

This command will pull current branch from the tracking branch. If the tracking branch is not set, you will be prompted. If the git config `pull.rebase` is set true, the command will be execulated with `--rebase`.


## `git: pull with rebase`

Like `git: pull`, but rebasing on the remote branch explictly.

## `git: pull from branch`

When running this command, you will be prompted first for the remote you want to pull from, and then the branch.  If your local branch tracks a remote, that branch name will be pre-selected at the second prompt.

## `git: pull from branch with rebase`

Like `git: pull from branch`, but rebasing on the remote branch instead of merging.

**For the following commands you need to configure a username and password in git, so GitSavvy can use it.**

## `git: push`

This command will push current branch to the tracking branch. If the tracking branch is not set, you will be prompted for a remote and a branch name.

## `git: push to branch`

When running this command, you will be prompted first for the remote you want to push to, and then the branch.

## `git: push to branch name`

When running this command, you will prompted first for the remote you want to push to.  Next, you'll be provided a text field to enter the name of the remote branch.  This is useful if the remote branch does not yet exist.
