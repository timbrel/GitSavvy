from unittesting import DeferrableTestCase
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.core.fns import accumulate, unzip

import GitSavvy.core.commands.diff as module
from GitSavvy.core.parse_diff import HunkHeader


f1 = """\
@@ -383,3 +383,3 @@ class gs_diff_zoom(TextCommand):
         step_size = max(abs(amount), MIN_STEP_SIZE)
-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
+        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
         if amount > 0:
"""

f2 = """\
@@ -383,2 +383,3 @@ class gs_diff_zoom(TextCommand):
         step_size = max(abs(amount), MIN_STEP_SIZE)
+        values
         values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
"""

f3 = """\
@@ -383,3 +383,2 @@ class gs_diff_zoom(TextCommand):
         step_size = max(abs(amount), MIN_STEP_SIZE)
-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
         if amount > 0:
"""

f4 = """\
@@ -383,4 +383,3 @@ class gs_diff_zoom(TextCommand):
         step_size = max(abs(amount), MIN_STEP_SIZE)
-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
-        if amount > 0:
+        zif amount > 0:
             next_value = next(x for x in values if x > current)
"""


class TestDiffRecountingLinesFunctions(DeferrableTestCase):
    @p.expand([
        (
            "sym modification",
            f1,
            [383, 384, 384, 385],
            [383, 383, 384, 385]
        ),
        (
            "addition",
            f2,
            [383, 383, 384],
            [383, 384, 385]
        ),
        (
            "deletion",
            f3,
            [383, 384, 385],
            [383, 383, 384]
        ),
        (
            "asym modification",
            f4,
            [383, 384, 385, 385, 386],
            [383, 383, 383, 384, 385]
        ),

        (
            "x",
            """\
@@ -381,8 +381,7 @@ class gs_diff_zoom(TextCommand):
         current = settings.get('git_savvy.diff_view.context_lines')

         MINIMUM, DEFAULT, MIN_STEP_SIZE = 1, 3, 5
-        step_size = max(abs(amount), MIN_STEP_SIZE)
-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
+        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
         if amount > 0:
             next_value = next(x for x in values if x > current)
         else:
""",
            [381, 382, 383, 384, 385, 385, 386, 387, 388],
            [381, 382, 383, 383, 383, 384, 385, 386, 387]

        )

    ])
    def test_recount_lines_of_hunk(self, _, PATCH, aside, bside):
        diff = module.SplittedDiff.from_string(PATCH)
        hunk = diff.hunks[0]
        actual = module.recount_lines(hunk)
        EXPECTED = list(zip(aside, bside))
        self.assertEqual(EXPECTED, [a_b for line, a_b in actual])

    @p.expand([
        ("sym modification", f1, [383, 384, 384, 385]),
        ("addition", f2, [383, 384, 385]),
        ("deletion", f3, [383, 384, 384]),
        ("asym modification", f4, [383, 384, 384, 384, 385])
    ])
    def test_recount_for_jump_to_file(self, _, PATCH, EXPECTED):
        diff = module.SplittedDiff.from_string(PATCH)
        hunk = diff.hunks[0]
        actual = module.recount_lines_for_jump_to_file(hunk)
        self.assertEqual(EXPECTED, [line.b for line in actual])


header = """\
diff --git a/core/commands/diff.py b/core/commands/diff.py
index 5b91be74..f1925fb2 100644
--- a/core/commands/diff.py
+++ b/core/commands/diff.py
"""


