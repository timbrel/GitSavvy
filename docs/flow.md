# git-flow

The excellent [git-flow](https://github.com/nvie/gitflow) extension is fully supported, allowing to run flow commands with sublime commands. These commands will only be visible if have installed `git-flow`.

Most commands attempt to mirror `git-flow` 's interface with the added ability to select a target from local branches/remotes.

## `flow: init`

A required step when you wish to setup a project to use `git-flow`. This will present a series of prompts to configure `git-flow` very much like the interactive shell command does.

## `flow: feature/release/hotfix/support start`

When running this command, you will prompted first for the feature/release/hotfix/support name (without the prefix), and then the branch will be created and checked out.

## `flow: feature/release/hotfix/support finish`

When running this command when an existing feature/release/hotfix/support branch is checked out, you will asked to confirm finish. Otherwise, you will be asked to select the relevant branch. This flow merges the changes from this branch into the "develop" branch (without fast-forwarding, unless branch has only a single commit).

## `flow: feature/release/hotfix publish`

When running this command when an existing feature/release/hotfix branch is checked out, you will asked to confirm publish. Otherwise, you will be asked to select the relevant branch. This flow pushes the target branch to the configured remote.

## `flow: feature/release track`

When running this command you will be prompted to provide a feature name. The command will pull a feature/release from a configured remote and check it out.

## `flow: feature pull`

This will pull a feature from a given remote (not necessarily the configured default remote) and check it out. You will be first prompted to select a remote and then to provide a feature name.
