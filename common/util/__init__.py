import sys

from .parse_diff import parse_diff
from . import dates
from . import view
from . import file
from . import log
from . import actions
from . import debug
from . import diff_string
from . import reload
from . import color

super_key = "SUPER" if sys.platform == "darwin" else "CTRL"
