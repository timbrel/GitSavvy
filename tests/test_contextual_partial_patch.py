import unittest

from GitSavvy.core.parse_diff import SplittedDiff
import GitSavvy.core.commands.diff as module


def marked_diff_case(text):
    source, expected = text.split("========================\n", 1)
    line_starts = set()
    lines = []
    pt = 0

    for line in source.splitlines(True):
        marker, line = line[0], line[1:]
        if marker == ">":
            line_starts.add(pt)
        lines.append(line)
        pt += len(line)

    return "".join(lines), line_starts, expected


class TestContextualPartialPatch(unittest.TestCase):
    def test_applies_selected_change_with_context(self):
        self.assert_patch("""\
 diff --git a/fooz b/barz
 --- a/fooz
 +++ b/barz
>@@ -10,3 +10,3 @@
  context
>-old
>+new
  tail
========================
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -10,3 +10,3 @@
 context
-old
+new
 tail
""")

    def test_reverts_selected_change_with_context(self):
        self.assert_patch("""\
 diff --git a/fooz b/barz
 --- a/fooz
 +++ b/barz
>@@ -10,3 +10,3 @@
  context
>-old
>+new
  tail
========================
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -10,3 +10,3 @@
 context
-old
+new
 tail
""", reverse=True)

    def test_applies_selected_addition_with_context(self):
        self.assert_patch("""\
 diff --git a/fooz b/barz
 --- a/fooz
 +++ b/barz
 @@ -10,3 +10,3 @@
  context
 -old
>+new
  tail
========================
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -10,3 +10,4 @@
 context
 old
+new
 tail
""")

    def test_reverts_selected_addition_with_context(self):
        self.assert_patch("""\
 diff --git a/fooz b/barz
 --- a/fooz
 +++ b/barz
 @@ -10,3 +10,3 @@
  context
 -old
>+new
  tail
========================
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -10,2 +10,3 @@
 context
+new
 tail
""", reverse=True)

    def test_selected_hunk_header_alone_does_not_select_hunk(self):
        self.assert_patch("""\
 diff --git a/fooz b/barz
 --- a/fooz
 +++ b/barz
>@@ -10,3 +10,3 @@
  context
 -old
 +new
  tail
========================
""")

    def test_selected_context_line_alone_does_not_select_hunk(self):
        self.assert_patch("""\
 diff --git a/fooz b/barz
 --- a/fooz
 +++ b/barz
 @@ -10,3 +10,3 @@
> context
 -old
 +new
  tail
========================
""")

    def test_applies_selected_removal_with_context(self):
        self.assert_patch("""\
 diff --git a/fooz b/barz
 --- a/fooz
 +++ b/barz
 @@ -10,3 +10,3 @@
> context
>-old
 +new
  tail
========================
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -10,3 +10,2 @@
 context
-old
 tail
""")

    def test_reverts_selected_removal_with_context(self):
        self.assert_patch("""\
 diff --git a/fooz b/barz
 --- a/fooz
 +++ b/barz
 @@ -10,3 +10,3 @@
> context
>-old
 +new
  tail
========================
diff --git a/fooz b/barz
--- a/fooz
+++ b/barz
@@ -10,4 +10,3 @@
 context
-old
 new
 tail
""", reverse=True)

    def test_zero_context_selection_does_not_build_contextual_patch(self):
        raw_diff, line_starts, _ = marked_diff_case("""\
 diff --git a/fooz b/barz
 --- a/fooz
 +++ b/barz
 @@ -1,1 +1,1 @@
>-old
>+new
========================
""")
        diff = SplittedDiff.from_string(raw_diff)
        actual, error = module.compute_contextual_patch_for_sel(
            diff, line_starts, reverse=False
        )
        self.assertEqual(actual, "")
        self.assertEqual(error, "Cannot apply selection without context.")

    def assert_patch(self, text, reverse=False):
        raw_diff, line_starts, expected = marked_diff_case(text)
        diff = SplittedDiff.from_string(raw_diff)
        actual, error = module.compute_contextual_patch_for_sel(diff, line_starts, reverse)
        self.assertEqual(error, None)
        self.assertEqual(actual, expected)
