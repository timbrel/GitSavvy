# Interacting with remotes

GitSavvy provides a few mechanisms for interact with remotes.

## `git: fetch`

This updates the local history of remote branches, and downloads all commit objects referenced in that history.  When running this command from the palette, you will prompted to indicate the remote from which you'd like to fetch.

## `git: checkout remote branch as local`

Assuming you've recently fetched, this command allows you to create a local branch (e.g. `feature-branch-one`) with the same history as a remote branch.  Upon running the command, you will be presented with a list of remote branches and, once selected, the local branch will be created and checked out.

## `git: pull`

When running this command, you will be prompted first for the remote you want to pull from, and then the branch.  If your local branch tracks a remote, that branch name will be pre-selected at the second prompt.

## `git: push`

This command performs a simple `git push`.  In recent Git versions, this will result in a push to the tracking branch if it exists, else a remote branch with the same name as the local.

## `git: push to branch`

When running this command, you will be prompted first for the remote you want to push to, and then the branch.

## `git: push to branch name`

When running this command, you will prompted first for the remote you want to push to.  Next, you'll be provided a text field to enter the name of the remote branch.  This is useful if the remote branch does not yet exist.
