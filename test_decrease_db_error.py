#!/usr/bin/env python3
"""shop.py decrease 命令数据库写入失败的可重复回归测试。

复现的场景：数据库可正常打开，products 与 cart 表结构完好，
show 与 catalog 均能读取原有内容，但 cart 表的 UPDATE/DELETE
被触发器拒绝，decrease 的本次变更无法提交。
按 README 约定“任何失败操作都不会改变已有购物车内容”：
应只输出一行“数据库不可用”并以退出码 1 结束，
不改变表结构、商品目录与原有购物车记录；解除写入拒绝后，
同一数据库上的减少操作仍可正常完成。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

所有用例均在独立临时目录中运行，不会接触项目或用户已有的店铺文件。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 固定样例：两件 P001、一件 P002（单价 1200 / 2500 分）
SAMPLE = (("P001", "2"), ("P002", "1"))
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)
SAMPLE_CATALOG = "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n"
# decrease P001 1 成功后：两件商品各一件
ONE_EACH_SHOW = (
    "P001 虚拟笔记本 1200 1 1200",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 2",
    "总金额 3700",
)
# decrease P001 2 成功后：P001 减至零被移除，仅剩 P002 一件
ONLY_P002_SHOW = (
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 1",
    "总金额 2500",
)

# 拒绝 cart 表写入的触发器：读取不受影响，UPDATE/DELETE 被中止
DENY_WRITE_TRIGGERS = (
    "CREATE TRIGGER reject_cart_update BEFORE UPDATE ON cart "
    "BEGIN SELECT RAISE(ABORT, 'write denied'); END",
    "CREATE TRIGGER reject_cart_delete BEFORE DELETE ON cart "
    "BEGIN SELECT RAISE(ABORT, 'write denied'); END",
)
DROP_WRITE_TRIGGERS = (
    "DROP TRIGGER reject_cart_update",
    "DROP TRIGGER reject_cart_delete",
)


class DecreaseDatabaseErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py（db=None 表示不传 --db）。"""
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
            if db is ...:
                db = self.tmpdir / "cart.sqlite3"
            cmd += ["--db", str(db)]
        cmd += list(args)
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"  # 强制子进程按 UTF-8 输出，结果与环境语言无关
        return subprocess.run(
            cmd,
            cwd=str(cwd if cwd is not None else self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    def seed_sample(self, db):
        """用公开的 add 语义准备固定样例购物车（P001 两件、P002 一件）。"""
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")

    def deny_writes(self, db):
        """为 cart 表加装拒绝 UPDATE/DELETE 的触发器（读取不受影响）。"""
        conn = sqlite3.connect(str(db))
        try:
            for statement in DENY_WRITE_TRIGGERS:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()

    def allow_writes(self, db):
        """解除写入拒绝：删除触发器，同一数据库恢复可写。"""
        conn = sqlite3.connect(str(db))
        try:
            for statement in DROP_WRITE_TRIGGERS:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()

    def snapshot(self, db):
        """读取失败后必须保持一致的状态：表结构（含触发器）、商品资料、购物车记录。"""
        conn = sqlite3.connect(str(db))
        try:
            schema = conn.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        finally:
            conn.close()
        return schema, products, cart

    def assert_db_unavailable(self, result):
        """数据库写入失败：退出码 1、空标准输出、仅一行错误、无堆栈与 SQL 详情。"""
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("sqlite3", result.stderr.lower())
        self.assertNotIn("write denied", result.stderr.lower())

    def assert_show(self, db, expected_lines):
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_catalog(self, db):
        """另起进程调用 catalog，核对商品目录保持原样。"""
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, SAMPLE_CATALOG)

    def test_partial_decrease_rejected_keeps_cart_then_recovers(self):
        """部分减少被数据库拒绝：购物车原样保留，解除拒绝后减一件成功。"""
        db = self.tmpdir / "cart.sqlite3"
        self.seed_sample(db)
        self.deny_writes(db)
        before = self.snapshot(db)

        # 异常样例可正常打开：show 与 catalog 仍能读取原有内容
        self.assert_show(db, SAMPLE_SHOW)
        self.assert_catalog(db)

        # 本次针对 P001 的减少被数据库拒绝：仅一行数据库不可用，退出码 1
        result = self.run_shop(["decrease", "P001", "1"], db=db)
        self.assert_db_unavailable(result)

        # 失败前后表结构、商品名称与价格、两条购物车记录完全一致
        self.assertEqual(self.snapshot(db), before)
        self.assertEqual(self.snapshot(db)[2], [("P001", 2), ("P002", 1)])

        # 重复失败调用不累积减少、不移除记录
        result = self.run_shop(["decrease", "P001", "1"], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(db), before)

        # 另起进程 show：仍按编号升序显示原有明细，总数量 3、总金额 4900 分
        self.assert_show(db, SAMPLE_SHOW)

        # 解除写入拒绝后同一数据库恢复正常：减一件成功，退出码 0、标准错误为空
        self.allow_writes(db)
        result = self.run_shop(["decrease", "P001", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db, ONE_EACH_SHOW)
        self.assert_catalog(db)

    def test_decrease_to_zero_rejected_keeps_cart_then_recovers(self):
        """恰好减至零被数据库拒绝：购物车原样保留，解除拒绝后减两件成功。"""
        db = self.tmpdir / "cart.sqlite3"
        self.seed_sample(db)
        self.deny_writes(db)
        before = self.snapshot(db)

        # 异常样例可正常打开：show 与 catalog 仍能读取原有内容
        self.assert_show(db, SAMPLE_SHOW)
        self.assert_catalog(db)

        # 本次针对 P001 的减少被数据库拒绝：仅一行数据库不可用，退出码 1
        result = self.run_shop(["decrease", "P001", "2"], db=db)
        self.assert_db_unavailable(result)

        # 失败前后表结构、商品名称与价格、两条购物车记录完全一致
        self.assertEqual(self.snapshot(db), before)
        self.assertEqual(self.snapshot(db)[2], [("P001", 2), ("P002", 1)])

        # 重复失败调用不累积减少、不移除记录
        result = self.run_shop(["decrease", "P001", "2"], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(db), before)

        # 另起进程 show：仍按编号升序显示原有明细，总数量 3、总金额 4900 分
        self.assert_show(db, SAMPLE_SHOW)

        # 解除写入拒绝后同一数据库恢复正常：减两件成功，退出码 0、标准错误为空
        self.allow_writes(db)
        result = self.run_shop(["decrease", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 0\n")
        self.assertEqual(result.stderr, "")

        # P001 减至零被移除，购物车只剩 P002：总数量 1、总金额 2500 分
        self.assert_show(db, ONLY_P002_SHOW)
        self.assert_catalog(db)


if __name__ == "__main__":
    unittest.main()
