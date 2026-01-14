#!/usr/bin/env python3

import json
import time
import datetime
import argparse
import mysql.connector
from utils import *


prepare_table_name = 't'
GB = 1024**3
MB = 1024**2
prepare_table = False
show_mysql_result = False
tidb_server_memory_limit_name = 'tidb_server_memory_limit'


sql1 = "set @@tidb_mem_quota_query={}; explain analyze select * from t t1 join t t2 on t1.k1 = t2.k1 and t1.k2 = t2.k2 order by t1.v1, t2.v1;".format(
    int(4 * GB))
sql2 = "explain analyze (select * from t) union all (select * from t) order by k1,v1,k2;" * 2
sql3 = "explain analyze (select * from t) order by k1,v1,k2;"

tidb_server_memory_limit_gb = 20


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '--kill_all', help='kill all sessions', action='store_true')
    parser.add_argument(
        '--host', help='tidb host', default="0.0.0.0")
    parser.add_argument(
        '--limit', help='server limit(GB)', default=tidb_server_memory_limit_gb)
    parser.add_argument(
        '--tidb_status', choices=["init", "reserve", "hold", "gc", "cap", "align", "dump"], )
    parser.add_argument(
        '--val')
    parser.add_argument(
        '--cmp', action='store_true')
    parser.add_argument('-p', '--parel', help="", default=1)
    parser.add_argument('-l', '--loop', help="", default=1)
    parser.add_argument('--port', help="port", default=4066)
    parser.add_argument('--port2', help="", default=4078)
    parser.add_argument(
        '--sql2')
    parser.add_argument(
        '--sql2_prepare')
    parser.add_argument(
        '--sql3', help=sql3, action='store_true')
    parser.add_argument(
        '--cop_cache', help="use copr cache", default="0")
    parser.add_argument(
        '--restart', help="restart tidb", action='store_true')
    parser.add_argument(
        '--restart_kv', help="restart tikv", action='store_true')
    parser.add_argument('--tpcc')
    parser.add_argument('--tpch')
    parser.add_argument('--tpcc_run')
    parser.add_argument('--tpch_run')
    parser.add_argument('--mode')
    parser.add_argument('--max_memory', help='max memory', default="1.5G")
    parser.add_argument('--max_cpus', help='cpus', default=10)
    parser.add_argument('--tidb_bin', help='tidb-server binary')

    args = parser.parse_args()

    runner = Runner(args)
    return runner.run()


