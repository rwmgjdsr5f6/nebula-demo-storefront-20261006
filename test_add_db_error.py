#!/usr/bin/env python3
"""shop.py add 命令数据库错误处理的可重复回归测试。

复现的问题：products 表正常、cart 表缺少 quantity 列时，
add 能打开数据库但写入购物车失败，旧版本直接抛出 SQLite 异常堆栈。
按 README 约定应只输出一行“数据库不可用”并以退出码 1 结束，
且不改变表结构、商品目录与原有购物车记录。

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

PRODUCTS = [
    ("P001", "虚拟笔记本", 1200),
    ("P002", "虚拟马克杯", 2500),
]


class AddDatabaseErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py（db=None 表示不传 --db）。"""
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
            if db is ...:
                db = self.tmpdir / "broken.sqlite3"
            cmd += ["--db", str(db)]
        cmd += list(args)
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            cmd,
            cwd=str(cwd if cwd is not None else self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    def make_broken_db(self, path):
        """构造异常数据库：products 正常且有两件演示商品；

        cart 表只有 product_id 主键并已存在 P001 记录，缺少 quantity 列。
        """
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(
                "CREATE TABLE products ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL)"
            )
            conn.executemany(
                "INSERT INTO products (id, name, price) VALUES (?, ?, ?)",
                PRODUCTS,
            )
            conn.execute("CREATE TABLE cart (product_id TEXT PRIMARY KEY)")
            conn.execute("INSERT INTO cart (product_id) VALUES (?)", ("P001",))
            conn.commit()
        finally:
            conn.close()

    def snapshot(self, path):
        """读取失败后必须保持一致的状态：建表语句、商品资料、购物车记录。"""
        conn = sqlite3.connect(str(path))
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
                "SELECT product_id FROM cart ORDER BY product_id"
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
        self.assertNotIn("quantity", result.stderr.lower())

    def assert_failure(self, result, code, message):
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_broken_cart_table_reports_db_error_and_keeps_state(self):
        """缺 quantity 列的 cart 表：add 只报数据库不可用，重复调用结果一致。"""
        db = self.tmpdir / "broken.sqlite3"
        self.make_broken_db(db)
        before = self.snapshot(db)

        # 典型输入：P001 已在购物车，但累加 quantity 必然失败
        result = self.run_shop(["add", "P001", "1"], db=db)
        self.assert_db_unavailable(result)

        # 失败前后的表结构、商品资料、原购物车记录完全一致
        self.assertEqual(self.snapshot(db), before)

        # 再试一件购物车里没有的商品：INSERT 同样因缺列失败
        result = self.run_shop(["add", "P002", "2"], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(db), before)

        # 重复调用得到同一结果
        result = self.run_shop(["add", "P001", "1"], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(db), before)

    def test_broken_db_validation_order_unchanged(self):
        """异常数据库上参数与数量、编号校验的既有优先级不变，且不触达写入。"""
        db = self.tmpdir / "broken.sqlite3"
        self.make_broken_db(db)
        before = self.snapshot(db)

        # 缺少/多出参数仍报参数错误（退出 2）
        self.assert_failure(
            self.run_shop(["add", "P001"], db=db), 2, "参数错误"
        )
        self.assert_failure(
            self.run_shop(["add", "P001", "1", "x"], db=db), 2, "参数错误"
        )

        # 数量校验先于编号校验与购物车写入
        self.assert_failure(
            self.run_shop(["add", "P999", "0"], db=db), 2, "数量必须为正整数"
        )
        self.assert_failure(
            self.run_shop(["add", "P999", "1"], db=db), 2, "未知商品"
        )

        self.assertEqual(self.snapshot(db), before)

    def test_broken_default_db_in_temp_cwd_same_result(self):
        """默认 shop.sqlite3 入口与显式 --db 行为一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"
        self.make_broken_db(default_db)
        before = self.snapshot(default_db)

        result = self.run_shop(["add", "P001", "1"], db=None, cwd=workdir)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(default_db), before)

        result = self.run_shop(["add", "P999", "0"], db=None, cwd=workdir)
        self.assert_failure(result, 2, "数量必须为正整数")
        result = self.run_shop(["add", "P999", "1"], db=None, cwd=workdir)
        self.assert_failure(result, 2, "未知商品")
        self.assertEqual(self.snapshot(default_db), before)

    def test_healthy_db_leading_zero_accumulation_and_totals(self):
        """正常数据库冒烟核对：两件 P001 + 一件 P002 后 add P001 01 → 3。"""
        db = self.tmpdir / "healthy.sqlite3"
        for args in (["add", "P001", "2"], ["add", "P002", "1"]):
            result = self.run_shop(args, db=db)
            self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["add", "P001", "01"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 3\n")
        self.assertEqual(result.stderr, "")

        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200 3 3600\n"
            "P002 虚拟马克杯 2500 1 2500\n"
            "总数量 4\n"
            "总金额 6100\n",
        )


if __name__ == "__main__":
    unittest.main()
