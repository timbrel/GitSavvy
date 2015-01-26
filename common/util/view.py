import sublime


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


def get_lines_from_regions(view, regions):
    full_line_regions = (view.full_line(region) for region in regions)
    return [line for region in full_line_regions for line in view.substr(region).split("\n")]
