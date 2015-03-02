# Branch management

The following commands are provided to manage the state of your branch(es).  Future versions of GitSavvy will include a full-featured branch view, similar to the status dashboard.


## `git: checkout`

You will be shown a panel of local branches.  Once you've made a selection, that branch will be checked out.


## `git: checkout new branch`

You will be prompted for a new branch name.  Once entered, that branch will be created and checked out.


## `git: merge`

You will be shown a list of all local _and_ remote branches.  Once selected, that branch will be merged in the current branch.  Any errors will be displayed, and any merge conflicts can be seen and addressed in the status dashboard.


## `git: abort merge`

While in a merge, running this command will reset the working tree back to pre-merge conditions.


## `git: restart merge for file...`

Running this command while mid-merge will display a list of all files with merge conflicts.  Once you've made a selection, that file will be reset to the condition it was in at the beginning of the merge, before you attempted to resolve the merge conflict.
