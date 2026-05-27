from typing import NewType
from typing_extensions import TypeAlias

# Use LineNo, ColNo for 1-based line column counting (like git or `window.open_file`),
# use Row, Col for 0-based counting like Sublime's `view.rowcol`!
LineNo = int
ColNo = int
Row = int
Col = int

#
FullPath = NewType("FullPath", str)
ShortPath = NewType("ShortPath", str)

#
FullHash = NewType("FullHash", str)
ShortHash = NewType("ShortHash", str)
CommitHash: TypeAlias = "FullHash | ShortHash"
Commitish: TypeAlias = str
