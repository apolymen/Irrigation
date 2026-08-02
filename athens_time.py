# athens_time.py
#
# Europe/Athens timezone support for MicroPython.
# RTC must be kept in UTC.
#
# Standard time : UTC+2
# Summer time   : UTC+3
#
# DST starts:
#   Last Sunday of March at 01:00 UTC
#
# DST ends:
#   Last Sunday of October at 01:00 UTC

import time

STD_OFFSET = 2 * 3600
DST_OFFSET = 3 * 3600


def _is_leap(year):
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)


def _weekday(year, month, day):
    # Sakamoto algorithm
    t = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
    if month < 3:
        year -= 1
    return (year + year//4 - year//100 + year//400 +
            t[month-1] + day) % 7
    # Sunday=0 ... Saturday=6


def _last_sunday(year, month):
    days = (31,
            29 if _is_leap(year) else 28,
            31,30,31,30,31,31,30,31,30,31)

    last_day = days[month-1]
    w = _weekday(year, month, last_day)
    return last_day - w


def _dst_active(utc):
    year, month, day, hour = utc[:4]

    if month < 3 or month > 10:
        return False

    if 3 < month < 10:
        return True

    if month == 3:
        ls = _last_sunday(year, 3)
        if day > ls:
            return True
        if day < ls:
            return False
        return hour >= 1

    # October
    ls = _last_sunday(year, 10)
    if day < ls:
        return True
    if day > ls:
        return False
    return hour < 1


def utc_offset():
    utc = time.localtime()
    return DST_OFFSET if _dst_active(utc) else STD_OFFSET


def localtime():
    return time.localtime(time.time() + utc_offset())


def now_string():
    t = localtime()
    return "%04d-%02d-%02d %02d:%02d:%02d" % (
        t[0], t[1], t[2], t[3], t[4], t[5]
    )
