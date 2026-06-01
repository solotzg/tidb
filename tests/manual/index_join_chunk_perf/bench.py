#!/usr/bin/env python3
#
# Manual perf harness for index lookup join chunk initial capacity.
#
# This script is intentionally kept out of normal CI. It targets a real TiDB
# server so that heap/RSS metrics can be compared between two tidb-server
# binaries.

import argparse
import json
import math
import random
import statistics
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta

try:
    import pymysql
except ImportError:
    print("missing dependency: pip3 install pymysql", file=sys.stderr)
    raise


SITES = ("5630", "5633")
FACT_TABLES = (
    "dh_yuebao_info",
    "dh_active_promote_details",
    "dh_active_details",
)
ACCOUNT_BASIC_COLUMNS = (
    "id",
    "site_code",
    "register_time",
    "last_update_timestamp",
    "username",
    "useridx",
    "account_type",
    "register_device_id",
    "register_ip",
    "register_ip_addr",
    "register_area",
    "user_type",
    "password",
    "nickname",
    "portrait_id",
    "portrait2_id",
    "mobile_phone",
    "wechat_id",
    "operation_id",
    "vip_level",
    "rmb_gold",
    "game_gold",
    "user_status",
    "user_status_reason",
    "member_level",
    "vipgift_recvd_status",
    "level_locked",
    "profit_validbet_rate",
    "remark",
    "gender",
    "user_question",
    "secret_security",
    "mobile_phone_time",
    "jpush_id",
    "integral",
    "prestige",
    "activity",
    "rewardbox",
    "regpkgid",
    "ad_platform",
    "regtype",
    "third_user_id",
    "status_remark",
    "verify_time",
    "birthday_secret",
    "phone_secret",
    "wechat_secret",
    "phone_hash",
    "wechat_hash",
    "realname_secret",
    "real_name",
    "area_code",
    "change_password",
    "unlock_time",
    "verify_type",
    "username_concat",
    "mtpush_id",
    "password_hash",
    "jg_web_id",
    "parent_id",
    "_V$_idx_dh_account_basic_reverse_mobile_phone_0",
    "restrict_flag",
    "verify_flag",
    "_V$_idx_dh_account_basic_lower_name_0",
    "club_id",
    "loan_status",
    "special_status",
    "create_time",
    "register_currency",
    "language",
    "delete_time",
    "delete_operator",
)
YUEBAO_INFO_COLUMNS = (
    "id",
    "site_code",
    "currency",
    "data_hash",
    "useridx",
    "balance",
    "principal",
    "interest",
    "total_interest",
    "last_remain_interest",
    "valid_principal",
    "add_principal",
    "add_principal_time",
    "last_interest_time",
    "create_time",
    "update_time",
    "account_type",
    "username",
    "version_no",
    "operator",
    "operate_time",
)
ACTIVE_PROMOTE_DETAILS_COLUMNS = (
    "id",
    "site_code",
    "activeid",
    "parentId",
    "parentName",
    "parentCurrency",
    "useridx",
    "username",
    "userCurrency",
    "is_pass",
    "register_time",
    "remark",
    "updatetime",
    "account_type",
)
ACTIVE_DETAILS_COLUMNS = (
    "id",
    "site_code",
    "createtime",
    "useridx",
    "user_status",
    "member_level",
    "vip_level",
    "orderno",
    "opt_type",
    "deal_type",
    "amount",
    "activeid",
    "awarded",
    "create_type",
    "currency",
    "ruleid",
    "activity",
    "username",
    "bonus_enum",
    "bonus_big_enum",
)
METRIC_NAMES = (
    "process_cpu_seconds_total",
    "process_resident_memory_bytes",
    "go_memstats_heap_inuse_bytes",
    "go_memstats_heap_alloc_bytes",
    "go_memstats_heap_objects",
)


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4000)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default="")
    parser.add_argument("--database", default="test")
    parser.add_argument("--status", default="http://127.0.0.1:10080")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--reset-only", action="store_true")
    parser.add_argument("--rows", type=int, default=50000, help="rows per site per fact table")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=100)
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument("--warmup", type=float, default=30.0)
    parser.add_argument("--in-list", type=int, default=256)
    parser.add_argument("--hit-ratio", type=float, default=0.45)
    parser.add_argument("--hit-pattern", choices=("contiguous", "spread"), default="contiguous")
    parser.add_argument("--split-table-regions", type=int, default=0)
    parser.add_argument("--split-index-regions", type=int, default=0)
    parser.add_argument("--target-qps", type=float, default=0.0)
    parser.add_argument("--mode", choices=("select", "update"), default="select")
    parser.add_argument("--username-state", choices=("stale", "matched"), default="stale")
    parser.add_argument("--tables", default=",".join(FACT_TABLES))
    parser.add_argument("--out", default="result.json")
    parser.add_argument("--heap-prefix", default="")
    return parser.parse_args()


