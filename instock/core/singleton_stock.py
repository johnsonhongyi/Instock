#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import concurrent.futures
from itertools import islice
from threading import RLock
import instock.core.stockfetch as stf
import instock.core.tablestructure as tbs
import instock.lib.trade_time as trd
from instock.lib.singleton_type import singleton_type

__author__ = 'myh '
__date__ = '2023/3/10 '


# 读取当天股票数据
class stock_data(metaclass=singleton_type):
    def __init__(self, date):
        self._lock = RLock()
        self._loaded_date = None
        self._loaded = False
        self.data = None
        self._load(date)

    @staticmethod
    def _date_key(date):
        return date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)[:10]

    def _load(self, date, refresh=False):
        date_key = self._date_key(date)
        with self._lock:
            if not refresh and self._loaded and self._loaded_date == date_key:
                return
            if self._loaded_date != date_key:
                self.data = None
                self._loaded = False
            try:
                result = stf.fetch_stocks(date)
                if result is not None and not result.empty:
                    self.data = result
                    self._loaded = True
                    self._loaded_date = date_key
                elif refresh:
                    self.data = None
                    self._loaded = False
                    logging.error('singleton.stock_data fresh Sina snapshot unavailable for %s', date_key)
            except Exception as e:
                logging.error(f"singleton.stock_data处理异常：{e}")
                if refresh:
                    self.data = None
                    self._loaded = False

    def get_data(self, date=None, refresh=False):
        if date is not None:
            self._load(date, refresh=refresh)
        return self.data


# 读取股票历史数据
class stock_hist_data(metaclass=singleton_type):
    def __init__(self, date=None, stocks=None, workers=2):
        self._lock = RLock()
        self._full_data = None
        self._full_loaded_date = None
        self._subset_cache = {}
        self.data = None
        self._load(date, stocks, workers)

    @staticmethod
    def _date_key(date):
        return date.strftime("%Y-%m-%d") if hasattr(date, "strftime") else str(date)[:10]

    def _fetch_hist_batch(self, stocks, workers=2):
        if not stocks:
            return {}
        date_start, is_cache = trd.get_trade_hist_interval(self._date_key(stocks[0][0]))
        _data = {}
        worker_count = max(1, min(int(workers), 2))
        batch_size = worker_count * 4
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                stock_iter = iter(stocks)
                while True:
                    batch = list(islice(stock_iter, batch_size))
                    if not batch:
                        break
                    future_to_stock = {
                        executor.submit(stf.fetch_stock_hist, stock, date_start, is_cache): stock
                        for stock in batch
                    }
                    for future in concurrent.futures.as_completed(future_to_stock):
                        stock = future_to_stock[future]
                        try:
                            history = future.result()
                            if history is not None:
                                _data[stock] = history
                        except Exception as e:
                            logging.error("singleton.stock_hist_data处理异常：%s代码%s", stock[1], e)
        except Exception as e:
            logging.error(f"singleton.stock_hist_data处理异常：{e}")
        return _data

    def _load(self, date=None, stocks=None, workers=2):
        with self._lock:
            date_key = self._date_key(date)

            # 场景 1: 全量请求 (stocks is None)
            if stocks is None:
                if self._full_loaded_date == date_key and self._full_data is not None:
                    self.data = self._full_data
                    return

                snapshot = stock_data(date).get_data(date)
                if snapshot is None or snapshot.empty:
                    logging.error("singleton.stock_hist_data没有%s的股票行情列表", date)
                    self.data = None
                    self._full_data = None
                    self._full_loaded_date = date_key
                    return

                subset = snapshot[list(tbs.TABLE_CN_STOCK_FOREIGN_KEY['columns'])]
                full_stocks = [tuple(row) for row in subset.values]
                if not full_stocks:
                    self.data = None
                    self._full_data = None
                    self._full_loaded_date = date_key
                    return

                _data = self._fetch_hist_batch(full_stocks, workers=workers)
                minimum = max(1, int(len(full_stocks) * 0.7))
                if len(_data) < minimum:
                    logging.error("singleton.stock_hist_data TDX历史数据覆盖不足: %s/%s", len(_data), len(full_stocks))
                    self._full_data = None
                    self.data = None
                else:
                    self._full_data = _data
                    self._full_loaded_date = date_key
                    self.data = _data
                return

            # 场景 2: 局部候选股请求 (stocks is not None)
            if len(stocks) == 0:
                self.data = {}
                return

            # 2.1 若全量数据已就绪，直接从内存全量数据中按 code 检索过滤（0 磁盘/网络 I/O）
            if self._full_loaded_date == date_key and self._full_data is not None:
                requested_codes = {str(stock[1]).split('.')[0].zfill(6) for stock in stocks}
                filtered = {stock: frame for stock, frame in self._full_data.items()
                            if str(stock[1]).split('.')[0].zfill(6) in requested_codes}
                if len(filtered) >= max(1, int(len(stocks) * 0.7)):
                    self.data = filtered
                    return

            # 2.2 全量数据未就绪，检查局部缓存
            cache_codes = tuple(sorted(str(stock[1]).split('.')[0].zfill(6) for stock in stocks))
            cache_key = (date_key, cache_codes)
            if cache_key in self._subset_cache:
                self.data = self._subset_cache[cache_key]
                return

            # 2.3 执行拉取，结果仅写入局部缓存，绝不污染 self._full_data
            _data = self._fetch_hist_batch(stocks, workers=workers)
            minimum = max(1, int(len(stocks) * 0.7))
            if len(_data) < minimum:
                logging.error("singleton.stock_hist_data TDX历史数据覆盖不足: %s/%s", len(_data), len(stocks))
                self.data = None
            else:
                if len(self._subset_cache) > 16:
                    self._subset_cache.clear()
                self._subset_cache[cache_key] = _data
                self.data = _data

    def get_data(self, date=None, stocks=None, workers=2):
        if date is not None:
            self._load(date, stocks, workers)
        return self.data
