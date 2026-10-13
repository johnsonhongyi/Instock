"""Regression checks for the unpushed release, without production jobs or DBs."""
import ast
import contextlib
import concurrent.futures
from collections import Counter, OrderedDict
import datetime
import glob
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

import pandas as pd
import tornado.web
from tornado.testing import AsyncHTTPTestCase

from instock.job.scan_helpers import bounded_results

ROOT = Path(__file__).parents[1]


def definitions(path, names, namespace):
    # Execute actual functions/classes, excluding unrelated startup imports
    # that initialize market providers or require a live database.
    tree = ast.parse((ROOT / path).read_text(encoding='utf-8'))
    selected = [node for node in tree.body if getattr(node, 'name', '') in names]
    assert len(selected) == len(names)
    exec(compile(ast.Module(body=selected, type_ignores=[]), path, 'exec'), namespace)
    return namespace


# The provider's table metadata imports live strategy modules. Load only the
# actual cache primitives here so test discovery never starts those modules.
eastmoney_daily = ModuleType('eastmoney_cache_under_test')
eastmoney_daily.__dict__.update({
    'contextlib': contextlib, 'os': os, 'threading': threading, 'tempfile': tempfile,
    'json': json, 'logging': logging, 'pd': pd, 're': re, 'fcntl': None,
    '_CACHE_DIR': '', '_LOCAL_LOCK': threading.RLock(), '_is_after_close_today': Mock(return_value=False),
})
definitions('instock/core/eastmoney_daily.py',
    ['EastmoneyBlockedError', 'EastmoneyDataError', '_file_lock', '_atomic_json', '_read_json', '_daily_fetch'],
    eastmoney_daily.__dict__)


class DailyCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.patcher = patch.object(eastmoney_daily, '_CACHE_DIR', self.tmp.name)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_historical_cache_reused_without_live_fetch(self):
        saved = pd.DataFrame({'code': ['000001']})
        saved.to_pickle(self.cache / 'stock-fund-flow-2026-10-09.pkl')
        fetch = Mock(side_effect=AssertionError('must not fetch current quotes'))
        with patch.object(eastmoney_daily, '_is_after_close_today', return_value=False):
            result = eastmoney_daily._daily_fetch('stock-fund-flow', '2026-10-09', fetch)
        pd.testing.assert_frame_equal(result, saved)
        fetch.assert_not_called()

    def test_missing_historical_snapshot_never_fabricated(self):
        fetch = Mock()
        with patch.object(eastmoney_daily, '_is_after_close_today', return_value=False):
            self.assertIsNone(eastmoney_daily._daily_fetch('stock-fund-flow', '2026-10-09', fetch))
        fetch.assert_not_called()

    def test_retryable_markers_and_cache_hit(self):
        for status in ['blocked', 'deferred', 'failed', 'started']:
            dataset = 'sample-' + status
            (self.cache / f'{dataset}-2026-10-09.state').write_text(json.dumps({'status': status}))
            fetch = Mock(return_value={'result': status})
            self.assertEqual(eastmoney_daily._daily_fetch(dataset, '2026-10-09', fetch, after_close=False), {'result': status})
            eastmoney_daily._daily_fetch(dataset, '2026-10-09', fetch, after_close=False)
            fetch.assert_called_once()

    def test_malformed_marker_status_fails_closed(self):
        (self.cache / 'sample-2026-10-09.state').write_text(json.dumps({'status': []}))
        fetch = Mock()
        self.assertIsNone(eastmoney_daily._daily_fetch('sample', '2026-10-09', fetch, after_close=False))
        fetch.assert_not_called()


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / 'cache/eastmoney'
        self.cache.mkdir(parents=True)
        today = datetime.date(2026, 10, 13)
        class FixedDate(datetime.date):
            @classmethod
            def today(cls):
                return today
        self.block, self.bonus, self.flow = Mock(), Mock(), Mock()
        self.ns = definitions('instock/job/basic_data_other_daily_job.py', ['_retry_recent_external_data'], {
            'cpath_current': self.tmp.name, 'datetime': SimpleNamespace(date=FixedDate, timedelta=datetime.timedelta),
            'glob': glob, 'json': json, 'os': os, 'logging': logging,
            'save_stock_blocktrade_data': self.block, 'save_nph_stock_bonus': self.bonus,
            'save_nph_stock_fundflow_data': self.flow,
        })

    def marker(self, dataset, date, marker, cached=False):
        base = self.cache / f'{dataset}-{date}'
        base.with_suffix('.state').write_text(json.dumps(marker))
        if cached:
            base.with_suffix('.pkl').touch()

    def test_unsupported_newer_snapshot_does_not_starve_dated_recovery(self):
        self.marker('stock-fund-flow', '2026-10-12', {'status': 'blocked'})
        self.marker('stock-bonus', '2026-10-12', {'status': 'deferred'})
        self.marker('stock-blocktrade', '2026-10-09', {'status': 'failed'})
        self.ns['_retry_recent_external_data']()
        self.block.assert_called_once_with(datetime.date(2026, 10, 9))
        self.bonus.assert_not_called()
        self.flow.assert_not_called()

    def test_bad_markers_do_not_abort_valid_recovery(self):
        self.marker('stock-blocktrade', '2026-10-12', [])
        self.marker('stock-bonus', '2026-10-12', {'status': []}, cached=True)
        self.marker('stock-blocktrade', '2026-10-09', {'status': 'blocked'})
        self.marker('stock-blocktrade', '2026-09-01', {'status': 'blocked'})
        self.ns['_retry_recent_external_data']()
        self.block.assert_called_once_with(datetime.date(2026, 10, 9))

    def test_failed_dataset_does_not_stop_cached_snapshot_recovery(self):
        self.marker('stock-blocktrade', '2026-10-12', {'status': 'blocked'})
        self.marker('stock-bonus', '2026-10-12', {'status': 'deferred'}, cached=True)
        self.block.side_effect = RuntimeError('isolated fetch failure')
        with self.assertLogs(level='ERROR'):
            self.ns['_retry_recent_external_data']()
        self.bonus.assert_called_once_with(datetime.date(2026, 10, 12), before=False)

    def test_dated_block_trade_fetch_allows_past_but_gates_today(self):
        fetch_once = Mock(return_value=None)
        ns = definitions('instock/core/stockfetch.py', ['fetch_stock_blocktrade_data'], {
            'datetime': datetime, 'logging': logging, 'fetch_external_once': fetch_once,
            'sde': Mock(), 'tbs': Mock(), 'is_a_stock': Mock(),
        })
        past = datetime.date.today() - datetime.timedelta(days=3)
        for value, gated in [(past, False), (datetime.datetime.now(), True)]:
            ns['fetch_stock_blocktrade_data'](value)
            self.assertEqual(fetch_once.call_args.kwargs['after_close'], gated)


