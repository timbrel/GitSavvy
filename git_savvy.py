import sys

if sys.version_info[0] == 2:
    raise ImportWarning("GitSavvy does not support Sublime Text 2.")
else:
    def plugin_loaded():
        from .common import util
        util.file.determine_syntax_files()

    from .common.commands import *
    from .common.ui import *
    from .core.commands import *
    from .core.interfaces import *
    from .github.commands import *
