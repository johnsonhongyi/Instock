#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime
import logging
import os
import sys


_CALENDAR_PYTHONPATH = os.environ.get(
    'INSTOCK_TRADE_CALENDAR_PYTHONPATH',
    '/data/InStock/instock/cache/trade_calendar_pythonpath',
)
if _CALENDAR_PYTHONPATH and _CALENDAR_PYTHONPATH not in sys.path:
    sys.path.insert(0, _CALENDAR_PYTHONPATH)

try:
    import a_trade_calendar as _a_trade_calendar
except Exception as e:
    _a_trade_calendar = None
    logging.error('a_trade_calendar is unavailable; trade-date checks fail closed: %s', e)

__author__ = 'myh '
__date__ = '2023/4/10 '


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    value = str(value or '').strip()[:10]
    if len(value) == 8 and value.isdigit():
        value = '%s-%s-%s' % (value[:4], value[4:6], value[6:8])
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _restore_date_type(value, original):
    if isinstance(original, datetime.datetime):
        return datetime.datetime.combine(value, original.timetz())
    return value


def is_trade_date(date=None):
    target = _as_date(date)
    if target is None or _a_trade_calendar is None:
        return False
    try:
        return bool(_a_trade_calendar.is_trade_date(target.isoformat()))
    except Exception as e:
        logging.error('a_trade_calendar trade-date check failed for %s: %s', target, e)
        return False


def get_previous_trade_date(date):
    target = _as_date(date)
    if target is None:
        raise ValueError('date must be a valid calendar date')
    if _a_trade_calendar is None:
        return date
    previous = _as_date(_a_trade_calendar.get_pre_trade_date(target.isoformat()))
    if previous is None:
        raise LookupError('no previous trade date in the available calendar')
    return _restore_date_type(previous, date)


def get_next_trade_date(date):
    target = _as_date(date)
    if target is None:
        raise ValueError('date must be a valid calendar date')
    if _a_trade_calendar is None:
        return date
    following = _as_date(_a_trade_calendar.get_next_trade_date(target.isoformat()))
    if following is None:
        raise LookupError('no next trade date in the available calendar')
    return _restore_date_type(following, date)


OPEN_TIME = (
    (datetime.time(9, 15, 0), datetime.time(11, 30, 0)),
    (datetime.time(13, 0, 0), datetime.time(15, 0, 0)),
)


def is_tradetime(now_time):
    now = now_time.time()
    for begin, end in OPEN_TIME:
        if begin <= now < end:
            return True
    else:
        return False


def is_trading(now_time=None):
    """当前时刻是否处于实际交易盘中时段（排除非交易日、午间休市 11:30-13:00 及收盘）"""
    if now_time is None:
        now_time = datetime.datetime.now()
    if not is_trade_date(now_time.date()):
        return False
    return is_tradetime(now_time)


PAUSE_TIME = (
    (datetime.time(11, 30, 0), datetime.time(12, 59, 30)),
)


def is_pause(now_time):
    now = now_time.time()
    for b, e in PAUSE_TIME:
        if b <= now < e:
            return True


CONTINUE_TIME = (
    (datetime.time(12, 59, 30), datetime.time(13, 0, 0)),
)


def is_continue(now_time):
    now = now_time.time()
    for b, e in CONTINUE_TIME:
        if b <= now < e:
            return True
    return False


CLOSE_TIME = (
    datetime.time(15, 0, 0),
)


def is_closing(now_time, start=datetime.time(14, 54, 30)):
    now = now_time.time()
    for close in CLOSE_TIME:
        if start <= now < close:
            return True
    return False


def is_close(now_time):
    now = now_time.time()
    for close in CLOSE_TIME:
        if now >= close:
            return True
    return False


def is_open(now_time):
    now = now_time.time()
    if now >= datetime.time(9, 30, 0):
        return True
    return False


def get_trade_hist_interval(date):
    if hasattr(date, "strftime"):
        date_str = date.strftime("%Y-%m-%d")
    else:
        date_str = str(date)[:10]
    tmp_year, tmp_month, tmp_day = date_str.split("-")
    date_end = datetime.datetime(int(tmp_year), int(tmp_month), int(tmp_day))
    date_start = (date_end + datetime.timedelta(days=-(365 * 3))).strftime("%Y%m%d")

    now_time = datetime.datetime.now()
    now_date = now_time.date()
    is_trade_date_open_close_between = False
    if date_end.date() == now_date:
        if is_trade_date(now_date):
            if is_open(now_time) and not is_close(now_time) and not is_pause(now_time):
                is_trade_date_open_close_between = True

    return date_start, not is_trade_date_open_close_between


def get_trade_date_last(websrv=False):
    now_time = datetime.datetime.now()
    run_date = now_time.date()
    run_date_nph = run_date
    if is_trade_date(run_date):
        if not websrv:
            if not is_close(now_time):
                run_date = get_previous_trade_date(run_date)
                if not is_open(now_time):
                    run_date_nph = run_date
        else:
            if not is_open(now_time):
                run_date = get_previous_trade_date(run_date)
                run_date_nph = run_date
    else:
        run_date = get_previous_trade_date(run_date)
        run_date_nph = run_date
    return run_date, run_date_nph


def get_trade_date_last_old():
    now_time = datetime.datetime.now()
    run_date = now_time.date()
    run_date_nph = run_date
    if is_trade_date(run_date):
        if not is_close(now_time):
            run_date = get_previous_trade_date(run_date)
            if not is_open(now_time):
                run_date_nph = run_date
    else:
        run_date = get_previous_trade_date(run_date)
        run_date_nph = run_date
    return run_date, run_date_nph


def get_quarterly_report_date():
    now_time = datetime.datetime.now()
    year = now_time.year
    month = now_time.month
    if 1 <= month <= 3:
        month_day = '1231'
    elif 4 <= month <= 6:
        month_day = '0331'
    elif 7 <= month <= 9:
        month_day = '0630'
    else:
        month_day = '0930'
    return f"{year}{month_day}"


def get_bonus_report_date():
    now_time = datetime.datetime.now()
    year = now_time.year
    month = now_time.month
    if 2 <= month <= 6:
        year -= 1
        month_day = '1231'
    elif 8 <= month <= 12:
        month_day = '0630'
    elif month == 7:
        if now_time.day > 25:
            month_day = '0630'
        else:
            year -= 1
            month_day = '1231'
    else:
        year -= 1
        if now_time.day > 25:
            month_day = '1231'
        else:
            month_day = '0630'
    return f"{year}{month_day}"
