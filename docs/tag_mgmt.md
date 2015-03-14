# Tag management

The following commands are provided to manage local and remote tags.


## `git: tags`

GitSavvy's tag dashboard displays all local and remote tags, and enables you to:

- create a new tag (`c`)
- select and delete existing tags(s) (`d`)
- select and push tag(s) to a remote (`p`)
- push all tags to a remote (`P`), and
- view the diff commit that is tagged (`l`)

Remote tags are retrieved asynchronously, and may not display immediately when the view opens.


## `git: quick tag`

You will be prompted first for a tag name, followed by a tag message.  Once entered, a tag will created and associated with the commit at HEAD.
