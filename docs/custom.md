# Custom commands

GitSavvy provides a flexible "generic" command, which can be used to add any almost any git functionality and call it from sublime, like any other GitSavvy command.

## Usage

Adding new commands is as simple as creating/modifying `User.sublime-commands` file in your `User` Package directory (see [examples below](#examples)):

## Command arguments

Your custom command may be further customized by setting the following arguments:

* `output_to_panel` - send the command output to a panel when complete
* `args`            - arguments to pass to the `git` command

      GitSavvy also supports some basic interpolation when specifying your `args`. If one of these strings is provided as an element of your `args` array, the appropriate string will be substituted. The following strings are currently supported:
      
       - `{FILE_PATH}` - the path to the currently opened file.
       - `{REPO_PATH}` - the path to the currently opened file's repo path.
       - `{PROMPT_ARG}` - prompt use to a custom argument

* `start_msg`       - a message to display in status bar when the command starts
* `complete_msg`    - a message to display in status bar when the command completes
* `prompt_msg`      - when using "{PROMPT_ARG}" argument in command, this determines the prompt message
* `run_in_thread`   - when true, your command will be run in a separate child thread, independent of the async UI thread

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
    }
]
```
