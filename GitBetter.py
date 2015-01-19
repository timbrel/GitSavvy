import sys
import imp
from .common import log

for name, module in sys.modules.items():
    if name[0:9] == "GitBetter":
        print("reloading " + name)
        imp.reload(module)

log.set_level(0)

if sys.version_info[0] == 2:
    raise ImportWarning("GitBetter does not support Sublime Text 2.")
else:
    from .commands import *
