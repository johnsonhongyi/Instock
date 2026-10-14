"""Isolated regressions for prepared history and realtime overlays."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepared_under_test', ROOT / 'instock/JSONData/prepared_history.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


class PreparedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name, '000001-qfq.pkl')
        self.frame = pd.DataFrame({'date': pd.date_range('2024-01-01', periods=650).strftime('%Y-%m-%d'),
                                   'close': np.arange(650, dtype=float) + 10})
        self.signature = ('20240101', '2026-10-13', 'qfq', ('date', 'close'), 'source', (1, 2))
        counter = types.ModuleType('instock.JSONData.history_cache')
        counter._count = lambda name: None
        self.env = patch.dict(os.environ, {'INSTOCK_HISTORY_CACHE_EPOCH': '2026-10-13',
                                           'INSTOCK_HIST_LOOKBACK_ROWS': '600'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.mods = patch.dict(sys.modules, {'instock.JSONData.history_cache': counter})
        self.mods.start()
        self.addCleanup(self.mods.stop)
        store._METADATA_CACHE.clear()
        store._GLOBAL_MANIFEST.clear()

    def warm(self):
        return store.prepared_history(self.path, self.signature, lambda: self.frame.copy())

    def hit(self, signature=None):
        return store.prepared_history(self.path, signature or self.signature,
                                     lambda: self.fail('unexpected source read'))

    def test_legacy_manifest_cannot_shadow_current_600_profile(self):
        self.warm()
        meta = self.path.with_name('000001-qfq-600.meta.json')
        stale = json.loads(meta.read_text())
        stale['version'] = 'old'
        stale['source_version'] = 'old'
        Path(self.tmp.name, 'manifest.json').write_text(json.dumps({'000001': stale}))
        pd.testing.assert_frame_equal(self.hit(), self.frame.tail(600).reset_index(drop=True))

    def test_manifest_tracks_each_profile_and_atomic_replacement(self):
        self.warm()
        meta = self.path.with_name('000001-qfq-600.meta.json')
        old_meta = meta.with_name('000001-qfq-1000.meta.json')
        old = json.loads(meta.read_text())
        old['version'] = 'old'
        old_meta.write_text(json.dumps(old))
        store.save_manifest(self.tmp.name)
        manifest = json.loads(Path(self.tmp.name, 'manifest.json').read_text())
        self.assertIn(meta.name, manifest)
        self.assertIn(old_meta.name, manifest)
        self.assertTrue(store.is_history_cache_ready(self.tmp.name, min_entries=1))
        self.assertFalse(store.is_history_cache_ready(self.tmp.name, min_entries=2))
        self.hit()
        changed = self.signature[:-1] + ((2, 3),)
        calls = []
        store.prepared_history(self.path, changed, lambda: calls.append(1) or self.frame.copy())
        self.assertEqual(calls, [1])
        self.hit(changed)

    def test_same_epoch_source_change_must_reload(self):
        self.warm()
        changed = self.signature[:-1] + ((1, 3),)
        calls = []
        store.prepared_history(self.path, changed, lambda: calls.append(1) or self.frame.copy())
        self.assertEqual(calls, [1])

    def test_unchanged_source_reused_next_epoch_and_bounds_applied(self):
        self.warm()
        os.environ['INSTOCK_HISTORY_CACHE_EPOCH'] = '2026-10-14'
        self.hit()
        bounded = ('20250101', '2025-06-01') + self.signature[2:]
        actual = self.hit(bounded)
        expected = self.frame.loc[self.frame.date.between('2025-01-01', '2025-06-01')].reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, expected)


class OverlayTests(unittest.TestCase):
    def overlay(self, quotes):
        path = ROOT / 'instock/core/stockfetch.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        node = next(n for n in tree.body if getattr(n, 'name', '') == 'stocks_data_to_realtime')
        columns = ['date', 'open', 'close', 'high', 'low', 'volume', 'amount',
                   'amplitude', 'quote_change', 'ups_downs', 'turnover']
        import logging
        scope = dict(pd=pd, np=np, logging=logging,
                     tbs=types.SimpleNamespace(CN_STOCK_HIST_DATA={'columns': columns}),
                     tl=types.SimpleNamespace(ROC=lambda a, n: pd.Series(a).pct_change().to_numpy() * 100),
                     _normalize_talib_columns=lambda frame: frame)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
        frame = pd.DataFrame({'date': pd.to_datetime(['2026-10-12', '2026-10-13']), 'close': [10., 10.5]})
        key = ('2026-10-13', '000001', 'stock')
        result = scope['stocks_data_to_realtime']('2026-10-13', {key: frame}, quotes)
        self.assertEqual(frame.close.tolist(), [10., 10.5])
        return result

    def quote(self):
        return pd.DataFrame([dict(date='2026-10-13', code='000001', new_price=11.,
            open_price=10., high_price=12., low_price=9., volume=100.,
            deal_amount=1100., turnoverrate=1., change_rate=10.,
            amplitude=30., ups_downs=1., pre_close_price=10.)])

    def test_overlay_replaces_today_and_preserves_history(self):
        result = next(iter(self.overlay(self.quote()).values()))
        self.assertEqual(result.close.tolist(), [10., 11.])
        self.assertAlmostEqual(result.p_change.iloc[-1], 10.)
        self.assertEqual(result.date.iloc[-1], pd.Timestamp('2026-10-13'))

    def test_stale_and_zero_quotes_do_not_create_today_bar(self):
        for column, value in [('date', '2026-10-12'), ('new_price', 0.)]:
            quotes = self.quote()
            quotes[column] = value
            self.assertEqual(self.overlay(quotes), {})


if __name__ == '__main__':
    unittest.main()
