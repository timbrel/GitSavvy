def next_color(color_text):
    """
    Given a color string "#xxxxxy", returns its next color "#xxxxx{y+1}".
    """
    hex_value = int(color_text[1:], 16)
    if hex_value == 16777215:  # #ffffff
        return "#fffffe"
    else:
        return "#{}".format(hex(hex_value+1)[2:])
