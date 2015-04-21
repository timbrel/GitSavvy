from datetime import datetime
from datetime import timedelta
import calendar


TEN_MINS = 600
ONE_HOUR = 3600
TWO_HOURS = 7200
ONE_DAY = 86400


# http://stackoverflow.com/questions/4563272/how-to-convert-a-python-utc-datetime-to-a-local-datetime-using-only-python-stand/13287083#13287083
def utc_to_local(utc_dt):
    """
    Convert times that have been given to us in UTC to out local timezone
    """
    # get integer timestamp to avoid precision lost
    timestamp = calendar.timegm(utc_dt.timetuple())
    local_dt = datetime.fromtimestamp(timestamp)
    assert utc_dt.resolution >= timedelta(microseconds=1)
    return local_dt.replace(microsecond=utc_dt.microsecond)


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
