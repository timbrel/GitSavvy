# Committing

There are several ways to make a commit, both through the status view and through the command palette.

## `git: commit`

This command can be invoked by both the command palette and the status dashboard, and opens a special view where you can make a commit.  The view includes instructions for how to complete and how to abort the action.

GitHub integration is provided to provide easy access to [issues](github.md#issues-integration) and [contributors](github.md#contributors-integration).  More information can be found in their respective sections.

## `git: commit including unstaged files`

This command is similar to the above `git: commit` command.  However, once you have provided the commit message, all unstaged changes will be added to the index before the commit is made.

## `git: amend previous commit`

This command is similar to the above `git: commit` command.  However, instead of creating a new commit, this command will modify the previous command.

The commit view will be pre-populated with the previous commit message.  Proceeding will update the commit message, and the commit's diff will be amended with any changes present in the index.

## `git: fixup from stage`

This command is used to fixup an earlier commit from already staged files. A list of commits of the current branch will be shown and upon selection, the fixup will be squashed into the select commit. If autosquash fails, it leaves a fixup commit `fixup! <original commit>` in the log.

## `git: quick commit`

When invoking this palette command, Sublime will prompt you for a one-line commit message (you will not see the GitSavvy commit view).  Pressing `Enter` will invoke the commit action, and pressing `Escape` will abort.

This command is only available via the command palette.

## `git: quick stage current file and commit`

When invoking this palette command, Sublime will prompt you for a one-line commit message.  Before making the commit, the currently open file will be staged and included in the subsequent commit.

Keep in mind that any other changes that have been staged will also be included in the commit.  This command is only available via the command palette.

## `git: quick stage current file and fixup`

Similar to `git: fixup from stage`. This command also stages the current file before fixing up the commit.
