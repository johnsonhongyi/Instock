"""Fetch a dated bulk financial snapshot without substituting missing values with zero."""

import logging

import pandas as pd
from sqlalchemy import text

import instock.lib.database as mdb
from instock.core.eastmoney_daily import fetch_external_once, request_eastmoney_json


def fetch_fundamentals(date):
    date_key = date.isoformat() if hasattr(date, 'isoformat') else str(date)[:10]

    def fetch():
        rows = []
        page = 1
        while True:
            payload = request_eastmoney_json(
                'https://datacenter-web.eastmoney.com/wstock/selection/api/data/get',
                {'type': 'RPTA_PCNEW_STOCKSELECT',
                 'sty': 'SECUCODE,SECURITY_CODE,NEW_PRICE,PE9,PBNEWMRQ,ROE_WEIGHT,REPORT_DATE',
                 'filter': '(NEW_PRICE>0)(PE9>0)(PE9<=20)(PBNEWMRQ<=10)(ROE_WEIGHT>=15)',
                 'p': page, 'ps': 500, 'st': 'SECURITY_CODE', 'sr': 1,
                 'source': 'SELECT_SECURITIES', 'client': 'WEB'},
                'https://data.eastmoney.com/xuangu/', required_key='result')
            result = payload.get('result') or {}
            batch = result.get('data') or []
            if result.get('nextpage') not in (True, False):
                raise ValueError('Missing financial pagination status')
            if not batch and result['nextpage'] is False and page == 1:
                check = request_eastmoney_json(
                    'https://datacenter-web.eastmoney.com/wstock/selection/api/data/get',
                    {'type': 'RPTA_PCNEW_STOCKSELECT',
                     'sty': 'SECUCODE,SECURITY_CODE,NEW_PRICE', 'filter': '(NEW_PRICE>0)',
                     'p': 1, 'ps': 1, 'source': 'SELECT_SECURITIES', 'client': 'WEB'},
                    'https://data.eastmoney.com/xuangu/', required_key='result')
                sample = (check.get('result') or {}).get('data') or []
                if not sample or str(sample[0].get('MAX_TRADE_DATE', ''))[:10] != date_key:
                    raise ValueError('Cannot verify date of empty selection')
                return {'date': date_key, 'rows': []}
            if not batch or page > 100:
                raise ValueError('Incomplete financial snapshot')
            rows.extend(batch)
            if result['nextpage'] is False:
                break
            page += 1
        frame = pd.DataFrame(rows).rename(columns={
            'SECURITY_CODE': 'code', 'NEW_PRICE': 'reference_price', 'PE9': 'pe9',
            'PBNEWMRQ': 'pbnewmrq', 'ROE_WEIGHT': 'roe_weight', 'REPORT_DATE': 'report_date'})
        if not frame['MAX_TRADE_DATE'].astype(str).str[:10].eq(date_key).all():
            raise ValueError('Financial provider snapshot does not match requested date ' + date_key)
        frame['code'] = frame['code'].astype(str).str.zfill(6)
        frame = frame.drop_duplicates('code', keep='last')
        for column in ['reference_price', 'pe9', 'pbnewmrq', 'roe_weight']:
            frame[column] = pd.to_numeric(frame[column], errors='coerce')
        frame['report_date'] = pd.to_datetime(frame['report_date'], errors='coerce').dt.date
        if len(frame) != len(rows):
            raise ValueError('Incomplete financial selection snapshot')
        if not frame[['pe9', 'pbnewmrq', 'roe_weight']].notna().all(axis=1).all():
            raise ValueError('Selected stocks have missing financial fields')
        return {'date': date_key, 'rows': frame[
            ['code', 'reference_price', 'pe9', 'pbnewmrq', 'roe_weight', 'report_date']].to_dict('records')}

    return fetch_external_once('stock-fundamentals-selected-v2', date_key, fetch, after_close=False)


def enrich_spot_fundamentals(date):
    """Update only financial fields, atomically; leave quote prices and volume untouched."""
    date_key = date.isoformat() if hasattr(date, 'isoformat') else str(date)[:10]
    snapshot = fetch_fundamentals(date)
    if snapshot is None or snapshot.get('date') != date_key:
        logging.error('fundamentals unavailable for %s; preserve existing financial data', date_key)
        return False
    records = []
    for row in snapshot['rows']:
        if pd.isna(row['reference_price']) or row['reference_price'] <= 0:
            continue
        record = {key: None if pd.isna(value) else value for key, value in row.items()}
        record['date'] = date_key
        records.append(record)
    with mdb.engine().begin() as connection:
        # Unknown financial values from the quote-only feed must not appear as real zeros.
        connection.execute(text(
            'UPDATE cn_stock_spot SET pe9=NULL,pbnewmrq=NULL,roe_weight=NULL '
            'WHERE date=:date AND (pe9=0 AND pbnewmrq=0 AND roe_weight=0)'), {'date': date_key})
        if records:
            connection.execute(text(
            'UPDATE cn_stock_spot SET pe9=:pe9, pbnewmrq=:pbnewmrq, '
            'roe_weight=:roe_weight, report_date=:report_date '
            'WHERE date=:date AND code=:code'), records)
    logging.info('fundamentals enriched: date=%s provider_rows=%s', date_key, len(records))
    return True