def connect(args, database=None):
    return pymysql.connect(
        host=args.host,
        port=args.port,
        user=args.user,
        password=args.password,
        database=database if database is not None else args.database,
        autocommit=True,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.Cursor,
    )


def exec_sql(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())


def quote_ident(name):
    return "`" + name.replace("`", "``") + "`"


def query_all(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def set_tidb_vars(conn):
    statements = (
        "set @@tidb_init_chunk_size = 32",
        "set @@tidb_max_chunk_size = 1024",
        "set @@tidb_index_lookup_join_concurrency = 5",
        "set @@tidb_index_join_batch_size = 25000",
    )
    for sql in statements:
        exec_sql(conn, sql)


def active_tables(args):
    tables = [table.strip() for table in args.tables.split(",") if table.strip()]
    unknown = [table for table in tables if table not in FACT_TABLES]
    if unknown:
        raise ValueError(f"unknown tables: {', '.join(unknown)}")
    if not tables:
        raise ValueError("--tables must not be empty")
    return tuple(tables)


def fact_username(args, site, useridx):
    if args.username_state == "matched":
        return f"account_{site}_{useridx}"
    return f"stale_{site}_{useridx}"


def reset_username_expr(username_state):
    if username_state == "matched":
        return "concat('account_', site_code, '_', useridx)"
    return "concat('stale_', site_code, '_', useridx)"


def create_yuebao_info_table(conn):
    exec_sql(
        conn,
        """
        create table dh_yuebao_info (
          id bigint primary key,
          site_code varchar(64) not null default '',
          currency varchar(30) not null default '',
          data_hash bigint not null default 0,
          useridx bigint not null default 0,
          balance decimal(20,6) not null default 0,
          principal decimal(20,6) not null default 0,
          interest decimal(20,6) not null default 0,
          total_interest decimal(20,6) not null default 0,
          last_remain_interest decimal(20,6) not null default 0,
          valid_principal decimal(20,6) not null default 0,
          add_principal decimal(20,6) not null default 0,
          add_principal_time datetime not null,
          last_interest_time datetime not null,
          create_time datetime not null,
          update_time datetime not null,
          account_type bigint not null default 0,
          username varchar(64) not null default '',
          version_no bigint not null default 0,
          operator varchar(64) not null default '',
          operate_time datetime not null,
          key udx_yuebao_info_useridx_sitecod(useridx, site_code),
          key idx_lastInterestTime_sitecode(last_interest_time, site_code),
          key idx_pri_lit_sitecode(principal, last_interest_time, site_code),
          key idx_currency_principal_site(currency, principal, site_code),
          key idx_uidx_scode_prin_currency(useridx, site_code, principal, currency)
        )
        """,
    )


def create_active_promote_details_table(conn):
    exec_sql(
        conn,
        """
        create table dh_active_promote_details (
          id bigint primary key,
          site_code varchar(64) not null default '',
          activeid int not null default 0,
          parentId bigint not null default 0,
          parentName varchar(60) not null default '',
          parentCurrency varchar(60) not null default '',
          useridx bigint not null default 0,
          username varchar(60) not null default '',
          userCurrency varchar(60) not null default '',
          is_pass tinyint not null default 0,
          register_time int not null default 0,
          remark varchar(8000) not null default '',
          updatetime int not null default 0,
          account_type int not null default 2,
          key idx_dh_active_promote_details_updatetime(activeid, parentId, is_pass, updatetime),
          unique key udx_useridx_sitecode(activeid, parentId, useridx, site_code),
          key idx_activeid_sitecode(activeid, site_code),
          key idx_dh_active_promote_details_registerTime_sitecode(activeid, parentId, site_code, register_time),
          key idx_dh_active_promote_details_username_sitecode(activeid, parentId, site_code, username),
          key idx_dh_active_promote_details_is_pass_sitecode(activeid, is_pass, site_code),
          key idx_dh_active_promote_details_updatetime_sitecode(activeid, parentId, site_code, updatetime),
          key idx_dh_active_promote_details_updatetime2_sitecode(activeid, updatetime, site_code),
          key idx_useridx_siteCode(useridx, site_code),
          key idx_parentId(parentId, site_code)
        )
        """,
    )


def create_active_details_table(conn):
    exec_sql(
        conn,
        """
        create table dh_active_details (
          id bigint primary key,
          site_code varchar(64) not null default '',
          createtime int not null default 0,
          useridx bigint not null default 0,
          user_status int not null default 0,
          member_level int not null default 0,
          vip_level int not null default -1,
          orderno varchar(100) not null default '0',
          opt_type int not null default 0,
          deal_type int not null default 0,
          amount decimal(18,6) not null default 0,
          activeid int not null default 0,
          awarded int not null default 0,
          create_type int not null default 0,
          currency varchar(60) not null default ' ',
          ruleid varchar(255) not null default '0',
          activity int not null default 0,
          username varchar(60) not null default '',
          bonus_enum int not null default 0,
          bonus_big_enum int not null default 0,
          key idx_active_details_amount(amount),
          key idx_dh_active_details_useridx_opt_type_deal_type_createtime(useridx, opt_type, deal_type, createtime),
          key idx_dh_active_details_currency(currency),
          key idx_dh_active_details_opt_type_currency_createtime(opt_type, currency, createtime),
          key idx_dh_active_details_currency_createtime(currency, createtime),
          key idx_dh_active_details_deal_type_currency_createtime(deal_type, currency, createtime),
          key idx_active_details_orderno_sitecode(orderno, site_code),
          key idx_active_details_useridx_sitecode(useridx, createtime, site_code),
          key idx_active_details_createtime_sitecode(createtime, site_code),
          key idx_dh_active_details_opt_type_activeid_createtime_sitecode(opt_type, activeid, createtime, site_code),
          key idx_dh_active_details_opt_type_createtime_sitecode(opt_type, createtime, site_code),
          key idx_dh_active_details_opttype_dealtype_createtime_sitecode(opt_type, deal_type, createtime, site_code),
          key idx_dh_active_details_dealtype_createtime_sitecode(deal_type, createtime, site_code),
          key idx_dh_active_details_activeid_createtime_sitecode(activeid, createtime, site_code),
          key idx_dh_active_details_currency_sitecode(currency, site_code),
          key idx_active_details_activeid_useridx_sitecode(activeid, useridx, createtime, site_code),
          key idx_active_details_username_sitecode(username, createtime, site_code)
        )
        """,
    )


def create_account_basic_table(conn):
    exec_sql(
        conn,
        """
        create table dh_account_basic (
          id bigint not null,
          site_code varchar(64) not null default '',
          register_time int not null default 0,
          last_update_timestamp timestamp not null default current_timestamp on update current_timestamp,
          username varchar(60) not null default '',
          useridx bigint not null default 0,
          account_type tinyint not null default 0,
          register_device_id varchar(64) not null default '',
          register_ip int not null default 0,
          register_ip_addr varchar(45) not null default '',
          register_area varchar(255) not null default '',
          user_type int not null default 0,
          password varchar(300) not null default '',
          nickname varchar(20) not null default '',
          portrait_id int not null default 0,
          portrait2_id bigint not null default 0,
          mobile_phone varchar(60) not null default '',
          wechat_id varchar(100) not null default '',
          operation_id int not null default 0,
          vip_level int not null default 0,
          rmb_gold decimal(18,6) not null default 0,
          game_gold decimal(20,6) not null default 0,
          user_status int not null default 0,
          user_status_reason int not null default 0,
          member_level int not null default 0,
          vipgift_recvd_status varchar(8192) not null default '',
          level_locked int not null default 0,
          profit_validbet_rate decimal(18,6) not null default 0,
          remark varchar(600) not null default '',
          gender int not null default 0,
          user_question int not null default 0,
          secret_security varchar(255) not null default '',
          mobile_phone_time int not null default 0,
          jpush_id varchar(128) not null default '',
          integral int not null default 0,
          prestige int not null default 0,
          activity int not null default 0,
          rewardbox mediumblob,
          regpkgid int not null default 0,
          ad_platform varchar(128) not null default '',
          regtype int not null default 0,
          third_user_id varchar(256) not null default '',
          status_remark varchar(600) not null default '',
          verify_time int not null default 0,
          birthday_secret varchar(128) not null default '',
          phone_secret varchar(200) not null default '',
          wechat_secret varchar(200) not null default '',
          phone_hash varchar(32) not null default '',
          wechat_hash varchar(32) not null default '',
          realname_secret varchar(500) not null default '',
          real_name varchar(100) not null default '',
          area_code varchar(15) not null default '',
          change_password int not null default 0,
          unlock_time int not null default 0,
          verify_type int not null default 0,
          username_concat varchar(128) not null default '',
          mtpush_id varchar(128) not null default '',
          password_hash varchar(32) not null default '',
          jg_web_id varchar(128) not null default '',
          parent_id bigint not null default 0,
          `_V$_idx_dh_account_basic_reverse_mobile_phone_0` varchar(60) not null default '',
          restrict_flag int not null default 0,
          verify_flag int not null default 0,
          `_V$_idx_dh_account_basic_lower_name_0` varchar(100) not null default '',
          club_id bigint not null default 0,
          loan_status int not null default 0,
          special_status int not null default 0,
          create_time int not null default 0,
          register_currency varchar(30) not null default '',
          language varchar(64) not null default '',
          delete_time int not null default 0,
          delete_operator varchar(50) not null default '',
          primary key (id),
          key idx_account_basic_registerip(register_ip),
          key idx_account_regpkgid(regpkgid),
          key idx_account_basic_userstatus(user_status),
          key idx_account_basic_gender(gender),
          key idx_account_basic_viplevel(vip_level),
          key idx_account_basic_memberlevel(member_level),
          key idx_account_basic_portraitid(portrait_id),
          key idx_account_basic_operationid(operation_id),
          key idx_account_basic_game_gold(game_gold),
          key idx_account_basic_registertime(register_time),
          key idx_account_basic_wechathash(wechat_hash),
          key idx_account_basic_phonehash(phone_hash),
          key idx_account_basic_useridx_accounttype(useridx, account_type),
          key idx_account_basic_register_ip_addr(register_ip_addr),
          key idx_account_basic_registertime_username_1(username, register_time),
          key idx_dh_account_basic_registerdeviceid(register_device_id),
          key idx_account_basic_username_concat(username_concat),
          key idx_account_type_register_time_useridx_username(account_type, register_time, useridx, username),
          key idx_account_basic_memberlevel_accounttype(member_level, account_type),
          key idx_account_basic_registertime_username(register_time, username),
          key idx_dh_account_basic_useridx_username(useridx, username),
          key idx_account_basic_registertime_username_accounttype_useridx(register_time, username, account_type, useridx),
          key idx_thirdUserId(third_user_id),
          key idx_dh_account_type_register_device_id_register_time(account_type, register_device_id, register_time, id),
          key idx_dh_account_type_register_ip_register_time(account_type, register_ip_addr, register_time, id),
          key idx_verfype_accoun_type(verify_type, account_type),
          key idx_status_time(user_status, unlock_time),
          key idx_real_namerealname(real_name, realname_secret),
          key idx_dh_account_basic_reverse_mobile_phone(`_V$_idx_dh_account_basic_reverse_mobile_phone_0`),
          key idx_dh_account_basic_verify_flag(verify_flag),
          key idx_dh_account_basic_lower_name(`_V$_idx_dh_account_basic_lower_name_0`),
          unique key udx_account_basic_useridx_sitecode(useridx, site_code),
          unique key udx_account_basic_username_sitecode(username, site_code),
          key idx_parentId_sitecode(parent_id, site_code),
          key idx_account_basic_register_time_id(register_time, id),
          key idx_register_currency_site_code(register_currency, site_code),
          key idx_rcurrency_scode_rtime_username_atype(register_currency, site_code, register_time, username, account_type),
          key idx_dh_account_basic_status_delete_username_sitecode(user_status, account_type, delete_time, username, site_code),
          key idx_dh_account_basic_site_code_id(site_code, id),
          key idx_account_basic_sc_rc_id_mt_jg_uid(site_code, register_currency, id, mtpush_id, jg_web_id, useridx),
          key idx_lut_uid_sc_bs(last_update_timestamp, useridx, site_code, birthday_secret),
          key idx_sitecode_accttype_useridx(site_code, account_type, useridx),
          key idx_uidx_sc_uname_rgid(useridx, site_code, username, regpkgid),
          key idx_sc_rtime_idx(site_code, register_time, useridx),
          key idx_uidx_scode_recurrency_cid(useridx, site_code, register_currency, club_id)
        )
        """,
    )


def prepare_schema(conn, tables):
    for table in ("dh_account_basic",) + tables:
        exec_sql(conn, f"drop table if exists {table}")

    create_account_basic_table(conn)
    if "dh_yuebao_info" in tables:
        create_yuebao_info_table(conn)
    if "dh_active_promote_details" in tables:
        create_active_promote_details_table(conn)
    if "dh_active_details" in tables:
        create_active_details_table(conn)


def batched(values, size):
    for i in range(0, len(values), size):
        yield values[i : i + size]


def insert_many(conn, sql, rows, batch_size):
    with conn.cursor() as cur:
        for batch in batched(rows, batch_size):
            cur.executemany(sql, batch)


def load_account_basic(conn, args):
    placeholders = ", ".join(["%s"] * len(ACCOUNT_BASIC_COLUMNS))
    col_names = ", ".join(quote_ident(name) for name in ACCOUNT_BASIC_COLUMNS)
    sql = f"insert into dh_account_basic ({col_names}) values ({placeholders})"
    rows = []
    for site_idx, site in enumerate(SITES):
        base_id = site_idx * args.rows
        for useridx in range(1, args.rows + 1):
            username = f"account_{site}_{useridx}"
            mobile = f"13{useridx:09d}"[-11:]
            real_name = f"real_{useridx}"
            rows.append(
                (
                    base_id + useridx,
                    site,
                    1716652800 + useridx,
                    "2026-05-26 00:00:00",
                    username,
                    useridx,
                    0,
                    f"device_{useridx % 10000}",
                    0,
                    "127.0.0.1",
                    "area",
                    0,
                    "pwd",
                    f"nick_{useridx % 10000}",
                    0,
                    0,
                    mobile,
                    f"wechat_{useridx}",
                    0,
                    useridx % 8,
                    "0.000000",
                    "0.000000",
                    0,
                    0,
                    useridx % 10,
                    "",
                    0,
                    "0.000000",
                    "",
                    0,
                    0,
                    "",
                    0,
                    f"jpush_{useridx}",
                    useridx % 100000,
                    useridx % 100000,
                    useridx % 100000,
                    b"{}",
                    useridx % 128,
                    "bench",
                    0,
                    f"third_{useridx}",
                    "",
                    0,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    real_name,
                    "86",
                    0,
                    0,
                    0,
                    username,
                    f"mt_{useridx}",
                    "",
                    f"jg_{useridx}",
                    useridx % 10000,
                    mobile[::-1],
                    0,
                    0,
                    real_name.lower(),
                    useridx % 32,
                    0,
                    0,
                    1716652800 + useridx,
                    "CNY",
                    "zh-CN",
                    0,
                    "",
                )
            )
            if len(rows) >= args.batch_size:
                insert_many(conn, sql, rows, args.batch_size)
                rows = []
    if rows:
        insert_many(conn, sql, rows, args.batch_size)


def load_yuebao_info(conn, args):
    base_time = datetime(2026, 5, 26, 0, 0, 0)
    placeholders = ", ".join(["%s"] * len(YUEBAO_INFO_COLUMNS))
    col_names = ", ".join(YUEBAO_INFO_COLUMNS)
    sql = f"insert into dh_yuebao_info ({col_names}) values ({placeholders})"
    rows = []

    for site_idx, site in enumerate(SITES):
        base_id = site_idx * args.rows
        for useridx in range(1, args.rows + 1):
            row_time = base_time + timedelta(seconds=useridx)
            principal = f"{1000 + useridx % 10000}.000000"
            interest = f"{useridx % 100}.000000"
            row = (
                base_id + useridx,
                site,
                "CNY",
                useridx * 131 + site_idx,
                useridx,
                principal,
                principal,
                interest,
                f"{useridx % 1000}.000000",
                "0.000000",
                principal,
                "0.000000",
                row_time.strftime("%Y-%m-%d %H:%M:%S"),
                row_time.strftime("%Y-%m-%d %H:%M:%S"),
                row_time.strftime("%Y-%m-%d %H:%M:%S"),
                row_time.strftime("%Y-%m-%d %H:%M:%S"),
                0,
                fact_username(args, site, useridx),
                1,
                "bench",
                row_time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            rows.append(row)
            if len(rows) >= args.batch_size:
                insert_many(conn, sql, rows, args.batch_size)
                rows = []
    if rows:
        insert_many(conn, sql, rows, args.batch_size)


def load_active_promote_details(conn, args):
    placeholders = ", ".join(["%s"] * len(ACTIVE_PROMOTE_DETAILS_COLUMNS))
    col_names = ", ".join(ACTIVE_PROMOTE_DETAILS_COLUMNS)
    sql = f"insert into dh_active_promote_details ({col_names}) values ({placeholders})"
    rows = []

    for site_idx, site in enumerate(SITES):
        base_id = site_idx * args.rows
        for useridx in range(1, args.rows + 1):
            activeid = 1000 + useridx % 16
            parent_id = useridx % 10000
            row = (
                base_id + useridx,
                site,
                activeid,
                parent_id,
                f"parent_{site}_{parent_id}",
                "CNY",
                useridx,
                fact_username(args, site, useridx),
                "CNY",
                useridx % 2,
                1716652800 + useridx,
                f'{{"useridx":{useridx},"site_code":"{site}"}}',
                1716652800 + useridx,
                2,
            )
            rows.append(row)
            if len(rows) >= args.batch_size:
                insert_many(conn, sql, rows, args.batch_size)
                rows = []
    if rows:
        insert_many(conn, sql, rows, args.batch_size)


def load_active_details(conn, args):
    placeholders = ", ".join(["%s"] * len(ACTIVE_DETAILS_COLUMNS))
    col_names = ", ".join(ACTIVE_DETAILS_COLUMNS)
    sql = f"insert into dh_active_details ({col_names}) values ({placeholders})"
    rows = []

    for site_idx, site in enumerate(SITES):
        base_id = site_idx * args.rows
        for useridx in range(1, args.rows + 1):
            createtime = 1716652800 + useridx
            activeid = 1000 + useridx % 16
            row = (
                base_id + useridx,
                site,
                createtime,
                useridx,
                0,
                useridx % 10,
                useridx % 8,
                f"order_{site}_{useridx}",
                useridx % 5,
                useridx % 7,
                f"{useridx % 10000}.000000",
                activeid,
                useridx % 2,
                1,
                "CNY",
                str(useridx % 128),
                useridx % 100,
                fact_username(args, site, useridx),
                useridx % 32,
                useridx % 8,
            )
            rows.append(row)
            if len(rows) >= args.batch_size:
                insert_many(conn, sql, rows, args.batch_size)
                rows = []
    if rows:
        insert_many(conn, sql, rows, args.batch_size)


def load_data(conn, args, tables):
    started = time.time()
    print(f"loading dh_account_basic, rows_per_site={args.rows}")
    load_account_basic(conn, args)
    if "dh_yuebao_info" in tables:
        print("loading dh_yuebao_info")
        load_yuebao_info(conn, args)
    if "dh_active_promote_details" in tables:
        print("loading dh_active_promote_details")
        load_active_promote_details(conn, args)
    if "dh_active_details" in tables:
        print("loading dh_active_details")
        load_active_details(conn, args)

    for table in ("dh_account_basic",) + tables:
        print(f"analyze table {table}")
        exec_sql(conn, f"analyze table {table}")
    print(f"data prepared in {time.time() - started:.1f}s")


def split_table_regions(conn, table, regions, upper_id):
    exec_sql(conn, f"split table {table} between (0) and ({upper_id}) regions {regions}")


def split_index_regions(conn, table, index, lower, upper, regions):
    exec_sql(
        conn,
        f"split table {table} index {index} between {lower} and {upper} regions {regions}",
    )


def split_regions(conn, args, tables):
    table_regions = args.split_table_regions
    index_regions = args.split_index_regions
    if table_regions <= 0 and index_regions <= 0:
        return
    started = time.time()
    upper_id = args.rows * len(SITES) + 1
    two_col_lower = "(0, '')"
    two_col_upper = f"({args.rows + 1}, 'zzzz')"
    index_splits = {
        "dh_account_basic": (("udx_account_basic_useridx_sitecode", two_col_lower, two_col_upper),),
        "dh_yuebao_info": (("udx_yuebao_info_useridx_sitecod", two_col_lower, two_col_upper),),
        "dh_active_promote_details": (("idx_useridx_siteCode", two_col_lower, two_col_upper),),
        "dh_active_details": (
            ("idx_active_details_useridx_sitecode", "(0, 0, '')", f"({args.rows + 1}, 2147483647, 'zzzz')"),
        ),
    }
    for table in ("dh_account_basic",) + tables:
        if table_regions > 0:
            print(f"split table {table}, regions={table_regions}")
            split_table_regions(conn, table, table_regions, upper_id)
        if index_regions > 0:
            for index, lower, upper in index_splits.get(table, ()):
                print(f"split index {table}.{index}, regions={index_regions}")
                split_index_regions(conn, table, index, lower, upper, index_regions)
    print(f"regions split in {time.time() - started:.1f}s")


def reset_all_data(conn, tables, username_state):
    started = time.time()
    username_expr = reset_username_expr(username_state)
    for table in tables:
        print(f"reset {table}")
        exec_sql(conn, f"update {table} set username = {username_expr}")
    print(f"data reset in {time.time() - started:.1f}s")


def count_rows(conn, tables):
    result = {}
    for table in ("dh_account_basic",) + tables:
        result[table] = int(query_all(conn, f"select count(*) from {table}")[0][0])
    return result


def select_sql(table, in_list):
    if table == "dh_yuebao_info":
        b_select = ", ".join(f"b.{name}" for name in YUEBAO_INFO_COLUMNS)
    elif table == "dh_active_promote_details":
        b_select = ", ".join(f"b.{name}" for name in ACTIVE_PROMOTE_DETAILS_COLUMNS)
    elif table == "dh_active_details":
        b_select = ", ".join(f"b.{name}" for name in ACTIVE_DETAILS_COLUMNS)
    else:
        raise ValueError(f"unknown fact table: {table}")
    placeholders = ", ".join(["%s"] * in_list)
    return f"""
        select /*+ INL_HASH_JOIN(a) */
          {b_select}, a.username
        from {table} b join dh_account_basic a
          on b.useridx = a.useridx and b.site_code = a.site_code
        where b.site_code = %s
          and b.useridx in ({placeholders})
          and b.username != a.username
    """


def update_sql(table, in_list):
    placeholders = ", ".join(["%s"] * in_list)
    return f"""
        update /*+ INL_HASH_JOIN(a) */
          {table} as b join dh_account_basic as a
            on b.useridx = a.useridx and b.site_code = a.site_code
        set b.username = a.username
        where a.useridx in ({placeholders})
          and a.site_code = %s
          and b.username != a.username
        limit 2000
    """


def reset_update_sql(table, in_list):
    placeholders = ", ".join(["%s"] * in_list)
    return f"""
        update {table}
        set username = concat('stale_', site_code, '_', useridx)
        where useridx in ({placeholders}) and site_code = %s
    """


def expected_columns(table):
    if table == "dh_account_basic":
        return ACCOUNT_BASIC_COLUMNS
    if table == "dh_yuebao_info":
        return YUEBAO_INFO_COLUMNS
    if table == "dh_active_promote_details":
        return ACTIVE_PROMOTE_DETAILS_COLUMNS
    if table == "dh_active_details":
        return ACTIVE_DETAILS_COLUMNS
    raise ValueError(f"unknown table: {table}")


def validate_schema(conn, tables):
    for table in ("dh_account_basic",) + tables:
        rows = query_all(conn, f"show columns from {table}")
        existing = {row[0] for row in rows}
        missing = [col for col in expected_columns(table) if col not in existing]
        if missing:
            raise RuntimeError(
                f"{table} schema is not prepared for this harness; missing columns: "
                f"{', '.join(missing[:8])}. Run with PREPARE=1 to rebuild the test schema."
            )


def explain_plans(conn, args, tables):
    plans = {}
    for table in tables:
        in_list = min(args.in_list, 8)
        if args.mode == "select":
            sql = "explain format='brief' " + select_sql(table, in_list)
            params = ["5630"] + list(range(1, in_list + 1))
        else:
            sql = "explain format='brief' " + update_sql(table, in_list)
            params = list(range(1, in_list + 1)) + ["5630"]
        rows = query_all(conn, sql, params)
        text = "\n".join("\t".join(str(col) for col in row) for row in rows)
        plans[table] = text
        if "IndexHashJoin" not in text or "IndexLookUp" not in text:
            raise RuntimeError(f"{table} plan does not contain IndexHashJoin + IndexLookUp:\n{text}")
    return plans


def fetch_metrics(status):
    url = status.rstrip("/") + "/metrics"
    with urllib.request.urlopen(url, timeout=5) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    values = {}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        name = parts[0].split("{", 1)[0]
        if name in METRIC_NAMES:
            try:
                values[name] = max(values.get(name, 0.0), float(parts[1]))
            except ValueError:
                pass
    return values


class MetricsSampler:
    def __init__(self, status):
        self.status = status
        self.samples = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=10)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                sample = fetch_metrics(self.status)
                sample["ts"] = time.time()
                self.samples.append(sample)
            except Exception as exc:
                self.samples.append({"ts": time.time(), "error": str(exc)})
            self.stop_event.wait(1.0)

    def summary(self):
        result = {"sample_count": len(self.samples)}
        for name in METRIC_NAMES:
            vals = [s[name] for s in self.samples if name in s]
            if vals:
                result[name] = {
                    "min": min(vals),
                    "max": max(vals),
                    "last": vals[-1],
                }
        return result


def percentile(values, pct):
    if not values:
        return 0.0
    values = sorted(values)
    idx = int(math.ceil((pct / 100.0) * len(values))) - 1
    return values[max(0, min(idx, len(values) - 1))]


def build_useridxs(args, rng):
    hit_count = max(1, min(args.in_list, round(args.in_list * args.hit_ratio)))
    miss_count = args.in_list - hit_count
    if args.hit_pattern == "spread":
        hit_step = max(1, args.rows // hit_count)
        hit_offset = rng.randint(0, max(0, hit_step - 1))
        useridxs = [1 + ((hit_offset + i * hit_step) % args.rows) for i in range(hit_count)]
        miss_step = max(1, args.rows // max(1, miss_count))
        miss_offset = rng.randint(0, max(0, miss_step - 1))
        useridxs.extend(args.rows + 1 + ((miss_offset + i * miss_step) % args.rows) for i in range(miss_count))
    else:
        hit_start = rng.randint(1, max(1, args.rows - hit_count + 1))
        miss_start = args.rows + rng.randint(1, max(1, args.rows))
        useridxs = list(range(hit_start, hit_start + hit_count))
        useridxs.extend(range(miss_start, miss_start + miss_count))
    rng.shuffle(useridxs)
    return useridxs


class RateLimiter:
    def __init__(self, target_qps):
        self.interval = 1.0 / target_qps if target_qps > 0 else 0.0
        self.next_at = time.perf_counter()
        self.lock = threading.Lock()

    def wait(self, stop_at):
        if self.interval <= 0:
            return True
        with self.lock:
            now = time.perf_counter()
            wait_sec = max(0.0, self.next_at - now)
            self.next_at = max(self.next_at, now) + self.interval
        if wait_sec > 0:
            if time.time() + wait_sec >= stop_at:
                return False
            time.sleep(wait_sec)
        return time.time() < stop_at


def worker(args, tables, limiter, worker_id, stop_at, stats, stats_lock, record):
    conn = connect(args)
    set_tidb_vars(conn)
    rng = random.Random(worker_id + int(time.time()))
    latencies = []
    attempt_count = 0
    exec_count = 0
    error_count = 0
    error_samples = []

    try:
        while time.time() < stop_at:
            if not limiter.wait(stop_at):
                break
            table = tables[attempt_count % len(tables)]
            site = SITES[(worker_id + attempt_count) % len(SITES)]
            attempt_count += 1
            useridxs = build_useridxs(args, rng)
            begin = time.perf_counter()
            try:
                if args.mode == "select":
                    sql = select_sql(table, args.in_list)
                    params = [site] + useridxs
                    rows = query_all(conn, sql, params)
                    if not rows:
                        raise RuntimeError("empty result")
                else:
                    params = useridxs + [site]
                    if args.username_state == "stale":
                        exec_sql(conn, reset_update_sql(table, args.in_list), params)
                    exec_sql(conn, update_sql(table, args.in_list), params)
                exec_count += 1
                if record:
                    latencies.append((time.perf_counter() - begin) * 1000.0)
            except Exception as exc:
                error_count += 1
                if len(error_samples) < 5:
                    error_samples.append(f"{table}: {exc}")
    finally:
        conn.close()

    with stats_lock:
        stats["exec_count"] += exec_count if record else 0
        stats["error_count"] += error_count if record else 0
        stats["latencies_ms"].extend(latencies)
        stats["error_samples"].extend(error_samples)


def run_workload(args, tables, duration, record=True):
    stats = {"exec_count": 0, "error_count": 0, "latencies_ms": [], "error_samples": []}
    stats_lock = threading.Lock()
    limiter = RateLimiter(args.target_qps)
    stop_at = time.time() + duration
    threads = [
        threading.Thread(target=worker, args=(args, tables, limiter, i, stop_at, stats, stats_lock, record))
        for i in range(args.concurrency)
    ]
    started = time.time()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = max(0.001, time.time() - started)
    latencies = stats["latencies_ms"]
    return {
        "elapsed_sec": elapsed,
        "exec_count": stats["exec_count"],
        "error_count": stats["error_count"],
        "error_samples": stats["error_samples"][:20],
        "qps": stats["exec_count"] / elapsed,
        "latency_ms": {
            "avg": statistics.mean(latencies) if latencies else 0.0,
            "p50": percentile(latencies, 50),
            "p95": percentile(latencies, 95),
            "p99": percentile(latencies, 99),
            "max": max(latencies) if latencies else 0.0,
        },
    }


def download_heap(status, path):
    url = status.rstrip("/") + "/debug/pprof/heap?gc=1"
    urllib.request.urlretrieve(url, path)


def main():
    args = parse_args()
    tables = active_tables(args)
    if args.in_list < 1:
        raise ValueError("--in-list must be positive")
    if args.hit_ratio <= 0 or args.hit_ratio > 1:
        raise ValueError("--hit-ratio must be in (0, 1]")
    if args.split_table_regions < 0:
        raise ValueError("--split-table-regions must be non-negative")
    if args.split_index_regions < 0:
        raise ValueError("--split-index-regions must be non-negative")
    if args.target_qps < 0:
        raise ValueError("--target-qps must be non-negative")
    if args.mode == "select" and args.username_state == "matched" and not args.reset_only:
        raise ValueError("--username-state=matched is only valid with --mode=update or --reset-only")

    admin = connect(args, database=None)
    exec_sql(admin, f"create database if not exists `{args.database}`")
    admin.close()

    conn = connect(args)
    set_tidb_vars(conn)
    if args.prepare:
        prepare_schema(conn, tables)
        load_data(conn, args, tables)
        split_regions(conn, args, tables)

    if args.reset_only:
        validate_schema(conn, tables)
        reset_all_data(conn, tables, args.username_state)
        conn.close()
        return

    validate_schema(conn, tables)
    row_counts = count_rows(conn, tables)
    plans = explain_plans(conn, args, tables)
    conn.close()

    if args.heap_prefix:
        download_heap(args.status, f"{args.heap_prefix}-before.pb.gz")

    if args.warmup > 0:
        print(f"warmup {args.warmup}s")
        run_workload(args, tables, args.warmup, record=False)

    target = f", target_qps={args.target_qps}" if args.target_qps > 0 else ""
    print(f"measuring mode={args.mode}, concurrency={args.concurrency}{target}, duration={args.duration}s")
    sampler = MetricsSampler(args.status)
    sampler.start()
    workload = run_workload(args, tables, args.duration, record=True)
    sampler.stop()

    if args.heap_prefix:
        download_heap(args.status, f"{args.heap_prefix}-after.pb.gz")

    output = {
        "config": vars(args),
        "row_counts": row_counts,
        "plans": plans,
        "workload": workload,
        "metrics": sampler.summary(),
        "metric_samples": sampler.samples,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, sort_keys=True)
    print(json.dumps({"out": args.out, "workload": workload, "metrics": sampler.summary()}, indent=2))


if __name__ == "__main__":
    main()
