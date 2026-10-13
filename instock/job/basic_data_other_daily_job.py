#!/usr/local/bin/python3
# -*- coding: utf-8 -*-

import logging
import os.path
import sys
import datetime
import glob
import json
import pandas as pd

cpath_current = os.path.dirname(os.path.dirname(__file__))
cpath = os.path.abspath(os.path.join(cpath_current, os.pardir))
sys.path.append(cpath)
import instock.lib.run_template as runt
import instock.core.tablestructure as tbs
import instock.lib.database as mdb
import instock.core.stockfetch as stf

__author__ = 'myh '
__date__ = '2023/3/10 '


# 每日股票龙虎榜
def save_nph_stock_top_data(date, before=True):
    if before:
        return

    try:
        data = stf.fetch_stock_top_data(date)
        if data is None or len(data.index) == 0:
            return

        table_name = tbs.TABLE_CN_STOCK_TOP['name']
        # 删除老数据。
        if mdb.checkTableIsExist(table_name):
            del_sql = f"DELETE FROM `{table_name}` where `date` = '{date}'"
            mdb.executeSql(del_sql)
            cols_type = None
        else:
            cols_type = tbs.get_field_types(tbs.TABLE_CN_STOCK_TOP['columns'])
        mdb.insert_db_from_df(data, table_name, cols_type, False, "`date`,`code`")
    except Exception as e:
        logging.error(f"basic_data_other_daily_job.save_stock_top_data处理异常：{e}")


# 每日股票大宗交易
def save_stock_blocktrade_data(date):
    try:
        data = stf.fetch_stock_blocktrade_data(date)
        if data is None or len(data.index) == 0:
            return

        table_name = tbs.TABLE_CN_STOCK_BLOCKTRADE['name']
        # 删除老数据。
        if mdb.checkTableIsExist(table_name):
            del_sql = f"DELETE FROM `{table_name}` where `date` = '{date}'"
            mdb.executeSql(del_sql)
            cols_type = None
        else:
            cols_type = tbs.get_field_types(tbs.TABLE_CN_STOCK_BLOCKTRADE['columns'])

        mdb.insert_db_from_df(data, table_name, cols_type, False, "`date`,`code`")
    except Exception as e:
        logging.error(f"basic_data_other_daily_job.save_stock_blocktrade_data处理异常：{e}")


# 每日股票资金流向
def save_nph_stock_fundflow_data(date, before=True):
    if before:
        return

    try:
        times = tuple(range(4))
        results = run_check(times, date)
        if results is None:
            return

        for t in times:
            if t == 0:
                data = results.get(t)
            else:
                r = results.get(t)
                if r is not None:
                    r.drop(columns=['name', 'new_price'], inplace=True)
                    data = pd.merge(data, r, on=['code'], how='left')

        if data is None or len(data.index) == 0:
            return

        data.insert(0, 'date', date.strftime("%Y-%m-%d"))

        table_name = tbs.TABLE_CN_STOCK_FUND_FLOW['name']
        # 删除老数据。
        if mdb.checkTableIsExist(table_name):
            del_sql = f"DELETE FROM `{table_name}` where `date` = '{date}'"
            mdb.executeSql(del_sql)
            cols_type = None
        else:
            cols_type = tbs.get_field_types(tbs.TABLE_CN_STOCK_FUND_FLOW['columns'])

        mdb.insert_db_from_df(data, table_name, cols_type, False, "`date`,`code`")
    except Exception as e:
        logging.error(f"basic_data_other_daily_job.save_nph_stock_fundflow_data处理异常：{e}")


def run_check(times, date):
    data = {}
    try:
        for _time in times:
            try:
                _data_ = stf.fetch_stocks_fund_flow(_time, date)
                if _data_ is not None:
                    data[_time] = _data_
            except Exception as e:
                logging.error(f"basic_data_other_daily_job.run_check处理异常：代码{e}")
    except Exception as e:
        logging.error(f"basic_data_other_daily_job.run_check处理异常：{e}")
    if not data:
        return None
    return data


# 每日股票分红配送
def save_nph_stock_bonus(date, before=True):
    if before:
        return

    try:
        data = stf.fetch_stocks_bonus(date)
        if data is None or len(data.index) == 0:
            return

        table_name = tbs.TABLE_CN_STOCK_BONUS['name']
        # 删除老数据。
        if mdb.checkTableIsExist(table_name):
            del_sql = f"DELETE FROM `{table_name}` where `date` = '{date}'"
            mdb.executeSql(del_sql)
            cols_type = None
        else:
            cols_type = tbs.get_field_types(tbs.TABLE_CN_STOCK_BONUS['columns'])
        mdb.insert_db_from_df(data, table_name, cols_type, False, "`date`,`code`")
    except Exception as e:
        logging.error(f"basic_data_other_daily_job.save_nph_stock_bonus处理异常：{e}")


