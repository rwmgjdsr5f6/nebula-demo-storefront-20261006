#!/usr/bin/env python3
"""shop.py catalog / budget 目录浏览共用读取与展示流程的回归测试。

这两个入口在重构后共用同一条「读取 → 格式化 → 输出」流程，本测试
专门锁定该流程在两个入口上一致的行为，而不重复各自既有的细项用例：

- 固定验收样例（P001 单价 1200、P002 单价 900）：catalog --sort price
  先显示 P002 再显示 P001；budget 900 只显示 P002。
- 两个入口的商品行完全同形（编号 名称 单价，字段间一个空格，末行
  带换行，无匹配时零字节），名称与单价均以数据库当前内容为准。
- 连续查看不改变商品资料与已有购物车数量。
- 目录读取失败时两个入口共用同一条失败路径：只报“数据库不可用”
  退出 1，标准输出为空，且无部分商品行。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest test_catalog_budget_flow
    python -m unittest discover

所有用例均在独立临时目录中运行，不会接触项目或用户已有的 shop.sqlite3。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 验收固定样例：把 P002 单价改为 900 后的目录
SAMPLE_BY_ID = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 900",
)
SAMPLE_BY_PRICE = (
    "P002 虚拟马克杯 900",
    "P001 虚拟笔记本 1200",
)


class CatalogBudgetFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=...):
        """以子进程运行 shop.py；db 缺省使用临时目录中的固定文件。"""
        if db is ...:
            db = self.tmpdir / "flow.sqlite3"
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
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

    def prepare_sample(self, db=...):
        """建库并按验收样例把 P002 单价改为 900（P001 保持 1200）。"""
        if db is ...:
            db = self.tmpdir / "flow.sqlite3"
        self.assertEqual(self.run_shop(["catalog"], db=db).returncode, 0)
        result = self.run_shop(["price", "P002", "900"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 单价 900\n")
        return db

    def assert_quiet_empty(self, result):
        """无匹配的成功调用：标准输出零字节、标准错误为空、退出码 0。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def assert_db_unavailable(self, result):
        """读取失败：退出码 1、空标准输出、仅一行错误、无堆栈与部分商品行。"""
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)

    def snapshot(self, db):
        """读取商品与购物车全部内容，供只读性核对。"""
        with sqlite3.connect(str(db)) as conn:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        return products, cart

    # ---- 验收固定样例 ---------------------------------------------------

    def test_acceptance_sample_sort_price_and_budget(self):
        """P001=1200、P002=900：--sort price 先 P002 后 P001；budget 900 仅 P002。"""
        db = self.prepare_sample()

        result = self.run_shop(["catalog", "--sort", "price"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "\n".join(SAMPLE_BY_PRICE) + "\n")

        result = self.run_shop(["budget", "900"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "P002 虚拟马克杯 900\n")

    # ---- 两入口共用行格式与空结果 ---------------------------------------

    def test_two_entries_share_identical_line_format(self):
        """同一批商品经 catalog 与 budget 输出时字节完全一致：同形、同顺序。"""
        db = self.prepare_sample()

        catalog = self.run_shop(["catalog"], db=db)
        budget = self.run_shop(["budget", "1200"], db=db)
        self.assertEqual(catalog.returncode, 0, catalog.stderr)
        self.assertEqual(budget.returncode, 0, budget.stderr)
        expected = "\n".join(SAMPLE_BY_ID) + "\n"
        self.assertEqual(catalog.stdout, expected)
        self.assertEqual(budget.stdout, expected)

        for line in catalog.stdout.splitlines():
            # 字段间恰好一个空格：编号、名称、单价三段
            self.assertEqual(line.split(" "), line.split())
            self.assertEqual(len(line.split(" ")), 3)
        self.assertTrue(catalog.stdout.endswith("\n"))
        self.assertNotIn("\n\n", catalog.stdout)

    def test_two_entries_emit_zero_bytes_without_match(self):
        """catalog 无匹配关键词与 budget 上限过低都输出零字节，退出码 0。"""
        db = self.prepare_sample()
        self.assert_quiet_empty(self.run_shop(["catalog", "P999"], db=db))
        self.assert_quiet_empty(self.run_shop(["catalog", "P999", "--sort", "price"], db=db))
        self.assert_quiet_empty(self.run_shop(["budget", "899"], db=db))

    def test_two_entries_use_saved_name_and_price(self):
        """两个入口都读库中当前名称与价格，不恢复内置演示资料。"""
        db = self.prepare_sample()
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE products SET name = ?, price = ? WHERE id = ?",
                ("演示笔记本", 800, "P001"),
            )
            conn.commit()

        catalog = self.run_shop(["catalog", "演示"], db=db)
        self.assertEqual(catalog.stdout, "P001 演示笔记本 800\n")
        # 预算只认单件现价：800 命中改名后的 P001，900 仍只命中 P002
        self.assertEqual(
            self.run_shop(["budget", "800"], db=db).stdout,
            "P001 演示笔记本 800\n",
        )
        self.assertEqual(
            self.run_shop(["budget", "900"], db=db).stdout,
            "P001 演示笔记本 800\nP002 虚拟马克杯 900\n",
        )

    # ---- 连续查看只读 ---------------------------------------------------

    def test_consecutive_views_keep_products_and_cart(self):
        """连续用两种入口查看后，商品资料与已有购物车数量保持不变。"""
        db = self.prepare_sample()
        for product_id, quantity in (("P001", "3"), ("P002", "2")):
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
        before = self.snapshot(db)

        for args in (
            ["catalog"],
            ["catalog", "虚拟"],
            ["catalog", "--sort", "price"],
            ["catalog", "虚拟", "--sort", "price"],
            ["budget", "1200"],
            ["budget", "900"],
            ["budget", "0"],
        ):
            result = self.run_shop(args, db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.snapshot(db), before)

        # 购物车行小计按查看期间保持的改价后单价计算，数量未被浏览改动
        show = self.run_shop(["show"], db=db)
        self.assertEqual(
            show.stdout,
            "P001 虚拟笔记本 1200 3 3600\n"
            "P002 虚拟马克杯 900 2 1800\n"
            "总数量 5\n总金额 5400\n",
        )

    # ---- 两入口共用读取失败路径 -----------------------------------------

    def make_products_unreadable_view(self, path):
        """构造「能打开、初始化语句通过、但 SELECT 商品行报错」的数据库。

        products 是引用已删除底层表的视图，INSERT 由 INSTEAD OF 触发器
        吞掉；只有共用读取流程真正查询商品时数据库才报错。
        """
        conn = sqlite3.connect(str(path))
        try:
            conn.execute(
                "CREATE TABLE products ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE cart ("
                "product_id TEXT PRIMARY KEY, quantity INTEGER NOT NULL)"
            )
            conn.executemany(
                "INSERT INTO products (id, name, price) VALUES (?, ?, ?)",
                (("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 900)),
            )
            conn.execute("INSERT INTO cart (product_id, quantity) VALUES ('P001', 2)")
            conn.execute("DROP TABLE products")
            conn.execute("CREATE TABLE ghost(id TEXT, name TEXT, price INTEGER)")
            conn.execute(
                "CREATE VIEW products AS SELECT id, name, price FROM ghost"
            )
            conn.execute(
                "CREATE TRIGGER products_ins INSTEAD OF INSERT ON products "
                "BEGIN SELECT 1; END"
            )
            conn.execute("DROP TABLE ghost")
            conn.commit()
        finally:
            conn.close()

    def test_read_failure_is_shared_by_catalog_and_budget(self):
        """商品表无法读取时两个入口都走同一失败路径，且购物车保持不变。"""
        db = self.tmpdir / "broken_view.sqlite3"
        self.make_products_unreadable_view(db)
        with sqlite3.connect(str(db)) as conn:
            cart_before = conn.execute(
                "SELECT product_id, quantity FROM cart"
            ).fetchall()

        for args in (
            ["catalog"],
            ["catalog", "笔记"],
            ["catalog", "--sort", "price"],
            ["budget", "900"],
        ):
            with self.subTest(args=args):
                self.assert_db_unavailable(self.run_shop(args, db=db))

        with sqlite3.connect(str(db)) as conn:
            cart_after = conn.execute(
                "SELECT product_id, quantity FROM cart"
            ).fetchall()
        self.assertEqual(cart_after, cart_before)
        self.assertEqual(cart_after, [("P001", 2)])

    def test_unopenable_db_is_unavailable_for_both_entries(self):
        """--db 指向现有目录：两个入口都只报数据库不可用退出 1。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()
        self.assert_db_unavailable(self.run_shop(["catalog"], db=directory))
        self.assert_db_unavailable(self.run_shop(
            ["catalog", "笔记", "--sort", "price"], db=directory))
        self.assert_db_unavailable(self.run_shop(["budget", "900"], db=directory))


if __name__ == "__main__":
    unittest.main()
