# Custom commands

GitSavvy provides a flexible "generic" command, which can be used to add any almost any git functionality and call it from sublime, like any other GitSavvy command.

## Usage

Adding new commands is as simple as creating/modifying `User.sublime-commands` file in your `User` Package directory (see [examples below](#examples)):

## Command arguments

Your custom command may be further customized by setting the following arguments:

* `run_in_thread`   - when true, your command will be run in a separate child thread, independent of the async UI thread. This flag must be **set to true if the command runs a long-lived process**, or otherwise GitSavvy will hang while waiting for the process to terminate.
* `output_to_panel` - send the command output to a panel when complete
* `output_to_buffer` - send the command output to a new buffer when complete
* `syntax` - If the output is printed to a buffer you can select syntax
      on example:
      "syntax": "Packages/GitSavvy/syntax/show_commit.sublime-syntax",
      Other syntaxes comming with GitSavvy can be found in (syntax)[https://github.com/timbrel/GitSavvy/tree/master/syntax]

* `args`            - arguments to pass to the `git` command

      GitSavvy also supports some basic interpolation when specifying your `args`. If one of these strings is provided as an element of your `args` array, the appropriate string will be substituted. The following strings are currently supported:

       - `{FILE_PATH}` - the path to the currently opened file.
       - `{REPO_PATH}` - the path to the currently opened file's repo path.
       - `{PROMPT_ARG}` - prompt use to a custom argument

* `start_msg`       - a message to display in status bar when the command starts
* `complete_msg`    - a message to display in status bar when the command completes
* `prompt_msg`      - when using "{PROMPT_ARG}" argument in command, this determines the prompt message
* `custom_environ`   - Can be used to set custom environment variables for this custom command, see example below

      **:boom: Warning**

      Take *extra* care when enabling `run_in_thread`; while it can be useful for long running `git` commands, if handled incorrectly, running such a background thread can have undesirable effects.


## Examples

Here are some more real-life examples of custom command usage:

```javascript
[
    {
        "caption": "git: create patch (from last commit)",
        "command": "gs_custom",
        "args": {
            "output_to_panel": true,
            "args": ["format-patch", "-1"],
            "start_msg": "Started creating patch",
            "complete_msg": "Finished creating patch"
        }
    },
    {
        "caption": "git: apply patch (with current file)",
        "command": "gs_custom",
        "args": {
            "output_to_panel": false,
            "args": ["apply", "{FILE_PATH}"],
            "start_msg": "Started applying patch",
            "complete_msg": "Finished applying patch"
        }
    },
    {
        "caption": "git: fixup commit",
        "command": "gs_custom",
        "args": {
            "output_to_panel": false,
            "args": ["commit", "--fixup", "{PROMPT_ARG}"],
            "prompt_msg": "Commit to fixup: "
        }
    },
    {
        "caption": "git: gui blame",
        "command": "gs_custom",
        "args": {
            "output_to_panel": false,
            "args": ["gui", "blame", "{FILE_PATH}"],
            "start_msg": "Starting gui blame...",
            "complete_msg": "Gui blame started",
            "run_in_thread": true  // SEE WARNING ABOVE !
        }
    },
    {
        "caption": "git: rebase fixup commits",
        "command": "gs_custom",
        "args": {
            "output_to_panel": true,
            "args": ["rebase", "-p", "-i", "--autosquash", "{PROMPT_ARG}"],
            "custom_environ": {"EDITOR": "cat"},
            "prompt_msg": "Rebase from commit: "
        }
    },
    {
        "caption": "git: rebase interactive (rebase -i)",
        "command": "gs_custom",
        "args": {
            "args": ["rebase", "-i", "{PROMPT_ARG}"],
            "prompt_msg": "Rebase from",
            "start_msg": "Starting rebase interactive...",
            "complete_msg": "Rebase interactive started",
            "custom_environ": {"EDITOR": "/usr/local/bin/subl -nw"},
        }
    }
]
```