# 基本面选股
def stock_spot_buy(date):
    try:
        _table_name = tbs.TABLE_CN_STOCK_SPOT['name']
        if not mdb.checkTableIsExist(_table_name):
            return

        from sqlalchemy import text
        with mdb.engine().connect() as connection:
            available = connection.execute(text(
                f'SELECT COUNT(*) FROM `{_table_name}` WHERE `date` = :date '
                'AND pe9 IS NOT NULL AND pe9 <> 0 AND pbnewmrq IS NOT NULL '
                'AND pbnewmrq <> 0 AND roe_weight IS NOT NULL'), {'date': date}).scalar()
        if int(available or 0) < 3000:
            from instock.core.fundamental_daily import enrich_spot_fundamentals
            if not enrich_spot_fundamentals(date):
                logging.error('basic stock selection skipped: financial snapshot unavailable for %s', date)
                return

        sql = f'''SELECT * FROM `{_table_name}` WHERE `date` = '{date}' and 
                `pe9` > 0 and `pe9` <= 20 and `pbnewmrq` <= 10 and `roe_weight` >= 15'''
        data = pd.read_sql(sql=sql, con=mdb.engine())
        data = data.drop_duplicates(subset="code", keep="last")
        table_name = tbs.TABLE_CN_STOCK_SPOT_BUY['name']
        # 删除老数据。
        if mdb.checkTableIsExist(table_name):
            with mdb.engine().begin() as connection:
                connection.execute(text(f'DELETE FROM `{table_name}` WHERE `date` = :date'), {'date': date})
                if not data.empty:
                    data.to_sql(table_name, con=connection, if_exists='append', index=False)
        else:
            cols_type = tbs.get_field_types(tbs.TABLE_CN_STOCK_SPOT_BUY['columns'])
            if not data.empty:
                mdb.insert_db_from_df(data, table_name, cols_type, False, "`date`,`code`")
        logging.info('basic stock selection complete: date=%s matched=%s', date, len(data))
    except Exception as e:
        logging.error(f"basic_data_other_daily_job.stock_spot_buy处理异常：{e}")


def _retry_recent_external_data():
    cache_dir = os.path.join(cpath_current, 'cache', 'eastmoney')
    today = datetime.date.today()
    oldest = today - datetime.timedelta(days=10)
    fetchers = {
        'stock-blocktrade': save_stock_blocktrade_data,
        'stock-bonus': lambda date: save_nph_stock_bonus(date, before=False),
        'stock-fund-flow': lambda date: save_nph_stock_fundflow_data(date, before=False),
    }
    pending = {}
    for dataset in fetchers:
        prefix = dataset + '-'
        for marker_path in glob.glob(os.path.join(cache_dir, prefix + '*.state')):
            date_text = os.path.basename(marker_path)[len(prefix):-len('.state')]
            try:
                marker_date = datetime.date.fromisoformat(date_text)
                with open(marker_path, 'r', encoding='utf-8') as stream:
                    marker = json.load(stream)
                status = marker.get('status') if isinstance(marker, dict) else None
            except (OSError, ValueError, TypeError):
                continue
            cached = os.path.isfile(os.path.splitext(marker_path)[0] + '.pkl')
            # Only block trades have a genuine dated historical API. Bonus and
            # fund-flow history may be restored only from their saved snapshot.
            recoverable = dataset == 'stock-blocktrade' or cached
            if (oldest <= marker_date < today and recoverable and isinstance(status, str)
                    and status in {'blocked', 'deferred', 'failed', 'started'}):
                pending.setdefault(marker_date, set()).add(dataset)

    if not pending:
        return
    # Recover the newest missed session first; existing fetch and persistence paths own the work.
    retry_date = max(pending)
    for dataset, fetcher in fetchers.items():
        if dataset in pending[retry_date]:
            logging.info('retrying deferred external data: dataset=%s date=%s', dataset, retry_date)
            try:
                fetcher(retry_date)
            except Exception:
                logging.exception('external recovery failed: dataset=%s date=%s', dataset, retry_date)


def main():
    runt.run_with_args(stock_spot_buy)
    runt.run_with_args(save_nph_stock_top_data)
    runt.run_with_args(save_stock_blocktrade_data)
    runt.run_with_args(save_nph_stock_bonus)
    runt.run_with_args(save_nph_stock_fundflow_data)
    if len(sys.argv) == 1:
        _retry_recent_external_data()


# main函数入口
if __name__ == '__main__':
    main()
