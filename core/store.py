from collections import defaultdict

MYPY = False
if MYPY:
    from typing import Any, DefaultDict, Dict, Tuple
    Repo = str

state = defaultdict(dict)  # type: DefaultDict[Repo, Dict[str, Any]]
cache = {}  # type: Dict[Tuple, str]
