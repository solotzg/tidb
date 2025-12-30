#!/usr/bin/env python3
import datetime
import logging
import os
import sys
import time
import types


SCRIPT_DIR = os.path.realpath(os.path.join(__file__, os.pardir, os.pardir))

orig_record_factory = logging.getLogRecordFactory()
log_colors = {
    logging.DEBUG: "\033[1;34m",  # blue
    logging.INFO: "\033[1;32m",  # green
    logging.WARNING: "\033[1;35m",  # magenta
    logging.ERROR: "\033[1;31m",  # red
    logging.CRITICAL: "\033[1;41m",  # red reverted
}


def get_message(ori):
    msg = str(ori.msg)
    if ori.args:
        msg = msg % ori.args
    msg = "{}{}{}".format(log_colors[ori.levelno], msg, "\033[0m")
    return msg


def record_factory(*args, **kwargs):
    record = orig_record_factory(*args, **kwargs)
    record.getMessage = types.MethodType(get_message, record)
    return record


logging.setLogRecordFactory(record_factory)


def _get_tz_offset():
    now_stamp = time.time()
    local_time = datetime.datetime.fromtimestamp(now_stamp)
    utc_time = datetime.datetime.utcfromtimestamp(now_stamp)
    offset = local_time - utc_time
    total_seconds = offset.total_seconds()
    flag = '+'
    if total_seconds < 0:
        flag = '-'
        total_seconds = -total_seconds
    mm, ss = divmod(total_seconds, 60)
    hh, mm = divmod(mm, 60)
    tz_offset = "%s%02d:%02d" % (flag, hh, mm)
    return tz_offset


def _init_logger(log_fmt, logger_name=None):
    assert logger_name != logging.root.name

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(fmt=log_fmt)
    logger.addHandler(handler)
    return logger


tz_offset = _get_tz_offset()
logger = _init_logger(log_fmt=logging.Formatter('[%(asctime)s.%(msecs)03d {}][%(levelname)s][%(message)s]'.format(tz_offset),
                                                datefmt='%Y/%m/%d %H:%M:%S'), logger_name='ROOT')
std_logger = _init_logger(log_fmt=logging.Formatter(
    '%(message)s'), logger_name='STDOUT')


def run_cmd(cmd, show_stdout=False, env=None, cb=None):
    import subprocess

    if show_stdout:
        logger.info("\nRUN CMD:\n\t{}".format(cmd))
    proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    if show_stdout:
        stdout = bytes()
        for line in proc.stdout:
            stdout += (line)
            logger.debug(line.decode('utf-8').rstrip())
        _, stderr = proc.communicate()
    elif cb:
        stdout = bytes()
        for line in proc.stdout:
            stdout += (line)
            cb(line.decode('utf-8').rstrip())
        _, stderr = proc.communicate()
    else:
        stdout, stderr = proc.communicate()
    return stdout.decode('utf-8'), stderr.decode('utf-8'), proc.returncode


def wrap_run_time(func):
    def wrap_func(*args, **kwargs):
        bg = time.time()
        r = func(*args, **kwargs)
        logger.debug('`{}`: time cost {:.3f}s'.format(
            func.__name__, time.time() - bg))
        return r

    return wrap_func


def sleep(s):
    logger.info('start to sleep `{:.2f}`sec'.format(s))
    time.sleep(s)
    logger.info('finish sleep `{:.2f}`sec'.format(s))


def normal_mode(mode: str):
    mode = mode.upper()
    if mode == "D":
        mode = "disable"
    elif mode == "P":
        mode = "priority"
    elif mode == "S":
        mode = "standard"
    return mode
