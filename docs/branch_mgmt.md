# Branch management

The following commands are provided to manage the state of your branch(es).  Future versions of GitSavvy will include a full-featured branch view, similar to the status dashboard.


## `git: checkout`

You will be shown a panel of local branches.  Once you've made a selection, that branch will be checked out.


## `git: checkout new branch`

You will be prompted for a new branch name.  Once entered, that branch will be created and checked out.


## `git: checkout current file`

Reset the current active file to HEAD.


## `git: merge`

You will be shown a list of all local _and_ remote branches.  Once selected, that branch will be merged in the current branch.  Any errors will be displayed, and any merge conflicts can be seen and addressed in the status dashboard.


## `git: abort merge`

While in a merge, running this command will reset the working tree back to pre-merge conditions.


## `git: restart merge for file...`

Running this command while mid-merge will display a list of all files with merge conflicts.  Once you've made a selection, that file will be reset to the condition it was in at the beginning of the merge, before you attempted to resolve the merge conflict.

## `git: branch`

Running this command opens a branch dashboard where you can view and manipulate local and remote branches.

### Actions

#### Checkout selected branch (`c`)

Checks out the selected branch.  If you have uncommitted changes, the action may fail.

#### Create new branch (`b`)

You will be prompted for a new branch name.  Once entered, a branch will be created from HEAD and will be checked out.

#### Delete selected branch (`d`)

The selected branch will be deleted.  This also works for remote branches, so use with caution.

#### Delete selected branch (force) (`D`)

The selected branch will be deleted (and will continue if changes are unmerged).  This also works for remote branches, so use with caution.

#### Rename local branch (`R`)

You will be prompted to enter a new branch name.  Once entered, the selected branch will be renamed.

#### Configure tracking for active branch (`t`)

You will be prompted for a remote and remote branch.  Once supplied, the selected branch will be configured to track the remote branch.

#### Push selected branch to remote (`p`)

You will be prompted for a remote.  Once supplied, the selected branch will be pushed.

#### Push all branches to remote (`P`)

You will be prompted for a remote.  Once supplied, all local branches will be pushed to the remote.

#### Merge selected branch into active (`m`)

The selected branch will be merged into the active branch.  If the merge cannot complete, you will be notified and will be required to resolve the conflicts.

#### Fetch remote and merge selected branch into active (`M`)

This action also merged the selected branch into active.  However, it only works for remote branches, and will first fetch from the selected remote before the branch is merged.

#### Diff selected branch against active (`f`)

A scratch view will be opened, showing the diff between the selected branch and the active branch.

#### Toggle display of remote branches (`e`)

By default, remote branches are not displayed in the branch dashboard.  In many cases, there are many remote branches that would overwhelm the interface.  To view, press `e`.  To hide, press `e` again.

If you would like the default behavior to be inverted, set `show_remotes_in_branch_dashboard` in `GitSavvy.sublime-settings`.
