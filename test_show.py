#!/usr/bin/env python3
"""shop.py show 命令的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

所有用例均在独立临时目录中运行：显式传入的数据库文件位于该目录，
未传 --db 时也把子进程的工作目录切到该目录，因此不会接触
项目或用户已有的 shop.sqlite3。
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
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)
EMPTY_CART_SHOW = ("总数量 0", "总金额 0")


def make_broken_db(path):
    """构造可正常打开但购物车缺少 quantity 列的数据库。

    products 表结构正常并含 P001、P002；cart 表只有 product_id 列，
    保留一条 P001 记录。show 的查询会因此触发 sqlite3.Error。
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE products ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO products (id, name, price) VALUES (?, ?, ?)",
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
        )
        conn.execute("CREATE TABLE cart (product_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO cart (product_id) VALUES ('P001')")
        conn.commit()
    finally:
        conn.close()


class ShopShowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py。

        db=None 表示不传 --db（走当前工作目录下的默认文件）；
        db 其余取值（含 Path）会展开为 --db 参数。
        cwd 为 None 时使用临时目录，避免在项目目录生成 shop.sqlite3。
        """
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

    def seed_sample(self, db=...):
        """用公开的 add 语义准备固定样例购物车。"""
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return self.tmpdir / "cart.sqlite3" if db is ... else db

    def assert_show(self, db, expected_lines):
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def assert_broken_db_untouched(self, db):
        """核对损坏样例未被修复：表结构、商品资料与 P001 记录原样保留。"""
        conn = sqlite3.connect(db)
        try:
            cart_columns = [
                row[1] for row in conn.execute("PRAGMA table_info(cart)")
            ]
            self.assertEqual(cart_columns, ["product_id"])
            cart_rows = conn.execute(
                "SELECT product_id FROM cart"
            ).fetchall()
            self.assertEqual(cart_rows, [("P001",)])
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            self.assertEqual(
                products,
                [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
            )
        finally:
            conn.close()

    def test_show_sample_cart_lists_lines_and_totals(self):
        """正常场景：按编号升序输出商品行，末尾给出总数量与总金额。"""
        db = self.seed_sample()

        self.assert_show(db, SAMPLE_SHOW)

        # 重复查看不改变数量与输出
        self.assert_show(db, SAMPLE_SHOW)

    def test_show_empty_cart_prints_zero_totals(self):
        """空购物车：只输出总数量 0 与总金额 0。"""
        db = self.tmpdir / "cart.sqlite3"
        self.assert_show(db, EMPTY_CART_SHOW)

    def test_show_initializes_missing_default_db(self):
        """不传 --db 且文件不存在：按既有规则初始化并显示空购物车。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"
        self.assertFalse(default_db.exists())

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "\n".join(EMPTY_CART_SHOW) + "\n")
        self.assertTrue(default_db.is_file())

    def test_show_broken_db_reports_unavailable(self):
        """cart 缺少 quantity 列：仅报数据库不可用并退出 1，且数据库不被改动。"""
        db = self.tmpdir / "broken.sqlite3"
        make_broken_db(db)

        result = self.run_shop(["show"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

        # 再次查看返回相同错误
        result = self.run_shop(["show"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

        # 不删除记录、不补列、不重建表：结构与内容原样保留
        self.assert_broken_db_untouched(db)

    def test_show_broken_default_db_reports_unavailable(self):
        """默认数据库遇到同样的读取错误时，输出规则一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"
        make_broken_db(default_db)

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assert_failure(result, 1, "数据库不可用")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assert_failure(result, 1, "数据库不可用")

        self.assert_broken_db_untouched(default_db)

    def test_show_with_extra_arg_is_argument_error(self):
        """show 多带参数：报参数错误并退出 2，正常库与损坏库均不读取。"""
        db = self.seed_sample()
        result = self.run_shop(["show", "extra"], db=db)
        self.assert_failure(result, 2, "参数错误")
        self.assert_show(db, SAMPLE_SHOW)

        broken = self.tmpdir / "broken.sqlite3"
        make_broken_db(broken)
        result = self.run_shop(["show", "extra"], db=broken)
        self.assert_failure(result, 2, "参数错误")
        self.assert_broken_db_untouched(broken)


if __name__ == "__main__":
    unittest.main()
