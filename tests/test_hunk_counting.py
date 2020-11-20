
from unittesting import DeferrableTestCase

import GitSavvy.core.commands.diff as module

from GitSavvy.tests.parameterized import parameterized as p


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
