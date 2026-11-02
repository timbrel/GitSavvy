import sys

from .parse_diff import parse_diff
from . import dates
from . import file
from . import actions
from . import diff_string

super_key = "super" if sys.platform == "darwin" else "ctrl"
