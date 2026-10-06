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


def build_broken_db(path):
    """构造可正常打开但 cart 表缺少 quantity 列的异常样例数据库。

    products 表结构正常并含 P001、P002；cart 表保留 product_id 列
    和一条 P001 记录，唯独没有 quantity 列，使 show 的查询失败。
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

    def read_db_state(self, db):
        """直接读取数据库的表结构、商品目录与购物车记录，用于核对未被改动。"""
        conn = sqlite3.connect(db)
        try:
            schema = conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' ORDER BY name"
            ).fetchall()
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute("SELECT * FROM cart").fetchall()
        finally:
            conn.close()
        return schema, products, cart

    def test_show_sample_cart(self):
        """正常场景：两件 P001、一件 P002，按编号升序显示并汇总。"""
        db = self.seed_sample()

        self.assert_show(db, SAMPLE_SHOW)
        # 重复查看不改变数量与输出
        self.assert_show(db, SAMPLE_SHOW)

    def test_show_empty_cart(self):
        """空购物车：只输出总数量 0 与总金额 0。"""
        db = self.tmpdir / "cart.sqlite3"
        self.assert_show(db, EMPTY_CART_SHOW)

    def test_show_first_use_initializes_db(self):
        """首次使用不存在的数据库：自动创建并初始化，购物车为空。"""
        db = self.tmpdir / "fresh.sqlite3"
        self.assertFalse(db.exists())

        self.assert_show(db, EMPTY_CART_SHOW)

        self.assertTrue(db.is_file())
        _, products, cart = self.read_db_state(db)
        self.assertEqual(
            products,
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
        )
        self.assertEqual(cart, [])

    def test_show_uses_saved_name_and_price(self):
        """已保存的商品名称与单价以数据库内容为准，而非代码内置目录。"""
        db = self.seed_sample()
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "UPDATE products SET name = ?, price = ? WHERE id = ?",
                ("改名笔记本", 1500, "P001"),
            )
            conn.commit()
        finally:
            conn.close()

        self.assert_show(
            db,
            (
                "P001 改名笔记本 1500 2 3000",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 3",
                "总金额 5500",
            ),
        )

    def test_show_broken_cart_schema_is_db_unavailable(self):
        """cart 表缺 quantity 列：仅报数据库不可用并退出 1，数据库保持原样。"""
        db = self.tmpdir / "broken.sqlite3"
        build_broken_db(db)
        before = self.read_db_state(db)

        result = self.run_shop(["show"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

        # 再次查看返回相同错误，不能把无法读取的购物车当作空购物车
        result = self.run_shop(["show"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

        # 表结构、商品资料与那条 P001 记录均保持不变
        self.assertEqual(self.read_db_state(db), before)
        _, products, cart = self.read_db_state(db)
        self.assertEqual(
            products,
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
        )
        self.assertEqual(cart, [("P001",)])

    def test_show_broken_default_db_is_db_unavailable(self):
        """不传 --db 时默认数据库遇到同样的读取错误：输出规则一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"
        build_broken_db(default_db)

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assert_failure(result, 1, "数据库不可用")

    def test_show_with_extra_arg_is_argument_error(self):
        """show 多带参数：仅报参数错误并退出 2，即使数据库不能读取也优先处理。"""
        db = self.tmpdir / "broken.sqlite3"
        build_broken_db(db)

        result = self.run_shop(["show", "extra"], db=db)
        self.assert_failure(result, 2, "参数错误")

        # 参数错误优先于数据库读取：数据库未被触碰
        _, _, cart = self.read_db_state(db)
        self.assertEqual(cart, [("P001",)])

    def test_show_missing_db_directory_is_unavailable(self):
        """--db 指向不存在目录中的文件：仅输出数据库不可用并退出 1。"""
        db = self.tmpdir / "no_such_dir" / "cart.sqlite3"

        result = self.run_shop(["show"], db=db)
        self.assert_failure(result, 1, "数据库不可用")


if __name__ == "__main__":
    unittest.main()