class StrategySafetyTests(unittest.TestCase):
    def test_incomplete_failed_or_empty_scan_preserves_existing_rows(self):
        for stocks, scan in [
            ({'one': 1, 'two': 2}, {'checked': 1, 'matched': 0, 'errors': 0}),
            ({'one': 1}, {'checked': 1, 'matched': 0, 'errors': 1}),
            ({}, {'checked': 0, 'matched': 0, 'errors': 0}),
        ]:
            with self.subTest(scan=scan):
                context, db = threading.local(), Mock()
                def run_check(*args):
                    context.last_scan = scan
                    return None
                ns = definitions('instock/job/strategy_data_daily_job.py', ['prepare'], {
                    'time': time, 'logging': logging, '_last_scan': {}, '_scan_context': context,
                    '_run_stats': None, '_run_errors': [], 'mdb': db, 'run_check': run_check,
                    'stock_hist_data': lambda date: SimpleNamespace(get_data=lambda: stocks),
                })
                ns['prepare'](datetime.date(2026, 10, 9), {'name': 'cn_test', 'func': Mock()})
                db.executeSql.assert_not_called()

    def test_concurrent_failed_scan_cannot_use_other_dates_clean_statistics(self):
        bad_date, clean_date = datetime.date(2026, 10, 9), datetime.date(2026, 10, 12)
        bad_finished, clean_finished = threading.Event(), threading.Event()
        db = Mock()
        db.checkTableIsExist.return_value = True
        ns = definitions('instock/job/strategy_data_daily_job.py', ['prepare', 'run_check'], {
            'logging': logging, 'time': time, 'concurrent': concurrent, 'Counter': Counter,
            '_scan_context': threading.local(), '_last_scan': {}, '_run_stats': None, '_run_errors': [],
            'stock_hist_data': lambda date: SimpleNamespace(get_data=lambda: {('market', '000001'): object()}),
            'mdb': db, 'bounded_results': bounded_results,
        })
        original = ns['run_check']
        def interleave(function, table, stocks, date):
            if date == clean_date:
                self.assertTrue(bad_finished.wait(5))
            result = original(function, table, stocks, date)
            if date == bad_date:
                bad_finished.set()
                self.assertTrue(clean_finished.wait(5))
            else:
                clean_finished.set()
            return result
        ns['run_check'] = interleave
        def check(stock, data, date):
            if date == bad_date:
                raise RuntimeError('missing historical evidence')
            return False
        strategy = {'name': 'cn_test', 'func': check}
        with self.assertLogs(level='ERROR'), concurrent.futures.ThreadPoolExecutor(2) as pool:
            bad = pool.submit(ns['prepare'], bad_date, strategy)
            good = pool.submit(ns['prepare'], clean_date, strategy)
            bad.result(timeout=10)
            good.result(timeout=10)
        db.executeSql.assert_called_once_with('DELETE FROM `cn_test` WHERE `date` = %s', ('2026-10-12',))


