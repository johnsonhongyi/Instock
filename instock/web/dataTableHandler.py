#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import json
import re
from abc import ABC
from tornado import gen
from tornado.web import HTTPError
# import logging
import datetime
import time
import logging
from collections import OrderedDict
import instock.lib.version as version
import instock.lib.trade_time as trd
import instock.core.singleton_stock_web_module_data as sswmd
import instock.web.base as webBase

__author__ = 'myh '
__date__ = '2023/3/10 '


class MyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, datetime.date):
            return obj.strftime("%Y-%m-%d")
        else:
            return json.JSONEncoder.default(self, obj)


def _table_exists(db, table_name):
    rows = db.query(
        'SELECT 1 FROM information_schema.tables '
        'WHERE table_schema = DATABASE() AND table_name = %s LIMIT 1',
        table_name,
    )
    return bool(rows)


def _query_parts(handler, module):
    """Keep identifiers allowlisted and all request values out of SQL text."""
    try:
        start = int(handler.get_argument('start', '0'))
        length = int(handler.get_argument('length', '10'))
        if start < 0 or length < -1:
            raise ValueError('Invalid pagination')
        filters, values, orders = [], [], []
        filtered_date = False
        sort_columns = {}
        for key in handler.request.arguments:
            match = re.fullmatch(r'columns\[(\d+)\]\[search\]\[value\]', key)
            if match:
                index = int(match[1])
                if index >= len(module.columns):
                    raise ValueError('Invalid column')
                value = handler.get_argument(key, strip=False)
                if value:
                    column = module.columns[index]
                    filtered_date = filtered_date or column == 'date'
                    filters.append(f'`{column}` = %s' if column == 'date' else f'`{column}` LIKE %s')
                    values.append(value if column == 'date' else '%' + value + '%')
            match = re.fullmatch(r'order\[(\d+)\]\[column\]', key)
            if match:
                sort_columns[int(match[1])] = int(handler.get_argument(key))
        date = handler.get_argument('date', None, strip=False)
        if not filtered_date and date is not None and 'date' in module.columns:
            filters.append('`date` = %s')
            values.append(date)
        for order, index in sorted(sort_columns.items()):
            direction = handler.get_argument(f'order[{order}][dir]', '').upper()
            if not 0 <= index < len(module.columns) or direction not in ('ASC', 'DESC'):
                raise ValueError('Invalid order')
            orders.append(f'`{module.columns[index]}` {direction}')
    except (ValueError, TypeError):
        raise HTTPError(400, reason='Invalid table query parameters') from None
    where = ' WHERE ' + ' AND '.join(filters) if filters else ''
    order = ' ORDER BY ' + ', '.join(orders) if orders else ''
    limit = ' LIMIT %s, %s' if length > 0 else ''
    return where, order, limit, values, ([start, length] if length > 0 else [])


class GetStockDatesHandler(webBase.BaseHandler):
    """Bounded, short-lived cache of indexed date-only month queries."""
    cache = OrderedDict()
    ttl = 60

    def get(self):
        name = self.get_argument('name', '')
        month = self.get_argument('month', '')
        modules = sswmd.stock_web_module_data().get_data_list()
        module = next((item for item in modules if item.table_name == name
                       and item.mode == 'query' and 'date' in item.columns), None)
        try:
            first = datetime.datetime.strptime(month, '%Y-%m').date().replace(day=1)
            if first.strftime('%Y-%m') != month:
                raise ValueError('Invalid month')
            following = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
            start = first - datetime.timedelta(days=7)
            end = following + datetime.timedelta(days=7)
        except (ValueError, OverflowError):
            self.set_status(400)
            self.write({'dates': [], 'error': 'Invalid month'})
            return
        if module is None:
            self.set_status(400)
            self.write({'dates': [], 'error': 'Invalid table'})
            return
        key = (name, month)
        cached = self.cache.get(key)
        if cached and time.monotonic() - cached[0] < self.ttl:
            self.cache.move_to_end(key)
            self.write({'dates': cached[1], 'ttl': self.ttl})
            return
        try:
            db = self.db
            if _table_exists(db, module.table_name):
                rows = db.query(
                    f'SELECT DISTINCT `date` FROM `{module.table_name}` '
                    'WHERE `date` >= %s AND `date` < %s ORDER BY `date`', start, end)
                dates = [row['date'].strftime('%Y-%m-%d') for row in rows]
            else:
                dates = []
        except Exception:
            logging.exception('Calendar query failed: %s', name)
            self.set_status(503)
            self.write({'dates': [], 'error': 'Calendar temporarily unavailable'})
            return
        self.cache[key] = (time.monotonic(), dates)
        self.cache.move_to_end(key)
        while len(self.cache) > 256:
            self.cache.popitem(last=False)
        self.write({'dates': dates, 'ttl': self.ttl})


# 获得页面数据。
class GetStockHtmlHandler(webBase.BaseHandler, ABC):
    @gen.coroutine
    def get(self):
        name = self.get_argument("table_name", default=None, strip=False)
        web_module_data = sswmd.stock_web_module_data().get_data(name)
        run_date, run_date_nph = trd.get_trade_date_last(websrv=True)
        if web_module_data.is_realtime:
            date_now_str = run_date_nph.strftime("%Y-%m-%d")
        else:
            date_now_str = run_date.strftime("%Y-%m-%d")

        self.render("stock_web.html", web_module_data=web_module_data, date_now=date_now_str,
                    stockVersion=version.__version__,
                    leftMenu=webBase.GetLeftMenu(self.request.uri))


# 获得股票数据内容。
class GetStockDataHandler(webBase.BaseHandler, ABC):
    def get(self):
        # 获得分页参数。
        name = self.get_argument("name", default=None, strip=False)
        try:
            web_module_data = sswmd.stock_web_module_data().get_data(name)
            if web_module_data.mode != 'query':
                raise KeyError(name)
        except KeyError:
            raise HTTPError(400, reason='Invalid table') from None
        search_sql, order_by_sql, limit_sql, values, pagination = _query_parts(self, web_module_data)
        # https://datatables.net/manual/server-side
        self.set_header('Content-Type', 'application/json;charset=UTF-8')
        db = self.db
        draw_param = self.get_argument('draw', None)
        try:
            draw = int(draw_param) if draw_param is not None else None
        except (TypeError, ValueError):
            draw = 0
        if not _table_exists(db, web_module_data.table_name):
            obj = {"recordsTotal": 0, "recordsFiltered": 0, "data": []}
            if draw is not None:
                obj['draw'] = draw
            self.write(json.dumps(obj, cls=MyEncoder))
            return
        sql = " SELECT * FROM `%s` %s %s %s " % (web_module_data.table_name, search_sql, order_by_sql, limit_sql)
        count_sql = " SELECT count(1) as num FROM `%s` %s " % (web_module_data.table_name, search_sql)

        stock_web_list = db.query(sql, *(values + pagination))
        stock_web_size = db.query(count_sql, *values)

        obj = {
            "recordsTotal": stock_web_size[0]["num"],
            "recordsFiltered": stock_web_size[0]["num"],
            "data": stock_web_list
        }
        if draw is not None:
            obj['draw'] = draw
        self.write(json.dumps(obj, cls=MyEncoder))
