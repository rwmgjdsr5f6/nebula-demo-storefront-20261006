#!/usr/bin/env python3
"""shop.py rename 命令数据库写入失败的可重复回归测试。

复现的问题：数据库能正常打开、catalog 与 show 仍能读取原有内容，
但针对 P001 的改名写入（UPDATE products）被数据库拒绝。
按 README 约定“任何失败操作都不会改变已有数据”，
此时应只输出一行“数据库不可用”并以退出码 1 结束，
且不改变表结构、商品名称/单价与购物车记录；
解除写入拒绝后，同一数据库上的同一改名仍可正常完成。

异常样例通过给 products 表添加只针对 P001 的 RAISE(ABORT) 触发器构造：
读取不受影响，只有 P001 的改名写入被拒绝；DROP TRIGGER 即恢复正常。

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

# 固定样例：P001 虚拟笔记本 单价 1500 分、购物车两件；
# P002 虚拟马克杯 单价 2500 分、购物车一件
SAMPLE_PRODUCTS = [("P001", "虚拟笔记本", 1500), ("P002", "虚拟马克杯", 2500)]
SAMPLE_CART = [("P001", 2), ("P002", 1)]
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1500 2 3000",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 5500",
)
SAMPLE_CATALOG = "P001 虚拟笔记本 1500\nP002 虚拟马克杯 2500\n"

# rename P001 演示笔记本 成功后：只有 P001 名称变化，
# 单价、编号、数量、小计、汇总与 P002 资料全部不变
RENAMED_SHOW = (
    "P001 演示笔记本 1500 2 3000",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 5500",
)
RENAMED_CATALOG = "P001 演示笔记本 1500\nP002 虚拟马克杯 2500\n"

# 只拒绝 P001 改名写入的触发器：products 表上针对 P001 的 UPDATE 被拒绝，
# 其余读取与写入（含 open_db 的 INSERT OR IGNORE 目录初始化）不受影响
REJECT_TRIGGER = (
    "CREATE TRIGGER reject_p001_rename "
    "BEFORE UPDATE ON products WHEN OLD.id = 'P001' "
    "BEGIN SELECT RAISE(ABORT, 'reject P001 rename'); END"
)
TRIGGER_NAME = "reject_p001_rename"


class RenameDatabaseErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=...):
        """以子进程运行 shop.py，工作目录与数据库都位于独立临时目录。"""
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
            if db is ...:
                db = self.tmpdir / "demo.sqlite3"
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
        """用公开命令准备固定样例：P001 单价 1500 分两件、P002 单价 2500 分一件。"""
        db = self.tmpdir / "demo.sqlite3"
        result = self.run_shop(["price", "P001", "1500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "P001 单价 1500\n")
        for product_id, quantity in (("P001", "2"), ("P002", "1")):
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return db

    def break_db(self, db):
        """把健康样例库变成异常样例：仅 P001 的改名写入被数据库拒绝。"""
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
        """重新打开数据库，读取失败后必须保持一致的状态：
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

    def assert_catalog(self, db, expected_output):
        """另起进程调用 catalog，核对持久化后的商品目录输出。"""
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, expected_output)

    def test_rename_db_error_keeps_state_and_recovers(self):
        """rename P001 演示笔记本 被拒时状态不变，解除拒绝后同一改名成功。"""
        db = self.seed_sample()
        self.break_db(db)
        before = self.snapshot(db)

        # 异常样例能正常打开：catalog 与 show 仍读取原有内容
        self.assert_catalog(db, SAMPLE_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

        # 针对 P001 的本次改名写入被数据库拒绝
        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assert_db_unavailable(result)

        # 失败后重新打开数据库：表结构、两件商品的编号/名称/单价、
        # 购物车数量与失败前完全一致，不留下部分改动
        self.assertEqual(self.snapshot(db), before)
        self.assertEqual(self.snapshot(db)[1], SAMPLE_PRODUCTS)
        self.assertEqual(self.snapshot(db)[2], SAMPLE_CART)

        # 另起进程查看：P001 仍显示虚拟笔记本，总数量 3、总金额 5500
        self.assert_catalog(db, SAMPLE_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

        # 重复同次改名仍得到相同失败结果和原有数据
        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(db), before)
        self.assert_show(db, SAMPLE_SHOW)

        # 解除写入拒绝后，同一数据库上的原输入改名正常完成
        self.restore_db(db)
        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 名称 演示笔记本\n")
        self.assertEqual(result.stderr, "")

        # 新进程的 catalog 与 show 都展示新名称；
        # 单价、数量与汇总仍为样例原值，P002 资料不变
        self.assert_catalog(db, RENAMED_CATALOG)
        self.assert_show(db, RENAMED_SHOW)

    def test_rename_same_name_db_error_keeps_state_and_recovers(self):
        """目标名称与当前相同仍要写库：写入被拒时报数据库不可用而非成功。"""
        db = self.seed_sample()
        self.break_db(db)
        before = self.snapshot(db)

        # 目标就是当前名称“虚拟笔记本”：UPDATE 被触发器拒绝，
        # 不能仅因名称相同就当作保存成功
        result = self.run_shop(["rename", "P001", "虚拟笔记本"], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(db), before)
        self.assert_catalog(db, SAMPLE_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

        # 解除写入拒绝后，同一数据库上改为原名称同样成功，数据不变
        self.restore_db(db)
        result = self.run_shop(["rename", "P001", "虚拟笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 名称 虚拟笔记本\n")
        self.assertEqual(result.stderr, "")
        self.assert_catalog(db, SAMPLE_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)


if __name__ == "__main__":
    unittest.main()
