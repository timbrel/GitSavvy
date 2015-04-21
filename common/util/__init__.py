import os

from .parse_diff import parse_diff
from . import dates
from . import view
from . import file
from . import log
from . import actions
from . import debug
from . import diff_string

super_key = "SUPER" if os.name == "posix" else "CTRL"