class TestDiffPatchGeneration(DeferrableTestCase):
    @p.expand([
        (
            "modification",
            """\
 @@ -383,3 +383,3 @@ class gs_diff_zoom(TextCommand):
          step_size = max(abs(amount), MIN_STEP_SIZE)
|-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
|+        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
          if amount > 0:
""",
            """\
@@ -384,1 +384,1 @@
-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
+        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
"""
        ),

        (
            "first line of modification",
            """\
 @@ -383,3 +383,3 @@ class gs_diff_zoom(TextCommand):
          step_size = max(abs(amount), MIN_STEP_SIZE)
|-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
 +        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
          if amount > 0:
""",
            """\
@@ -384,1 +383,0 @@
-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
"""
        ),
        (
            "single deletion",
            """\
 @@ -383,3 +383,2 @@ class gs_diff_zoom(TextCommand):
          step_size = max(abs(amount), MIN_STEP_SIZE)
|-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
          if amount > 0:
""",
            """\
@@ -384,1 +383,0 @@
-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
"""
        ),

        # Note how A and B stage "the" line after "383 step_size" differently!
        # In (A) the line comes still *after* the deletion line.
        (
            "second line of modification (A)",
            """\
 @@ -383,3 +383,3 @@ class gs_diff_zoom(TextCommand):
          step_size = max(abs(amount), MIN_STEP_SIZE)
 -        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
|+        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
          if amount > 0:
""",
            """\
@@ -384,0 +385,1 @@
+        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
"""
        ),
        (
            "single addition             (B)",

            """\
 @@ -383,2 +383,3 @@ class gs_diff_zoom(TextCommand):
          step_size = max(abs(amount), MIN_STEP_SIZE)
|+        values
          values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
""",
            """\
@@ -383,0 +384,1 @@
+        values
"""
        ),


        # 3-line modification;
        # first line being a deletion, 2nd and 3rd a symmetric modification.

        (
            "stage 3rd line",
            """\
 @@ -383,4 +383,3 @@ class gs_diff_zoom(TextCommand):
          MINIMUM, DEFAULT, MIN_STEP_SIZE = 1, 3, 5
 -        step_size = max(abs(amount), MIN_STEP_SIZE)
 -        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
|+        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
          if amount > 0:
""",
            """\
@@ -385,0 +386,1 @@
+        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
"""
        ),
        (
            "then stage 2nd line",
            """\
 @@ -383,4 +383,2 @@ class gs_diff_zoom(TextCommand):
          MINIMUM, DEFAULT, MIN_STEP_SIZE = 1, 3, 5
 -        step_size = max(abs(amount), MIN_STEP_SIZE)
|-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
          values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
""",
            """\
@@ -385,1 +384,0 @@
-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
"""
        ),

        #
        (
            "First stage 2nd line",
            """\
 @@ -383,4 +383,3 @@ class gs_diff_zoom(TextCommand):
          MINIMUM, DEFAULT, MIN_STEP_SIZE = 1, 3, 5
 -        step_size = max(abs(amount), MIN_STEP_SIZE)
|-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
 +        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
          if amount > 0:
""",
            """\
@@ -385,1 +384,0 @@
-        values = chain([MINIMUM, DEFAULT], count(step_size, step_size))
"""
        ),
        (
            "Then stage 3rd line",
            """\
 @@ -383,3 +383,3 @@ class gs_diff_zoom(TextCommand):
          MINIMUM, DEFAULT, MIN_STEP_SIZE = 1, 3, 5
 -        step_size = max(abs(amount), MIN_STEP_SIZE)
|+        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
          if amount > 0:
""",
            """\
@@ -384,0 +385,1 @@
+        values = chain([0, MINIMUM, DEFAULT], count(step_size, step_size))
"""
        ),



    ])
    def test_patch_generation(self, _, input, patch):
        marker, content = unzip((line[0], line[1:] or "\n") for line in input.splitlines(keepends=True))
        all_line_starts = list(accumulate(map(len, content), initial=len(header)))
        line_starts = {all_line_starts[i] for i, m in enumerate(marker) if m.strip()}
        diff_text = header + "".join(content)
        diff = module.SplittedDiff.from_string(diff_text)
        actual = module.compute_patch_for_sel(diff, line_starts, reverse=False)
        expected = header + patch
        if _.startswith("_"):
            if expected == actual:
                raise AssertionError("unexpected success")
            else:
                return  # Ok, expected failure

        self.assertEqual(expected, actual)


class HunkHeaderNumberExtraction(DeferrableTestCase):
    @p.expand([
        ("@@ -685,8 +686,14 @@ ...", [(685, 8), (686, 14)]),
        ("@@@ -685,8 -644 +686,14 @@@ ...", [(685, 8), (644, 1), (686, 14)]),
        ('@@ -7,0 +8 @@ RCall = "6f49c342-dc21-5d91-9882-a32aef131414"', [(7, 0), (8, 1)]),
        ("@@ -295 +313 @@ docs-main:iai-2.0.0:", [(295, 1), (313, 1)]),
    ])
    def test_safely_parse_metadata(self, input, expected):
        actual = HunkHeader(input).safely_parse_metadata()
        self.assertEqual(expected, actual)
