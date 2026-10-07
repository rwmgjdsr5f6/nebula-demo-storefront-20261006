#!/usr/bin/env python3
"""shop.py catalog / budget 目录浏览共用流程的回归测试。

重构后 catalog 与 budget 走同一套“读取商品 -> 统一行格式 -> 一次性
展示”的流程，本测试针对这一流程做端到端回归，覆盖：

- 验收固定样例：P001 单价 1200、P002 单价 900 时，
  catalog --sort price 先显示 P002 再显示 P001，budget 900 只显示 P002；
- 两个入口使用同一行格式（编号 名称 单价，字段间一个空格，末行换行），
  无匹配时标准输出为空、退出码 0；
- catalog 的区分大小写字面匹配、百分号/下划线非通配符、首尾空格参与；
- 连续经两个入口查看后，商品资料与已有购物车数量保持不变，
  首次使用仍初始化两件演示商品，筛选与排序偏好不保存；
- 两个入口共用同一读取失败处理：商品行无法读取时统一只报
  “数据库不可用”退出 1，标准输出为空、标准错误只有对应一行。

只使用 Python 标准库与本地 SQLite；在项目目录执行：

    python -m unittest test_catalog_budget_browse
    python -m unittest discover

所有用例均在独立临时目录中运行，不会接触项目或用户已有的
shop.sqlite3。现有 test_catalog*.py / test_budget*.py 的预期不改写，
本文件只额外锁定重构后的共用行为。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 首次创建数据库时写入的两件固定演示商品（编号升序）
INITIAL_CATALOG = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)

# 验收固定样例：把 P002 改为 900 后的库内资料
REPRICED_ID_ORDER = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 900",
)
REPRICED_PRICE_ORDER = (
    "P002 虚拟马克杯 900",
    "P001 虚拟笔记本 1200",
)


class CatalogBudgetBrowseTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py。

        db=None 表示不传 --db；db 其余取值（含 Path）展开为 --db 参数。
        cwd 为 None 时使用临时目录，避免在项目目录生成 shop.sqlite3。
        """
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
            if db is ...:
                db = self.tmpdir / "shop.sqlite3"
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

    def assert_ok_quiet(self, result, expected_stdout=""):
        """核对成功调用：退出码 0、标准错误为空、标准输出与预期逐字节一致。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, expected_stdout)

    def assert_db_unavailable(self, result):
        """读取失败：退出码 1、空标准输出、标准错误只有“数据库不可用”一行。"""
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)

    def expected_text(self, lines):
        """非空行序列 -> 每行一个换行、末行保留换行；空序列 -> 零字节。"""
        return "\n".join(lines) + "\n" if lines else ""

    def snapshot(self, db):
        """读取商品与购物车全部内容，供连续查看后的不变性核对。"""
        with sqlite3.connect(str(db)) as conn:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        return products, cart

    def set_price(self, db, product_id, price):
        """用公开的 price 语义改价。"""
        result = self.run_shop(["price", product_id, str(price)], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        return result

    # ---- 验收固定样例 ---------------------------------------------------

    def test_acceptance_sample_sort_price_then_budget(self):
        """P001=1200、P002=900：sort price 先 P002 后 P001；budget 900 只 P002。"""
        db = self.tmpdir / "sample.sqlite3"
        self.set_price(db, "P002", 900)

        # catalog --sort price：按整数分单价升序（900 在 1200 前），同价按编号
        result = self.run_shop(["catalog", "--sort", "price"], db=db)
        self.assert_ok_quiet(result, self.expected_text(REPRICED_PRICE_ORDER))

        # 不带排序仍按编号升序
        result = self.run_shop(["catalog"], db=db)
        self.assert_ok_quiet(result, self.expected_text(REPRICED_ID_ORDER))

        # budget 900：只比较单件现价、包含等于上限，按编号升序 -> 只有 P002
        result = self.run_shop(["budget", "900"], db=db)
        self.assert_ok_quiet(result, "P002 虚拟马克杯 900\n")

        # budget 899：无商品入选 -> 零字节成功
        result = self.run_shop(["budget", "899"], db=db)
        self.assert_ok_quiet(result, "")

    def test_catalog_and_budget_share_one_line_format(self):
        """同一商品在两个入口的行文本逐字节一致：一空格分隔、末行换行。"""
        db = self.tmpdir / "sample.sqlite3"
        self.set_price(db, "P002", 900)

        catalog = self.run_shop(["catalog"], db=db)
        budget = self.run_shop(["budget", "2500"], db=db)
        self.assertEqual(catalog.stdout, budget.stdout)
        self.assertEqual(
            catalog.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 900\n",
        )
        for line in catalog.stdout.splitlines():
            self.assertEqual(line.split(" "), line.split())  # 无多余空白
            self.assertEqual(len(line.split(" ")), 3)        # 恰好三个字段
        self.assertTrue(catalog.stdout.endswith("\n"))
        self.assertNotIn("\n\n", catalog.stdout)

    # ---- catalog 字面匹配规则（共用读取/过滤流程） ----------------------

    def test_catalog_literal_case_sensitive_match(self):
        """区分大小写的原始文字匹配；% 与 _ 是普通字符；首尾空格参与匹配。"""
        db = self.tmpdir / "lit.sqlite3"

        # 编号或名称完整包含关键词才命中
        self.assert_ok_quiet(
            self.run_shop(["catalog", "P002"], db=db),
            "P002 虚拟马克杯 2500\n",
        )
        # 小写 p 不命中大写 P
        self.assert_ok_quiet(self.run_shop(["catalog", "p001"], db=db), "")
        # 百分号、下划线不是通配符
        for keyword in ("%", "_", "P00_", "P00%", "%P%"):
            with self.subTest(keyword=keyword):
                self.assert_ok_quiet(
                    self.run_shop(["catalog", keyword], db=db), ""
                )
        # 关键词首尾空格按字面参与匹配，编号/名称都不含该串
        for keyword in (" P001", "P001 ", "P001 虚拟笔记本"):
            with self.subTest(keyword=keyword):
                self.assert_ok_quiet(
                    self.run_shop(["catalog", keyword], db=db), ""
                )
        # 每件商品只输出一次：关键词同时命中编号/名称也不重复
        self.assert_ok_quiet(
            self.run_shop(["catalog", "P"], db=db),
            self.expected_text(INITIAL_CATALOG),
        )

    def test_no_match_is_quiet_success_in_both_entries(self):
        """无匹配时两个入口都标准输出为空、退出码 0。"""
        db = self.tmpdir / "empty_match.sqlite3"
        cases = (
            ["catalog", "P999"],
            ["catalog", "P999", "--sort", "price"],
            ["budget", "0"],
        )
        for args in cases:
            with self.subTest(args=args):
                self.assert_ok_quiet(self.run_shop(args, db=db), "")

    # ---- 首次初始化与连续查看的只读不变性 -------------------------------

    def test_fresh_db_initializes_two_demo_products_for_both_entries(self):
        """两个入口首次使用都初始化两件演示商品，之后不重置库内资料。"""
        db = self.tmpdir / "fresh.sqlite3"
        self.assertFalse(db.exists())

        self.assert_ok_quiet(
            self.run_shop(["catalog"], db=db),
            self.expected_text(INITIAL_CATALOG),
        )
        self.assertTrue(db.is_file())
        self.assertEqual(
            self.snapshot(db)[0],
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
        )
        # budget 首次使用（另一全新库）同样初始化两件演示商品
        db2 = self.tmpdir / "fresh_budget.sqlite3"
        self.assert_ok_quiet(
            self.run_shop(["budget", "2500"], db=db2),
            self.expected_text(INITIAL_CATALOG),
        )

    def test_repeated_browsing_keeps_products_and_cart_unchanged(self):
        """连续经 catalog/budget 查看：改价与购物车数量保持不变，偏好不保存。"""
        db = self.tmpdir / "ro.sqlite3"
        self.set_price(db, "P002", 900)
        # 准备已有购物车：P001 两件、P002 一件
        for product_id, quantity in (("P001", "2"), ("P002", "1")):
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)

        before = self.snapshot(db)
        # 两个入口、含关键词与排序形式交替连续查看
        browse_args = (
            ["catalog"],
            ["catalog", "虚拟"],
            ["catalog", "--sort", "price"],
            ["catalog", "P002", "--sort", "price"],
            ["budget", "2500"],
            ["budget", "900"],
            ["budget", "0"],
            ["catalog", "P999"],
        )
        for args in browse_args:
            with self.subTest(args=args):
                result = self.run_shop(args, db=db)
                self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(db), before)

        # 排序与筛选偏好不保存：最后一次普通 catalog 仍按编号升序、无筛选
        result = self.run_shop(["catalog"], db=db)
        self.assert_ok_quiet(result, self.expected_text(REPRICED_ID_ORDER))

        # 购物车数量/金额不受目录浏览影响
        show = self.run_shop(["show"], db=db)
        self.assert_ok_quiet(
            show,
            "P001 虚拟笔记本 1200 2 2400\n"
            "P002 虚拟马克杯 900 1 900\n"
            "总数量 3\n总金额 3300\n",
        )

    def test_budget_ignores_cart_quantity_and_discount_while_browsing(self):
        """budget 只比较单件现价：购物车数量与满减门槛不影响筛选结果。"""
        db = self.tmpdir / "cart.sqlite3"
        self.set_price(db, "P002", 900)
        for product_id, quantity in (("P001", "10"), ("P002", "3")):
            self.assertEqual(
                self.run_shop(["add", product_id, quantity], db=db).returncode, 0
            )
        # 购物车总额远超满减门槛，但与单件预算无关：900 仍只显示 P002
        self.assert_ok_quiet(
            self.run_shop(["budget", "900"], db=db),
            "P002 虚拟马克杯 900\n",
        )

    # ---- 两个入口共用的读取失败处理 -------------------------------------

    def make_products_unreadable_view(self, path):
        """构造“能打开、建表与初始化通过、但读不到商品”的异常数据库。

        products 被替换为引用已删除底层表的视图：open_db 的
        CREATE TABLE IF NOT EXISTS 因同名对象存在而跳过，
        INSERT OR IGNORE 由 INSTEAD OF INSERT 触发器吞掉，
        只有 SELECT 商品行时数据库才报错。
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

    def test_unreadable_products_is_db_unavailable_in_both_entries(self):
        """商品行无法读取时两个入口共用同一失败结果：退出 1、空输出、一行错误。"""
        db = self.tmpdir / "broken_view.sqlite3"
        self.make_products_unreadable_view(db)

        with sqlite3.connect(str(db)) as conn:
            cart_before = conn.execute(
                "SELECT product_id, quantity FROM cart"
            ).fetchall()

        browse_args = (
            ["catalog"],
            ["catalog", "P001"],
            ["catalog", "--sort", "price"],
            ["catalog", "虚拟", "--sort", "price"],
            ["budget", "900"],
        )
        for args in browse_args:
            with self.subTest(args=args):
                self.assert_db_unavailable(self.run_shop(args, db=db))

        # 失败前后购物车与 schema 保持不变，重复失败不产生部分输出
        with sqlite3.connect(str(db)) as conn:
            cart_after = conn.execute(
                "SELECT product_id, quantity FROM cart"
            ).fetchall()
        self.assertEqual(cart_after, cart_before)
        self.assertEqual(cart_after, [("P001", 2)])

    def test_unopenable_db_is_db_unavailable_in_both_entries(self):
        """目标路径是目录：两个入口都只报数据库不可用退出 1。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()
        for args in (["catalog"], ["budget", "900"]):
            with self.subTest(args=args):
                self.assert_db_unavailable(self.run_shop(args, db=directory))


if __name__ == "__main__":
    unittest.main()
