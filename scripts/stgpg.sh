#!/bin/sh
# kudos to SourceTree for providing us with this idea
# use --batch and --no-tty to avoid the terminal
gpg2 --batch --no-tty "$@"
