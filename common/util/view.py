import sublime


##############
# DECORATORS #
##############

def single_cursor_pt(run):
    def decorated_run(self, edit):
        cursors = self.view.sel()
        if not cursors:
            return

        return run(self, edit, cursors[0].a)


####################
# HELPER FUNCTIONS #
####################

def move_cursor(view, line_no, char_no):
    # Line numbers are one-based, rows are zero-based.
    line_no -= 1

    # Negative line index counts backwards from the last line.
    if line_no < 0:
        last_line, _ = view.rowcol(view.size())
        line_no = last_line + line_no + 1

    pt = view.text_point(line_no, char_no)
    view.sel().clear()
    view.sel().add(sublime.Region(pt))
    view.show(pt)


def _region_within_regions(all_outer, inner):
    for outer in all_outer:
        if outer.begin() <= inner.begin() and outer.end() >= inner.end():
            return True
    return False


def get_lines_from_regions(view, regions, valid_ranges=None):
    full_line_regions = (view.full_line(region) for region in regions)

    valid_regions = ([region for region in full_line_regions if _region_within_regions(valid_ranges, region)]
                     if valid_ranges else
                     full_line_regions)

    return [line for region in valid_regions for line in view.substr(region).split("\n")]
