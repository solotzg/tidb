#!/usr/bin/env python3

import argparse
import mysql.connector
from utils import *
from bench import Runner as BaseRunner


sql1 = "desc analyze select * from test_tcoc_3429_v2.PM_ORG_VT order by ORG_CD,EKAK_CSTMR_CD"


class Runner(BaseRunner):
    def __init__(self, args):
        super().__init__(args)

    def init_schema(self):
        res, err, code = run_cmd("mysql -h {} -P {} -u root -D test -e 'source {}' ".format(
            self.args.host,
            self.args.tidb_port, SCRIPT_DIR+"mem-arbitrator-tests/sql4"), show_stdout=True)
        assert code == 0, "Failed to init schema: {}".format(err)

    def init_data(self):
        if not self.mysql_conn.is_connected():
            self.mysql_conn.reconnect()
        assert self.mysql_conn.is_connected(), "Failed to connect to MySQL"
        with self.mysql_conn.cursor() as cursor:
            stmt = "INSERT IGNORE INTO PM_ORG_VT (EKAK_CSTMR_CD, ORG_CD) VALUES (%s,%s)"
            n = 1519390
            batch = 100
            bg = ord('0')
            d = ord('z') - bg + 1
            for i in range(n // batch):
                data = []
                for j in range(batch):
                    m = i * batch + j
                    ekak_cstmr_cd = ""
                    org_cd = ""
                    for _ in range(5):
                        x = m % d + bg
                        m //= d
                        ekak_cstmr_cd += chr(x)
                    for _ in range(6):
                        x = m % d + bg
                        m //= d
                        org_cd += chr(x)
                    data.append((ekak_cstmr_cd, org_cd))
                cursor.executemany(stmt, data)
                self.mysql_conn.commit()
                logger.info("Inserted {} records".format(len(data)))


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument(
        '--host', help="TiDB host", default="0.0.0.0")
    parser.add_argument(
        '--port', help="TiDB port", type=int, default=4066)
    parser.add_argument(
        '--prepare', help="prepare schema and data", action='store_true')
    parser.add_argument(
        '--mode', help="set tidb_mem_arbitrator_mode", )
    parser.add_argument(
        '--bench', help="benchmark oom test", )
    parser.add_argument(
        '--restart', help="restart tidb server", default=1, choices=[0, 1], type=int)

    args = parser.parse_args()
    args.cop_cache = 0
    args.max_memory = "4G"
    args.max_cpus = 2

    runner = Runner(args)
    runner.ensure_tidb_running()

    if args.prepare:
        logger.info("Preparing schema and data")
        runner.init_schema()
        runner.init_data()
        logger.info("Preparation done")
        return

    if args.mode:
        args.mode = normal_mode(args.mode)
        logger.info(
            "Setting tidb_mem_arbitrator_mode to `{}`".format(args.mode))
        sql = "set global tidb_mem_arbitrator_mode = {} ".format(args.mode)
        runner.execute(sql, show_debug=True)

    if args.bench:
        if args.restart:
            runner.restart_tidb()
        cuncurrency, loop = args.bench.split(';')
        logger.info("Running benchmark with concurrency {} and loop {}".format(
            cuncurrency, loop))
        runner.batch_run_sql(sql=sql1, cuncurrency=int(
            cuncurrency), loop=int(loop))


if __name__ == "__main__":
    main()
