#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import os
import datetime
import numpy as np
import pandas as pd
import pymysql
from sqlalchemy import create_engine
from sqlalchemy.types import NVARCHAR
from sqlalchemy import inspect

__author__ = 'myh '
__date__ = '2023/3/10 '

db_host = "localhost"  # 数据库服务主机
db_user = "root"  # 数据库访问用户
db_password = "root"  # 数据库访问密码
db_database = "instockdb"  # 数据库名称
db_port = 3306  # 数据库服务端口
db_charset = "utf8mb4"  # 数据库字符集

# 使用环境变量获得数据库,docker -e 传递
_db_host = os.environ.get('db_host')
if _db_host is not None:
    db_host = _db_host
_db_user = os.environ.get('db_user')
if _db_user is not None:
    db_user = _db_user
_db_password = os.environ.get('db_password')
if _db_password is not None:
    db_password = _db_password
_db_database = os.environ.get('db_database')
if _db_database is not None:
    db_database = _db_database
_db_port = os.environ.get('db_port')
if _db_port is not None:
    db_port = int(_db_port)

MYSQL_CONN_URL = "mysql+pymysql://%s:%s@%s:%s/%s?charset=%s" % (
    db_user, db_password, db_host, db_port, db_database, db_charset)
logging.info(f"数据库链接信息：{ MYSQL_CONN_URL}")

MYSQL_CONN_DBAPI = {'host': db_host, 'user': db_user, 'password': db_password, 'database': db_database,
                    'charset': db_charset, 'port': db_port, 'autocommit': True}

MYSQL_CONN_TORNDB = {'host': f'{db_host}:{str(db_port)}', 'user': db_user, 'password': db_password,
                     'database': db_database, 'charset': db_charset, 'max_idle_time': 3600, 'connect_timeout': 1000}


# 通过数据库链接 engine
def engine():
    return create_engine(MYSQL_CONN_URL)


def engine_to_db(to_db):
    _engine = create_engine(MYSQL_CONN_URL.replace(f'/{db_database}?', f'/{to_db}?'))
    return _engine


# DB Api -数据库连接对象connection，有游标
def get_connection():
    try:
        return pymysql.connect(**MYSQL_CONN_DBAPI)
    except Exception as e:
        logging.error(f"database.conn_not_cursor处理异常：{MYSQL_CONN_DBAPI}{e}")
    return None


# 定义通用方法函数，插入数据库表，并创建数据库主键，保证重跑数据的时候索引唯一。
def insert_db_from_df(data, table_name, cols_type, write_index, primary_keys):
    # 插入默认的数据库。
    insert_other_db_from_df(None, data, table_name, cols_type, write_index, primary_keys)


# 增加一个插入到其他数据库的方法。
def insert_other_db_from_df(to_db, data, table_name, cols_type, write_index, primary_keys):
    # 定义engine
    if to_db is None:
        engine_mysql = engine()
    else:
        engine_mysql = engine_to_db(to_db)
    # 使用 http://docs.sqlalchemy.org/en/latest/core/reflection.html
    # 使用检查检查数据库表是否有主键。
    ipt = inspect(engine_mysql)
    col_name_list = data.columns.tolist()
    # 如果有索引，把索引增加到varchar上面。
    if write_index:
        # 插入到第一个位置：
        col_name_list.insert(0, data.index.name)
    try:
        if cols_type is None:
            data.to_sql(name=table_name, con=engine_mysql, schema=to_db, if_exists='append',
                        index=write_index, )
        elif not cols_type:
            data.to_sql(name=table_name, con=engine_mysql, schema=to_db, if_exists='append',
                        dtype={col_name: NVARCHAR(255) for col_name in col_name_list}, index=write_index, )
        else:
            data.to_sql(name=table_name, con=engine_mysql, schema=to_db, if_exists='append',
                        dtype=cols_type, index=write_index, )
    except Exception as e:
        logging.error(f"database.insert_other_db_from_df处理异常：{table_name}表{e}")

    # 判断是否存在主键
    if not ipt.get_pk_constraint(table_name)['constrained_columns']:
        try:
            # 执行数据库插入数据。
            with get_connection() as conn:
                with conn.cursor() as db:
                    db.execute(f'ALTER TABLE `{table_name}` ADD PRIMARY KEY ({primary_keys});')
        except Exception as e:
            logging.error(f"database.insert_other_db_from_df处理异常：{table_name}表{e}")


def _sanitize_db_value(v):
    if v is None:
        return None
    if pd.isna(v):
        return None
    if isinstance(v, (datetime.datetime, pd.Timestamp)):
        return v.strftime('%Y-%m-%d %H:%M:%S') if v.time() != datetime.time(0, 0, 0) else v.strftime('%Y-%m-%d')
    if isinstance(v, datetime.date):
        return str(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return float(v)
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    return str(v) if not isinstance(v, (str, int, float)) else v


# 更新数据
def update_db_from_df(data, table_name, where):
    if data is None or data.empty:
        return
    cols = list(data.columns)
    update_cols = [c for c in cols if c not in where]
    where_cols = [c for c in cols if c in where]
    if not update_cols or not where_cols:
        return

    set_clause = ', '.join([f'`{c}` = %s' for c in update_cols])
    where_clause = ' AND '.join([f'`{c}` = %s' for c in where_cols])
    sql = f'UPDATE `{table_name}` SET {set_clause} WHERE {where_clause}'

    clean_df = data[update_cols + where_cols].copy()

    params_list = [
        tuple(_sanitize_db_value(v) for v in row)
        for row in clean_df.itertuples(index=False, name=None)
    ]

    conn = get_connection()
    if conn is None:
        logging.error(f"database.update_db_from_df无法连接数据库：{table_name}表")
        return
    try:
        conn.autocommit(False)
        with conn.cursor() as db:
            db.executemany(sql, params_list)
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logging.error(f"database.update_db_from_df批量处理异常：{table_name}表 {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass


# 检查表是否存在
def checkTableIsExist(tableName):
    with get_connection() as conn:
        with conn.cursor() as db:
            db.execute("""
                SELECT COUNT(*)
                FROM information_schema.tables
                WHERE table_name = '{0}'
                """.format(tableName.replace('\'', '\'\'')))
            if db.fetchone()[0] == 1:
                return True
    return False


# 增删改数据
def executeSql(sql, params=()):
    with get_connection() as conn:
        with conn.cursor() as db:
            try:
                db.execute(sql, params)
            except Exception as e:
                logging.error(f"database.executeSql处理异常：{sql}{e}")


# 查询数据
def executeSqlFetch(sql, params=()):
    with get_connection() as conn:
        with conn.cursor() as db:
            try:
                db.execute(sql, params)
                return db.fetchall()
            except Exception as e:
                logging.error(f"database.executeSqlFetch处理异常：{sql}{e}")
            return None


# 计算数量
def executeSqlCount(sql, params=()):
    with get_connection() as conn:
        with conn.cursor() as db:
            try:
                db.execute(sql, params)
                result = db.fetchall()
                if len(result) == 1:
                    return int(result[0][0])
                else:
                    return 0
            except Exception as e:
                logging.error(f"database.select_count计算数量处理异常：{e}")
            return 0
