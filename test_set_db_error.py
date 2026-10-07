#!/usr/bin/env python3
"""shop.py set 命令数据库写入失败的可重复回归测试。

复现的问题：数据库能正常打开、show 与 catalog 仍能读取原有内容，
但针对 P001 的本次设定变更（UPDATE 或 DELETE）被数据库拒绝。
按 README 约定“任何失败操作都不会改变已有购物车内容”，
此时应只输出一行“数据库不可用”并以退出码 1 结束，
且不改变表结构、商品目录与原有购物车记录；
解除写入拒绝后，同一数据库上的对应设定仍可正常完成。

异常样例通过给 cart 表添加只针对 P001 的 RAISE(ABORT) 触发器构造：
读取不受影响，只有 P001 的设定写入被拒绝；DROP TRIGGER 即恢复正常。

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

# 固定样例：两件 P001、一件 P002
SAMPLE = (("P001", "2"), ("P002", "1"))
SAMPLE_CART = [("P001", 2), ("P002", 1)]
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)
# set P001 3 成功后：P001 三件、P002 一件
SET_THREE_SHOW = (
    "P001 虚拟笔记本 1200 3 3600",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 4",
    "总金额 6100",
)
# set P001 0 成功后：仅剩 P002 一件
ONLY_P002_SHOW = (
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 1",
    "总金额 2500",
)
CATALOG_OUTPUT = "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n"

# 只拒绝 P001 设定变更的触发器：UPDATE 对应设为正数，DELETE 对应设为零
REJECT_TRIGGERS = (
    "CREATE TRIGGER reject_p001_cart_update "
    "BEFORE UPDATE ON cart WHEN OLD.product_id = 'P001' "
    "BEGIN SELECT RAISE(ABORT, 'reject P001 set'); END",
    "CREATE TRIGGER reject_p001_cart_delete "
    "BEFORE DELETE ON cart WHEN OLD.product_id = 'P001' "
    "BEGIN SELECT RAISE(ABORT, 'reject P001 set'); END",
)
TRIGGER_NAMES = ("reject_p001_cart_update", "reject_p001_cart_delete")


class SetDatabaseErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=...):
        """以子进程运行 shop.py。"""
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
            cwd=str(self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    def seed_sample(self):
        """用公开的 add 语义准备固定样例购物车（P001 两件、P002 一件）。"""
        db = self.tmpdir / "cart.sqlite3"
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return db

    def break_db(self, db):
        """把健康样例库变成异常样例：仅 P001 的设定写入被数据库拒绝。"""
        conn = sqlite3.connect(str(db))
        try:
            for statement in REJECT_TRIGGERS:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()

    def restore_db(self, db):
        """解除写入拒绝：删除触发器，同一数据库恢复可写。"""
        conn = sqlite3.connect(str(db))
        try:
            for name in TRIGGER_NAMES:
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            conn.commit()
        finally:
            conn.close()

    def snapshot(self, db):
        """重新打开数据库，读取失败后必须保持一致的状态：
        建表语句、商品名称与价格、两条购物车记录（含数量）。
        """
        conn = sqlite3.connect(str(db))
        try:
            schema = conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name IN ('products', 'cart') "
                "ORDER BY name"
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
        self.assertNotIn("reject", result.stderr.lower())

    def assert_show(self, db, expected_lines):
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_catalog(self, db):
        """商品目录保持原样：编号、名称、单价均不变。"""
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, CATALOG_OUTPUT)

    def check_failure_then_recovery(self, target, success_stdout, recovered_show):
        """异常样例上 set P001 被拒且状态不变；解除拒绝后设定成功。"""
        db = self.seed_sample()
        self.break_db(db)
        before = self.snapshot(db)

        # 异常样例能正常打开：show 与 catalog 仍读取原有内容
        self.assert_show(db, SAMPLE_SHOW)
        self.assert_catalog(db)

        # 针对 P001 的本次设定变更被数据库拒绝
        result = self.run_shop(["set", "P001", target], db=db)
        self.assert_db_unavailable(result)

        # 失败后重新打开数据库：表结构、商品资料、购物车记录与失败前一致，
        # P001 数量仍为 2，P002 数量仍为 1
        self.assertEqual(self.snapshot(db), before)
        self.assertEqual(self.snapshot(db)[2], SAMPLE_CART)

        # 另起进程 show：仍按编号升序显示原有明细
        self.assert_show(db, SAMPLE_SHOW)

        # 重复失败调用不改变数量、不移除记录
        result = self.run_shop(["set", "P001", target], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(db), before)
        self.assert_show(db, SAMPLE_SHOW)

        # 解除写入拒绝后，同一数据库上的对应设定正常完成
        self.restore_db(db)
        result = self.run_shop(["set", "P001", target], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, success_stdout)
        self.assertEqual(result.stderr, "")
        self.assert_show(db, recovered_show)
        self.assert_catalog(db)

    def test_set_positive_db_error_keeps_state_and_recovers(self):
        """设为正数分支：set P001 3 被拒时状态不变，恢复后恰为三件。"""
        self.check_failure_then_recovery("3", "P001 数量 3\n", SET_THREE_SHOW)

    def test_set_to_zero_db_error_keeps_state_and_recovers(self):
        """设为零分支：set P001 0 被拒时状态不变，恢复后移除记录。"""
        self.check_failure_then_recovery("0", "P001 数量 0\n", ONLY_P002_SHOW)

    def test_set_same_quantity_db_error_keeps_state(self):
        """目标与当前相同仍要写库：UPDATE 被拒时数量保持两件，恢复后成功。"""
        db = self.seed_sample()
        self.break_db(db)

        result = self.run_shop(["set", "P001", "2"], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(db)[2], SAMPLE_CART)

        self.restore_db(db)
        result = self.run_shop(["set", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 2\n")
        self.assert_show(db, SAMPLE_SHOW)


if __name__ == "__main__":
    unittest.main()
