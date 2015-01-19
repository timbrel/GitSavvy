level = 1


def set_level(lvl):
    global level
    if lvl in ("INFO", 0):
        level = 0
    elif lvl in ("WARN", 1):
        level = 1
    elif lvl in ("ERROR", 2):
        level = 2
    else:
        raise Exception("Invalid")


def info(msg):
    if level >= 0 and msg:
        print(msg)


def warn(msg):
    if level >= 1 and msg:
        print(msg)


def error(msg):
    if level >= 2 and msg:
        print(msg)
