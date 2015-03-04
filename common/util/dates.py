from datetime import datetime

TEN_MINS = 600
ONE_HOUR = 3600
TWO_HOURS = 7200
ONE_DAY = 86400


def fuzzy(event, base=None):
    if not base:
        base = datetime.now()

    if type(event) == str:
        event = datetime.fromtimestamp(int(event))
    elif type(event) == int:
        event = datetime.fromtimestamp(event)
    elif type(event) != datetime:
        raise Exception(
            "Cannot convert object of type {} to fuzzy date string".format(event))

    delta = base - event

    if delta.days == 0:
        if delta.seconds < 60:
            return "{} seconds ago".format(delta.seconds)

        elif delta.seconds < 120:
            return "1 min and {} secs ago".format(delta.seconds - 60)

        elif delta.seconds < TEN_MINS:
            return "{} mins and {} secs ago".format(
                delta.seconds // 60,
                delta.seconds % 60)

        elif delta.seconds < ONE_HOUR:
            return "{} minutes ago".format(delta.seconds // 60)

        elif delta.seconds < TWO_HOURS:
            return "1 hour and {} mins ago".format(
                delta.seconds % ONE_HOUR // 60)

        return "over {} hours ago".format(delta.seconds // ONE_HOUR)

    elif delta.days < 2:
        return "over a day ago"

    elif delta.days < 7:
        return "over {} days ago".format(delta.days)

    return "{date:%b} {date.day}, {date.year}".format(date=event)
