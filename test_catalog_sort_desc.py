#!/usr/bin/env python3
"""shop.py catalog --sort price-desc 的可重复回归测试。

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

# 首次创建数据库时按单价降序的目录（与编号升序相反）
INITIAL_DESC = (
    "P002 虚拟马克杯 2500",
    "P001 虚拟笔记本 1200",
)

# SQLite INTEGER 上界，单价允许取到该值
MAX_PRICE = 9223372036854775807


class ShopCatalogSortDescTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py（与 test_catalog_sort.py 相同的约定）。"""
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

    def set_price(self, db, product_id, price):
        """用公开的 price 语义改价，返回该次调用结果。"""
        result = self.run_shop(["price", product_id, str(price)], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        return result

    def assert_catalog_desc(self, db, expected_lines, keyword=None, cwd=None,
                            explicit_db=True):
        """另起进程调用 catalog --sort price-desc，逐行核对输出。"""
        args = ["catalog"]
        if keyword is not None:
            args.append(keyword)
        args += ["--sort", "price-desc"]
        result = self.run_shop(args, db=db if explicit_db else None, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        expected = "\n".join(expected_lines) + "\n" if expected_lines else ""
        self.assertEqual(result.stdout, expected)
        return result

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_fresh_db_desc_acceptance(self):
        """新库初始化两件演示商品后，降序依次输出 P002、P001。"""
        db = self.tmpdir / "demo.sqlite3"
        self.assertFalse(db.exists())
        self.assert_catalog_desc(db, INITIAL_DESC)
        self.assertTrue(db.is_file())

    def test_keyword_desc_acceptance(self):
        """关键词筛选沿用完整字面包含规则，命中结果按单价降序。"""
        db = self.tmpdir / "demo.sqlite3"
        # “笔记”只命中 P001：一行且仍走降序读取流程
        self.assert_catalog_desc(db, ["P001 虚拟笔记本 1200"], keyword="笔记")
        # “虚拟”命中两件：按单价降序
        self.assert_catalog_desc(db, INITIAL_DESC, keyword="虚拟")
        # 编号字段同样可命中
        self.assert_catalog_desc(db, ["P002 虚拟马克杯 2500"], keyword="P002")

    def test_reprice_reflected_immediately_and_preference_not_saved(self):
        """改价后下一次浏览立即按新值排序，且不保存降序偏好。"""
        db = self.tmpdir / "cart.sqlite3"
        self.set_price(db, "P001", 5000)
        self.assert_catalog_desc(db, (
            "P001 虚拟笔记本 5000",
            "P002 虚拟马克杯 2500",
        ))
        # 下一次普通 catalog 仍按编号升序，降序不是持久偏好
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 5000\nP002 虚拟马克杯 2500\n",
        )
        # 升序形式语义不变
        result = self.run_shop(["catalog", "--sort", "price"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P002 虚拟马克杯 2500\nP001 虚拟笔记本 5000\n",
        )

    def test_zero_after_positive_and_same_price_id_tiebreak(self):
        """零单价排在正单价之后；同价按编号升序。"""
        db = self.tmpdir / "cart.sqlite3"
        self.set_price(db, "P002", 0)
        self.assert_catalog_desc(db, (
            "P001 虚拟笔记本 1200",
            "P002 虚拟马克杯 0",
        ))
        # 两件同为零时按编号升序
        self.set_price(db, "P001", 0)
        self.assert_catalog_desc(db, (
            "P001 虚拟笔记本 0",
            "P002 虚拟马克杯 0",
        ))
        # 同价为正数时仍按编号升序
        self.set_price(db, "P001", 900)
        self.set_price(db, "P002", 900)
        self.assert_catalog_desc(db, (
            "P001 虚拟笔记本 900",
            "P002 虚拟马克杯 900",
        ))

    def test_numeric_not_text_and_bounds(self):
        """按整数值降序比较，文本顺序不同的价位与 INTEGER 上界均正确。"""
        db = self.tmpdir / "cart.sqlite3"
        self.set_price(db, "P002", MAX_PRICE)
        self.set_price(db, "P001", 900)
        self.assert_catalog_desc(db, (
            f"P002 虚拟马克杯 {MAX_PRICE}",
            "P001 虚拟笔记本 900",
        ))

    def test_no_match_is_quiet_success(self):
        """降序形式下无匹配：标准输出零字节、标准错误为空、退出码 0。"""
        db = self.tmpdir / "cart.sqlite3"
        result = self.run_shop(
            ["catalog", "P999", "--sort", "price-desc"], db=db
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_desc_does_not_touch_products_or_cart(self):
        """降序浏览不修改商品资料或购物车。"""
        db = self.tmpdir / "cart.sqlite3"
        self.set_price(db, "P002", 900)
        result = self.run_shop(["add", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_catalog_desc(db, (
            "P001 虚拟笔记本 1200",
            "P002 虚拟马克杯 900",
        ))
        with sqlite3.connect(str(db)) as conn:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        self.assertEqual(
            products,
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 900)],
        )
        self.assertEqual(cart, [("P001", 2)])

    def test_invalid_sort_forms_are_argument_error_before_db_open(self):
        """非法排序组合：参数错误退出 2、空输出，且不创建数据库。"""
        db = self.tmpdir / "explicit.sqlite3"
        cases = (
            ["catalog", "--sort", "PRICE-DESC"],          # 只接受小写
            ["catalog", "--sort", "Price-Desc"],
            ["catalog", "--sort", "price_desc"],           # 连字符不可替换
            ["catalog", "--sort", "price-desc "],          # 含尾空白
            ["catalog", "--sort", " price-desc"],
            ["catalog", "笔记", "--sort", "price-desc", "extra"],  # 片段后追加参数
            ["catalog", "a", "b", "--sort", "price-desc"],  # 多个关键词
            ["catalog", "--sort", "price-desc", "--sort", "price-desc"],  # 重复片段
            ["catalog", "--sort", "price", "--sort", "price-desc"],       # 混合重复
            ["catalog", "", "--sort", "price-desc"],        # 空关键词
            ["catalog", " \t ", "--sort", "price-desc"],    # 全空白关键词
            ["catalog", "笔记", "--sort"],                   # 排序片段不完整
        )
        for args in cases:
            with self.subTest(args=args):
                result = self.run_shop(args, db=db)
                self.assert_failure(result, 2, "参数错误")
                self.assertFalse(db.exists())

    def test_sort_token_alone_still_a_keyword(self):
        """catalog --sort 与连写形式仍按普通关键词处理（无匹配，安静成功）。"""
        db = self.tmpdir / "cart.sqlite3"
        for keyword in ("--sort", "--sort=price-desc", "--sort price-desc"):
            with self.subTest(keyword=keyword):
                result = self.run_shop(["catalog", keyword], db=db)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def run_shop_eq(self, db):
        """以 --db=路径 连写形式运行 shop.py（run_shop 只覆盖分段写法）。"""
        cmd = [
            sys.executable, str(SHOP), f"--db={db}",
            "catalog", "--sort", "price-desc",
        ]
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            cmd, cwd=str(self.tmpdir), capture_output=True, text=True,
            encoding="utf-8", env=env, timeout=30,
        )

    def test_default_db_and_equal_form(self):
        """省略 --db 与 --db=路径 两种写法下降序语义一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        self.assert_catalog_desc(None, INITIAL_DESC, cwd=workdir,
                                 explicit_db=False)
        self.assertTrue((workdir / "shop.sqlite3").is_file())

        eq_db = self.tmpdir / "eq.sqlite3"
        result = self.run_shop_eq(eq_db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P002 虚拟马克杯 2500\nP001 虚拟笔记本 1200\n",
        )
        self.assertTrue(eq_db.is_file())

    def test_desc_db_unavailable(self):
        """数据库无法打开时降序形式只报数据库不可用退出 1、无商品行。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()
        for args in (
            ["catalog", "--sort", "price-desc"],
            ["catalog", "笔记", "--sort", "price-desc"],
        ):
            with self.subTest(args=args):
                result = self.run_shop(args, db=directory)
                self.assert_failure(result, 1, "数据库不可用")


if __name__ == "__main__":
    unittest.main()