class TableQueryTests(AsyncHTTPTestCase):
    def get_app(self):
        self.module = SimpleNamespace(table_name='cn_test', columns=('date', 'code', 'name'), mode='query')
        modules = {'cn_test': self.module}
        registry = SimpleNamespace(get_data=lambda name: modules[name], get_data_list=lambda: list(modules.values()))
        class Base(tornado.web.RequestHandler):
            @property
            def db(self):
                return self.application.db
        ns = definitions('instock/web/dataTableHandler.py',
            ['MyEncoder', '_table_exists', '_query_parts', 'GetStockDataHandler', 'GetStockDatesHandler'], {
                'json': json, 'datetime': datetime, 'time': time, 'logging': logging,
                'OrderedDict': OrderedDict, 're': re, 'HTTPError': tornado.web.HTTPError,
                'ABC': object, 'webBase': SimpleNamespace(BaseHandler=Base),
                'sswmd': SimpleNamespace(stock_web_module_data=lambda: registry),
            })
        app = tornado.web.Application([('/data', ns['GetStockDataHandler']), ('/dates', ns['GetStockDatesHandler'])])
        self.exists = True
        self.calls = []
        def query(sql, *values):
            self.calls.append((sql, values))
            if 'information_schema' in sql:
                return [{'exists': 1}] if self.exists else []
            if 'DISTINCT' in sql:
                return [{'date': datetime.date(2026, 10, 9)}]
            if 'count(1)' in sql:
                return [{'num': 1}]
            return [{'code': '000001', 'date': datetime.date(2026, 10, 9)}]
        app.db = SimpleNamespace(query=query)
        return app

    def test_filter_date_and_pagination_are_bound_values(self):
        attack = "' OR 1=1 --"
        response = self.fetch('/data?' + urlencode({'name': 'cn_test', 'draw': '4',
            'columns[2][search][value]': attack, 'columns[0][search][value]': '2026-10-09',
            'date': 'ignored-date', 'start': '10', 'length': '15',
            'order[0][column]': '1', 'order[0][dir]': 'desc'}))
        self.assertEqual(response.code, 200)
        self.assertEqual(json.loads(response.body)['draw'], 4)
        sql, values = next(call for call in self.calls if 'SELECT *' in call[0])
        self.assertNotIn(attack, sql)
        self.assertNotIn('2026-10-09', sql)
        self.assertNotIn('==', sql)
        self.assertEqual(values, ('%' + attack + '%', '2026-10-09', 10, 15))
        self.assertIn('`code` DESC', sql)

    def test_invalid_sort_column_pagination_or_table_returns_400(self):
        for fields in [
            {'order[0][column]': '1', 'order[0][dir]': 'desc; DROP TABLE x'},
            {'order[0][column]': '-1', 'order[0][dir]': 'asc'},
            {'columns[9][search][value]': 'x'}, {'start': '0 OR 1=1'},
            {'start': '-1'}, {'length': '-2'}, {'name': 'unknown'},
        ]:
            with self.subTest(fields=fields):
                self.assertEqual(self.fetch('/data?' + urlencode({'name': 'cn_test', **fields})).code, 400)
        self.assertEqual(self.calls, [])

    def test_missing_table_returns_empty_draw(self):
        self.exists = False
        response = self.fetch('/data?name=cn_test&draw=7')
        self.assertEqual(response.code, 200)
        self.assertEqual(json.loads(response.body), {'recordsTotal': 0, 'recordsFiltered': 0, 'data': [], 'draw': 7})
        self.assertEqual(len(self.calls), 1)

    def test_calendar_accepts_basic_table_and_handles_missing_table(self):
        response = self.fetch('/dates?name=cn_test&month=2026-10')
        self.assertEqual(response.code, 200)
        self.assertEqual(json.loads(response.body)['dates'], ['2026-10-09'])
        self.assertEqual(self.calls[-1][1], (datetime.date(2026, 9, 24), datetime.date(2026, 11, 8)))
        self.exists = False
        response = self.fetch('/dates?name=cn_test&month=2026-09')
        self.assertEqual(json.loads(response.body)['dates'], [])


if __name__ == '__main__':
    unittest.main()