class Runner:
    def __init__(self, args, ):
        self.args = args
        args.tidb_port = int(args.port)
        args.tidb_status_port = 10000 + args.tidb_port % 100

        self.url_prefix = "http://{}:{}".format(
            self.args.host, self.args.tidb_status_port)
        self._mysql_conn = None
        self.label = []

    @property
    def docker_env(self):
        return "max_memory={} max_cpus={} use_cop_cache={}".format(
            self.args.max_memory, self.args.max_cpus, self.args.cop_cache)

    def handle_tpcc_run(self, mode, database, warehouses, threads, dur):
        info = "TPC-C, mode: `{}`, database: {}, warehouses: {}, threads: {}, duration: {}".format(
            mode, database, warehouses, threads, dur)
        self.label.append(info, )
        self.label.append(self.docker_env)
        cmd_run = "tiup bench tpcc --warehouses {warehouses} run --host {host} --port {port}  --db {db} --threads {threads} --time {dur} --ignore-error  ".format(
            warehouses=warehouses, host=self.args.host, port=self.args.tidb_port, db=database, threads=threads, dur=dur)
        logger.info("Running TPCC with command: {}".format(cmd_run))
        now = time.time()
        stdout, stderr, ret = run_cmd(cmd_run, cb=lambda line: logger.error(
            line) if line.find("failed") != -1 else None)
        logger.info("TPC-C command finished, time cost: {:.3f}s".format(
            time.time() - now))
        res = ""
        if ret:
            logger.error("Failed to run {}, error:\n{}".format(
                info, stderr[:1000]))
        else:
            res: str = stdout.split('\n')[-2]
            if not res.startswith("tpmC:"):
                logger.error(
                    "TPC-C run did not return expected result: {}".format(res))
            else:
                logger.info(
                    "{}\n\t{}".format(info, res))

        if self.get_tidb_server_memory_limit() is None:
            logger.critical("TiDB server is down")
            res = "TiDB OOM"

        data = {}
        fname = "{}/.vscode/tpcc.res.json.new".format(SCRIPT_DIR)
        with open(fname, "r") as f:
            p = f.read()
            if not p:
                p = "{}"
            data = json.loads(p)
        if mode not in data:
            data[mode] = {}
        label = self.label[:]
        label.sort()
        label = ';'.join(label)
        label = label.lower()
        if label not in data[mode]:
            data[mode][label] = []
        data[mode][label].append({
            "time": datetime.datetime.now().strftime('%Y/%m/%d %H:%M:%S'),
            'result': res,
        })
        with open(fname, "w") as f:
            f.write(json.dumps(data, indent=4, ensure_ascii=False))

    def handle_tpch_run(self, mode, database, warehouses, threads, count=22):
        cmd_run = "tiup bench tpch --sf {warehouses} run --host {host} --port {port}  --db {db} --threads {threads} --count {count}".format(
            warehouses=warehouses, host=self.args.host, port=self.args.tidb_port, db=database, threads=threads, count=count)
        logger.info("Running TPC-H with command: {}".format(cmd_run))
        self.execute("set global tidb_mem_quota_query={}".format(
            int(20 * GB)))
        stdout, stderr, ret = run_cmd(cmd_run, show_stdout=True)
        if ret:
            logger.error("Failed to run TPC-H: {}".format(stderr))
        r = {}
        prefix = "[Summary] Q"
        for d in stdout.split('\n'):
            if d.startswith(prefix):
                q, t = d[len(prefix):].split(": ")
                r[int(q)] = float(t[:-1])
        assert len(r) == count, "Expected {}, got {}".format(
            count, len(r))
        s = 0
        for k, v in sorted(r.items()):
            print(v)
            s += v
        logger.info(
            "Total time cost for {} queries: {:.3f}s".format(count, s))

        if self.get_tidb_server_memory_limit() is None:
            logger.critical("TiDB server is down")
            return

    def analyze_table_all(self):
        self.set_tidb_mem_arbitrator_mode("disable")
        self.execute("set global tidb_enable_auto_analyze=default")
        for c in self.execute("SHOW STATS_HEALTHY"):
            if c[-1] != 100:
                self.execute("ANALYZE TABLE {}.{}".format(c[0], c[1]))

    def handle_tpcc_prepare(self,  database, warehouses, threads):
        self.label.append("cleanup|prepare")
        self.set_tidb_mem_arbitrator_mode("disable")
        self.restart_tidb("max_memory={} max_cpus={} ".format(0, 0))
        cmd_cleanup_db = "drop database if exists {}".format(database)
        self.execute(cmd_cleanup_db)
        cmd_prepare_db = "create database if not exists {}".format(database)
        self.execute(cmd_prepare_db)
        cmd_prepare = "tiup bench tpcc --warehouses {warehouses} prepare --host {host} --port {port}  --db {db} --threads {threads}".format(
            warehouses=warehouses, host=self.args.host, port=self.args.tidb_port, db=database, threads=threads)
        run_cmd(cmd_prepare, show_stdout=True)

    def handle_tpch_prepare(self,  database, warehouses, threads):
        cmd_cleanup_db = "drop database if exists {}".format(database)
        self.execute(cmd_cleanup_db)
        cmd_prepare_db = "create database if not exists {}".format(database)
        self.execute(cmd_prepare_db)
        cmd_prepare = "tiup bench tpch --sf {warehouses} prepare --host {host} --port {port}  --db {db} --threads {threads} --analyze".format(
            warehouses=warehouses, host=self.args.host, port=self.args.tidb_port, db=database, threads=threads)
        run_cmd(cmd_prepare, show_stdout=True)

    def restart_tidb(self, args=None):
        if args is None:
            args = self.docker_env
        cmd = "{} {}/mem-arbitrator-tests/run-tidb-docker.sh".format(
            args, SCRIPT_DIR)
        if self.args.tidb_bin:
            test_tidb_bin_path = self.args.tidb_bin
            logger.info("Using custom tidb-server binary: {}".format(
                test_tidb_bin_path))
            cmd = "test_tidb_bin_path={}".format(
                test_tidb_bin_path) + " " + cmd

        logger.info(
            "Restarting TiDB, cmd:\n\t{}".format(cmd))
        _, stderr, ret = run_cmd(cmd, show_stdout=False)
        if ret:
            raise Exception("Failed to restart TiDB: {}".format(stderr))
        else:
            while True:
                try:
                    self.get_tidb_server_memory_limit(ommit_exception=False)
                    break
                except Exception as e:
                    logger.warning("TiDB server is not ready")
                    time.sleep(1)
                    continue
            logger.info("TiDB restarted successfully.")

    def restart_tikv(self):
        cmd = "tiup cluster restart tzg-ng-cluster -R tikv -y"
        logger.info("Restarting TiKV with command: {}".format(cmd))
        _, stderr, ret = run_cmd(cmd)
        if ret:
            raise Exception("Failed to restart TiKV: {}".format(stderr))

    def set_tidb_mem_arbitrator_mode(self, mode):
        try:
            self.execute(
                "set global tidb_mem_arbitrator_mode='{}'".format(mode))
        except Exception as e:
            if mode != "disable":
                raise e

        if mode != "disable":
            self.execute("set global tidb_server_memory_limit='95%'")
            self.execute("set global tidb_enable_gogc_tuner=default")
            self.execute("set global tidb_gogc_tuner_threshold=default")
        else:
            self.execute("set global tidb_gogc_tuner_threshold=default")
            self.execute("set global tidb_enable_gogc_tuner=default")
            self.execute("set global tidb_server_memory_limit=default")
            self.execute("set global tidb_server_memory_limit_sess_min_size=default")

    def get_tidb_mem_arbitrator_mode(self, ommit_exception=True):
        try:
            rows = self.execute(
                "select @@tidb_mem_arbitrator_mode", show_debug=False)
            assert len(
                rows) == 1, "Expected one row for tidb_mem_arbitrator_mode"
            return rows[0][0]
        except Exception as e:
            if ommit_exception:
                logger.error(
                    "Failed to get tidb_mem_arbitrator_mode: {}".format(e))
            else:
                raise e
            return None

    def get_tidb_server_memory_limit(self, ommit_exception=True):
        try:
            rows = self.execute(
                "select @@tidb_server_memory_limit", show_debug=False)
            assert len(
                rows) == 1, "Expected one row for tidb_server_memory_limit"
            return rows[0][0]
        except Exception as e:
            if ommit_exception:
                logger.error(
                    "Failed to get tidb_server_memory_limit: {}".format(e))
            else:
                raise e
            return None

    def ensure_tidb_running(self):
        if self.get_tidb_server_memory_limit() is None:
            logger.critical("TiDB server is down; Restarting TiDB")
            self.restart_tidb()

    def handle_tpcc(self, mode, database, warehouses, threads, dur):
        if not dur:
            dur = "3m"
        logger.info("Handling TPC-C with mode: {}, database: {}, warehouses: {}, threads: {} duration: {}".format(
            mode, database, warehouses, threads, dur))
        self.ensure_tidb_running()
        self.restart_tikv()
        self.handle_tpcc_prepare(database, warehouses, 20)
        self.analyze_table_all()
        self.restart_tidb()
        self.set_tidb_mem_arbitrator_mode(mode)
        self.handle_tpcc_run(mode, database, warehouses, threads, dur)

    def compact_tikv(self):
        cmd1 = "tiup ctl:nightly tikv --pd 0.0.0.0:2377 compact-cluster -c default"
        cmd2 = "tiup ctl:nightly tikv --pd 0.0.0.0:2377 compact-cluster -c write"
        for cmd in [cmd1, cmd2]:
            logger.info("Compacting TiKV with command: {}".format(cmd))
            _, stderr, ret = run_cmd(cmd)
            if ret:
                raise Exception("Failed to compact TiKV: {}".format(stderr))

    def handle_tpch(self, mode, database, warehouses, threads, count: int = 22):
        logger.info("Handling TPC-H with mode: {}, database: {}, warehouses: {}, threads: {}, count: {}".format(
            mode, database, warehouses, threads, count))
        self.ensure_tidb_running()
        self.set_tidb_mem_arbitrator_mode("disable")
        self.handle_tpch_prepare(database, warehouses, 20)
        self.analyze_table_all()
        self.set_tidb_mem_arbitrator_mode(mode)
        self.restart_tikv()
        self.restart_tidb()
        self.handle_tpch_run(mode, database, warehouses, threads, )

    def kill_session(self, sid):
        sql = 'KILL {}'.format(sid)
        self.execute(sql)

    def kill_all_sessions(self):
        sql = 'SELECT ID FROM INFORMATION_SCHEMA.CLUSTER_PROCESSLIST'
        for s in self.execute(sql):
            self.kill_session(s[0])

    @wrap_run_time
    def batch_run_sql(self, sql, cuncurrency=1, loop=1, ):
        from threading import Thread
        import threading

        logger.info('run sql in concurrency `{}`:\n\t{}'.format(
            cuncurrency, sql[:100]))
        mutex = threading.Lock()
        threads = []
        res = {"success": 0, "fail": 0, "latency": 0}
        for i in range(cuncurrency):
            name = 'test_{}'.format(i)
            threads.append(Thread(target=self.oom_test_func,
                                  daemon=True, name=name,
                                  args=(sql, name, loop, mutex, res)))
        for t in threads:
            t.start()

        for t in threads:
            t.join()

        logger.info(
            "All threads finished, results: success={}, fail={}, avg-latency: {:.2f}s".format(
                res["success"], res["fail"], res["latency"] / res["success"] if res["success"] > 0 else 0))

        if self.get_tidb_server_memory_limit() is None:
            logger.critical("TiDB server is down")
        return res

    def oom_test_func(self, sql, name, loop, lock, res):
        conn = mysql.connector.connect(
            host=self.args.host,
            user="root",
            password="",
            port=self.args.tidb_port,
        )
        import random
        for _ in range(loop):
            try:
                with conn.cursor() as cursor:
                    bg = time.time()
                    if type(sql) is str:
                        sql = [sql]
                    for s in sql:
                        s = s.replace(
                            "???", "{}_{}".format(name, random.randint(1, 1e9)))
                        cursor.execute(s)
                        cursor.fetchall()
                    dur = time.time() - bg
                    lock.acquire()
                    res["success"] += 1
                    res["latency"] += dur
                    lock.release()
            except Exception as e:
                lock.acquire()
                res["fail"] += 1
                lock.release()
        conn.close()

    def run(self):
        args = self.args
        if args.mode:
            mode = normal_mode(args.mode)
            self.ensure_tidb_running()
            logger.info("Setting tidb_mem_arbitrator_mode to {}".format(mode))
            self.set_tidb_mem_arbitrator_mode(mode)

        if args.restart:
            logger.info("Restarting TiDB server")
            self.restart_tidb()

        if args.restart_kv:
            logger.info("Restarting TiKV server")
            self.restart_tikv()

        if args.kill_all:
            self.kill_all_sessions()
            return
        if args.cmp:
            self.run_impl(args.tidb_port, )
            self.run_impl(args.tidb_port_2, )
            return
        if args.sql2:
            if args.restart:
                self.restart_tidb()
            cuncurrency, loop = args.sql2.split(';')
            logger.info("Running benchmark with concurrency {} and loop {}".format(
                cuncurrency, loop))
            with open("{}/mem-arbitrator-tests/sql2".format(SCRIPT_DIR), "r") as f:
                sqls = f.read()
            self.batch_run_sql(sql=sqls, cuncurrency=int(
                cuncurrency), loop=int(loop))
            return
        if args.sql2_prepare:
            if args.restart:
                self.restart_tidb()
            cuncurrency, loop = args.sql2_prepare.split(';')
            logger.info("Running benchmark with concurrency {} and loop {}".format(
                cuncurrency, loop))
            with open("{}/mem-arbitrator-tests/sql2.prepare".format(SCRIPT_DIR), "r") as f:
                sqls = f.read()
            self.batch_run_sql(sql=sqls, cuncurrency=int(
                cuncurrency), loop=int(loop))
            return
        if args.tpcc:
            tpcc = args.tpcc.split(';')
            mode, db, warehouses, threads, dur = tpcc[:5]
            self.handle_tpcc(normal_mode(mode), db,
                             int(warehouses), int(threads), dur)
            return
        if args.tpch:
            tpch = args.tpch.split(';')
            mode, db, warehouses, threads, count = tpch[:5]
            self.handle_tpch(normal_mode(mode), db,
                             int(warehouses), int(threads), int(count))
            return
        if args.tpcc_run:
            tpcc_run = args.tpcc_run.split(';')
            mode, db, warehouses, threads, dur = tpcc_run[:5]
            mode = normal_mode(mode)
            logger.info("Handling TPC-C with mode: {}, database: {}, warehouses: {}, threads: {} duration: {}".format(
                mode, db, warehouses, threads, dur))
            self.ensure_tidb_running()
            self.analyze_table_all()
            self.restart_tidb()
            self.set_tidb_mem_arbitrator_mode(mode)
            self.handle_tpcc_run(mode, db, warehouses, threads, dur)
            return
        if args.tpch_run:
            tpch_run = args.tpch_run.split(';')
            mode, db, warehouses, threads, count = tpch_run[:5]
            mode = normal_mode(mode)
            if not count:
                count = 22
            logger.info("Handling TPC-H with mode: {}, database: {}, warehouses: {}, threads: {}, count: {}".format(
                mode, db, warehouses, threads, count))
            self.ensure_tidb_running()
            self.analyze_table_all()
            self.set_tidb_mem_arbitrator_mode(mode)
            self.restart_tidb()
            self.handle_tpch_run(mode, db, int(
                warehouses), int(threads), int(count))
            return
        return

    @property
    def mysql_conn(self):
        if self._mysql_conn and self._mysql_conn.is_connected():
            return self._mysql_conn
        conn = mysql.connector.connect(
            host=self.args.host,
            user="root",
            password="",
            port=self.args.tidb_port,
        )
        self._mysql_conn = conn
        return conn

    def run_impl(self, host, tidb_port, database):
        if prepare_table:
            mydb = mysql.connector.connect(
                host=host,
                user="root",
                password="",
                port=tidb_port,
            )
            self.prepare_table_data(mydb, database, prepare_table_name, 1000,
                                    1000, 200, 6, sleep_time=5.0)
            return

        tidb_server_memory_limit = int(self.args.limit) * GB
        # prepare_table_data(cursor, mydb, 'test', 't', 6, 2, 3)
        server_limit = get_global_server_memory_limit()
        if server_limit != tidb_server_memory_limit:
            set_global_var(tidb_server_memory_limit_name,
                           tidb_server_memory_limit)
            server_limit = tidb_server_memory_limit
        logger.info('`{}`: {} B ({:.3f} GB)'.format(
            'tidb_server_memory_limit', server_limit, float(server_limit)/1024**3))
        self.handle_dump()

        sql = sql1
        if self.args.sql2:
            sql = sql2
        if self.args.sql3:
            sql = sql3

        parel_query_cnt = 1
        loop_query_times = 1

        if self.args.parel is not None:
            parel_query_cnt = int(self.args.parel)
        if self.args.loop is not None:
            loop_query_times = int(self.args.loop)

        if True:
            fail = 0
            bg = time.time()
            for _ in range(loop_query_times):
                res = batch_run_sql(parel_query_cnt, sql)
                for k, (val, ok) in res.items():
                    if ok:
                        if show_mysql_result:
                            logger.info("{}:\n{}\n".format(k, val))
                    else:
                        logger.error("{}:\n{}\n".format(k, val))
                        fail += 1
            timecost = time.time() - bg
            method = logger.info
            if fail:
                method = logger.warning
            n = parel_query_cnt * loop_query_times
            method("run {} sql, {} sql success, {} sql failed, time-costs=[{:.4}s, avg({:.4}s)]".format(
                n, n-fail, fail, timecost, timecost / n))

    def execute(self, sql, show_debug=True, ):
        assert self.mysql_conn.is_connected(), "Failed to connect to MySQL"
        with self.mysql_conn.cursor() as cursor:
            if show_debug:
                logger.debug('execute sql:\n\t{}'.format(sql))
            cursor.execute(sql)
            rows = cursor.fetchall()
            if show_debug:
                logger.debug('execute result:\n\t{}'.format(rows))
            return rows

    def prepare_table_data(self, db, table_name, n, hash_key_n, hash_key_size, double_cnt, sleep_time=None):
        total = n * n / hash_key_n

        name = '`{}`.`{}`'.format(db, table_name)
        schema_build_sql = """
    drop table if exists {0};
    CREATE TABLE {0} (
    `k1` varchar(300) DEFAULT NULL,
    `k2` varchar(300) DEFAULT NULL,
    `v1` varchar(300) DEFAULT NULL
    );
    """.format(name)
        keys = [
            "k1", "k2", "v1"
        ]
        template = (
            "INSERT INTO {} "
            "({}) "
            "VALUES ({})".format(
                name,
                ','.join(keys),
                ','.join(['%({})s'.format(k) for k in keys])
            )
        )

        self.execute(schema_build_sql)

        for i in range(n):
            hash_key = (str(i % hash_key_n) *
                        hash_key_size)[:hash_key_size], i % hash_key_n
            data = {
                "k1": hash_key[0],
                "k2": hash_key[1],
                "v1": "B" * hash_key_size,
            }
            assert len(data.keys()) == len(keys)
            self.execute(template, data)
            if i % 500 == 0:
                mydb.commit()
        mydb.commit()

        cnt_sql = "select count(1) from {}".format(name)

        for _ in range(double_cnt):
            double_table_data(name)

        run_mysql_client(cnt_sql)
        run_mysql_client('analyze table {}'.format(name), )
        if sleep_time:
            sleep(sleep_time)


if __name__ == '__main__':
    main()
