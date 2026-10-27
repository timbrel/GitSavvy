## Stash

Command to manipulate stashes. For those commands where you would need to pick a commit it will open a panel to pick which stash to action on.

## `git: stash save` 

Create a stash.

## `git: stash save including untracked files` 

Create a stash including untracked files.

## `git: stash save staged changes only` 

Create a stash from staged changes only using `git stash push --staged` (requires Git 2.35 or newer). Unstaged and untracked changes are left in the working directory.

## `git: stash show` 

Show a stash

## `git: stash apply` 

Apply a stash

## `git: stash pop` 

Pop a stash

## `git: stash drop` 

Discard a stash
