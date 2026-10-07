#!/usr/bin/env python3
"""shop.py price 命令数据库写入失败的可重复回归测试。

复现的问题：数据库能正常打开、catalog 与 show 仍能读取原有内容，
但针对 P001 的本次改价（UPDATE products）被数据库拒绝。
按 README 约定“任何失败操作都不会改变已有购物车内容”，
此时应只输出一行“数据库不可用”并以退出码 1 结束，
且不改变表结构、商品编号/名称/单价与购物车记录；
解除写入拒绝后，同一数据库上的对应改价仍可正常完成。

异常样例通过给 products 表添加只针对 P001 的 RAISE(ABORT) 触发器构造：
读取不受影响，只有 P001 的改价写入被拒绝；DROP TRIGGER 即恢复正常。

只使用 Python 3 标准库与本地 SQLite；在项目目录执行：

    python -m unittest test_price_db_error
    python -m unittest discover

所有用例均在独立临时目录、显式指定的数据库文件上运行，
结束即清理，不会接触项目或用户已有的 shop.sqlite3 等店铺文件。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 固定样例：P001 已保存单价 1500、购物车两件；P002 单价 2500、一件
SAMPLE = (("P001", "2"), ("P002", "1"))
SAMPLE_PRODUCTS = [
    ("P001", "虚拟笔记本", 1500),
    ("P002", "虚拟马克杯", 2500),
]
SAMPLE_CART = [("P001", 2), ("P002", 1)]
SAMPLE_CATALOG = (
    "P001 虚拟笔记本 1500",
    "P002 虚拟马克杯 2500",
)
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1500 2 3000",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 5500",
)

# price P001 001800 成功后：两件 P001 小计 3600
PRICE_1800_CATALOG = (
    "P001 虚拟笔记本 1800",
    "P002 虚拟马克杯 2500",
)
PRICE_1800_SHOW = (
    "P001 虚拟笔记本 1800 2 3600",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 6100",
)

# price P001 000 成功后：P001 仍在目录和购物车中，小计为零
PRICE_ZERO_CATALOG = (
    "P001 虚拟笔记本 0",
    "P002 虚拟马克杯 2500",
)
PRICE_ZERO_SHOW = (
    "P001 虚拟笔记本 0 2 0",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 2500",
)

# 只拒绝 P001 改价的触发器：products 表上 P001 行的 UPDATE 被 ABORT
REJECT_TRIGGER = (
    "CREATE TRIGGER reject_p001_price "
    "BEFORE UPDATE ON products WHEN OLD.id = 'P001' "
    "BEGIN SELECT RAISE(ABORT, 'reject P001 price'); END"
)
TRIGGER_NAME = "reject_p001_price"


class PriceDatabaseErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=...):
        """以子进程运行 shop.py，每次都是全新进程（等价于重启后核对）。"""
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
        """用公开命令行语义准备固定样例：两件 P001、一件 P002，P001 单价 1500。"""
        db = self.tmpdir / "cart.sqlite3"
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        result = self.run_shop(["price", "P001", "001500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 1500\n")
        self.assertEqual(result.stderr, "")
        return db

    def break_db(self, db):
        """把健康样例库变成异常样例：仅 P001 的改价写入被数据库拒绝。"""
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(REJECT_TRIGGER)
            conn.commit()
        finally:
            conn.close()

    def restore_db(self, db):
        """解除写入拒绝：删除触发器，同一数据库恢复可写。"""
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")
            conn.commit()
        finally:
            conn.close()

    def snapshot(self, db):
        """重新打开数据库，读取失败前后必须保持一致的状态：
        建表语句、商品编号/名称/单价、两条购物车记录（含数量）。
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

    def assert_catalog(self, db, expected_lines):
        """另起进程调用 catalog，核对持久化后的完整目录输出。"""
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def check_failure_then_recovery(self, price_text, success_stdout,
                                   recovered_catalog, recovered_show):
        """异常样例上 price P001 被拒且状态不变；解除拒绝后改价成功。"""
        db = self.seed_sample()
        self.break_db(db)
        before = self.snapshot(db)

        # 异常样例能正常打开：catalog 与 show 仍读取原有内容
        self.assert_catalog(db, SAMPLE_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

        # 针对 P001 的本次改价被数据库拒绝
        result = self.run_shop(["price", "P001", price_text], db=db)
        self.assert_db_unavailable(result)

        # 失败后重新打开数据库：表结构、商品编号/名称/单价与购物车记录
        # 均与失败前一致，P001 单价仍为 1500，P002 资料与数量不变
        self.assertEqual(self.snapshot(db), before)
        self.assertEqual(self.snapshot(db)[1], SAMPLE_PRODUCTS)
        self.assertEqual(self.snapshot(db)[2], SAMPLE_CART)

        # 另起进程 catalog/show：P001 仍为 1500，两条明细与汇总没有变化
        self.assert_catalog(db, SAMPLE_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

        # 重复同一次失败操作：退出码、输出与保存数据仍然相同
        result = self.run_shop(["price", "P001", price_text], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(db), before)
        self.assert_catalog(db, SAMPLE_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

        # 解除写入拒绝后，对同一数据库重试原输入：正常成功，标准错误为空
        self.restore_db(db)
        result = self.run_shop(["price", "P001", price_text], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, success_stdout)
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertEqual(result.stderr, "")

        # 成功输出以换行结束，目录与购物车按新单价持久化，
        # P002 的资料及数量始终不变
        self.assert_catalog(db, recovered_catalog)
        self.assert_show(db, recovered_show)

    def test_price_positive_db_error_keeps_state_and_recovers(self):
        """非零单价分支：price P001 001800 被拒时状态不变，恢复后单价 1800。"""
        self.check_failure_then_recovery(
            "001800",
            "P001 单价 1800\n",
            PRICE_1800_CATALOG,
            PRICE_1800_SHOW,
        )

    def test_price_zero_db_error_keeps_state_and_recovers(self):
        """零单价分支：price P001 000 被拒时状态不变，恢复后单价为零且仍在车中。"""
        self.check_failure_then_recovery(
            "000",
            "P001 单价 0\n",
            PRICE_ZERO_CATALOG,
            PRICE_ZERO_SHOW,
        )

    def test_price_same_value_db_error_keeps_state(self):
        """设成现有单价仍要写库：UPDATE 被拒时单价保持 1500，恢复后成功。"""
        db = self.seed_sample()
        self.break_db(db)
        before = self.snapshot(db)

        result = self.run_shop(["price", "P001", "1500"], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(db), before)
        self.assert_catalog(db, SAMPLE_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

        self.restore_db(db)
        result = self.run_shop(["price", "P001", "1500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 1500\n")
        self.assertEqual(result.stderr, "")
        self.assert_catalog(db, SAMPLE_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)


if __name__ == "__main__":
    unittest.main()
