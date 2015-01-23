CLEAN_WORKING_DIR = "Nothing to commit, working directory clean."
ADD_ALL_UNSTAGED_FILES = " ?  All unstaged files"
ADD_ALL_FILES = " +  All files"
INLINE_DIFF_TITLE = "DIFF: "

STYLES_HEADER = """
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
"""

ADDED_LINE_STYLE = """
 <dict>
    <key>name</key>
    <string>GitBetter Added Line</string>
    <key>scope</key>
    <string>gitbetter.change.addition</string>
    <key>settings</key>
    <dict>
        <key>background</key>
        <string>#{}</string>
    </dict>
</dict>
"""

REMOVED_LINE_STYLE = """
 <dict>
    <key>name</key>
    <string>GitBetter Removed Line</string>
    <key>scope</key>
    <string>gitbetter.change.removal</string>
    <key>settings</key>
    <dict>
        <key>background</key>
        <string>#{}</string>
    </dict>
</dict>
"""

DIFF_HEADER = """diff --git a/{path} b/{path}
--- a/{path}
+++ b/{path}
"""
