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

# 首次创建数据库时写入的固定目录（按单价降序）
INITIAL_DESC = (
    "P002 虚拟马克杯 2500",
    "P001 虚拟笔记本 1200",
)

# 把 P002 单价改为 900 后按单价降序的目录
REPRICED_DESC = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 900",
)

# SQLite INTEGER 上界，单价允许取到该值
MAX_PRICE = 9223372036854775807


class ShopCatalogSortDescTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py（与 test_catalog.py 相同的约定）。"""
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

    def assert_catalog(self, db, expected_lines, extra_args=(), keyword=None, cwd=None):
        """另起进程调用 catalog，逐行核对输出与干净的错误流。

        extra_args 原样追加在关键词之后（用于排序片段）；keyword 为 None
        时不带关键词。
        """
        args = ["catalog"]
        if keyword is not None:
            args.append(keyword)
        args += list(extra_args)
        result = self.run_shop(args, db=db, cwd=cwd)
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

    def test_sort_price_desc_orders_by_saved_price_then_id(self):
        """改价后 --sort price-desc 按库中整数分单价降序，同价按编号升序。"""
        db = self.tmpdir / "cart.sqlite3"
        self.set_price(db, "P002", 900)

        # 单价降序：1200 的 P001 排在 900 的 P002 前面
        self.assert_catalog(db, REPRICED_DESC, extra_args=["--sort", "price-desc"])

        # 同一状态下普通 catalog 仍按编号升序
        self.assert_catalog(db, (
            "P001 虚拟笔记本 1200",
            "P002 虚拟马克杯 900",
        ))

        # 两件商品同价时仍按编号升序：P001 在前
        self.set_price(db, "P001", 900)
        self.assert_catalog(db, (
            "P001 虚拟笔记本 900",
            "P002 虚拟马克杯 900",
        ), extra_args=["--sort", "price-desc"])

    def test_sort_price_desc_with_keyword_filters_then_sorts(self):
        """关键词与降序组合：先按原有规则筛选，再按单价降序输出。"""
        db = self.tmpdir / "cart.sqlite3"
        self.set_price(db, "P002", 900)

        # 关键词命中一件：只输出该件
        self.assert_catalog(
            db, ["P001 虚拟笔记本 1200"],
            keyword="笔记", extra_args=["--sort", "price-desc"],
        )
        # 关键词命中两件：按单价降序
        self.assert_catalog(
            db, REPRICED_DESC, keyword="虚拟", extra_args=["--sort", "price-desc"],
        )
        # 关键词命中编号子串
        self.assert_catalog(
            db, ["P002 虚拟马克杯 900"],
            keyword="P002", extra_args=["--sort", "price-desc"],
        )

    def test_sort_price_desc_no_match_is_quiet_success(self):
        """降序形式下无匹配：标准输出与标准错误都为空，退出码 0。"""
        db = self.tmpdir / "cart.sqlite3"
        result = self.run_shop(
            ["catalog", "P999", "--sort", "price-desc"], db=db
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_sort_price_desc_numeric_not_text_zero_last_and_bounds(self):
        """按数值比较单价；零排在正数后，INTEGER 上界准确比较与显示。"""
        db = self.tmpdir / "cart.sqlite3"
        # 数值降序：上界在零前面；文本比较无法得到这个顺序
        self.set_price(db, "P002", 0)
        self.set_price(db, "P001", MAX_PRICE)
        self.assert_catalog(db, (
            f"P001 虚拟笔记本 {MAX_PRICE}",
            "P002 虚拟马克杯 0",
        ), extra_args=["--sort", "price-desc"])

        # 上界与零互换后顺序随之颠倒，比较不丢精度
        self.set_price(db, "P001", 0)
        self.set_price(db, "P002", MAX_PRICE)
        self.assert_catalog(db, (
            f"P002 虚拟马克杯 {MAX_PRICE}",
            "P001 虚拟笔记本 0",
        ), extra_args=["--sort", "price-desc"])

    def test_sort_price_desc_does_not_touch_products_or_cart(self):
        """降序只影响本次展示：商品资料与购物车不变，也不保存排序偏好。"""
        db = self.tmpdir / "cart.sqlite3"
        self.set_price(db, "P002", 900)
        result = self.run_shop(["add", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_catalog(db, REPRICED_DESC, extra_args=["--sort", "price-desc"])

        # 库中商品资料保持改价后的内容，顺序查询不重置任何字段
        with sqlite3.connect(str(db)) as conn:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
        self.assertEqual(
            products,
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 900)],
        )
        # 购物车数量与金额不受影响
        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200 2 2400\n总数量 2\n总金额 2400\n",
        )
        # 排序偏好不保存：下一次普通 catalog 仍按编号升序
        self.assert_catalog(db, (
            "P001 虚拟笔记本 1200",
            "P002 虚拟马克杯 900",
        ))
        # 已有的升序排序也不受影响
        self.assert_catalog(db, (
            "P002 虚拟马克杯 900",
            "P001 虚拟笔记本 1200",
        ), extra_args=["--sort", "price"])

    def test_sort_price_desc_on_fresh_db_initializes_then_sorts(self):
        """对首次使用的库先按现有规则初始化目录，再按单价降序输出。"""
        db = self.tmpdir / "demo.sqlite3"
        self.assertFalse(db.exists())

        # 验收场景：初始目录降序为 P002 2500、P001 1200
        self.assert_catalog(db, INITIAL_DESC, extra_args=["--sort", "price-desc"])
        self.assertTrue(db.is_file())

        # 关键词“笔记”降序只剩 P001 一行
        self.assert_catalog(
            db, ["P001 虚拟笔记本 1200"],
            keyword="笔记", extra_args=["--sort", "price-desc"],
        )

    def test_sort_price_desc_accepts_default_db(self):
        """省略 --db 时降序同样作用于当前工作目录下的默认库。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        self.assert_catalog(
            None, INITIAL_DESC, extra_args=["--sort", "price-desc"], cwd=workdir,
        )
        self.assertTrue((workdir / "shop.sqlite3").is_file())

    def test_invalid_sort_forms_are_argument_error_before_db_open(self):
        """非法降序组合：统一参数错误退出 2，标准输出为空，且不创建数据库。"""
        db = self.tmpdir / "explicit.sqlite3"
        cases = (
            ["catalog", "--sort", "PRICE-DESC"],          # 只接受小写 price-desc
            ["catalog", "--sort", "Price-Desc"],
            ["catalog", "--sort", "price_desc"],          # 下划线不是连字符
            ["catalog", "--sort", "price-desc "],         # 尾随空格
            ["catalog", "笔记", "--sort"],                 # 排序片段不完整
            ["catalog", "--sort", "price-desc", "extra"],  # 片段之后追加参数
            ["catalog", "a", "b", "--sort", "price-desc"],  # 多个关键词
            ["catalog", "--sort", "price-desc", "--sort", "price-desc"],  # 重复
            ["catalog", "--sort", "price", "--sort", "price-desc"],       # 升降混排
            ["catalog", "", "--sort", "price-desc"],      # 空关键词
            ["catalog", "  \t ", "--sort", "price-desc"],  # 全空白关键词
        )
        for args in cases:
            with self.subTest(args=args):
                result = self.run_shop(args, db=db)
                self.assert_failure(result, 2, "参数错误")
                # 在打开数据库之前拒绝：显式路径的库文件不会被创建
                self.assertFalse(db.exists())

    def test_catalog_sort_keyword_alone_still_a_keyword(self):
        """catalog --sort 保持原语义：--sort 是单个关键词（无匹配，安静成功）。

        排序片段必须是两个独立参数：连写的 --sort=price-desc 同样只是
        普通关键词，不会触发降序。
        """
        db = self.tmpdir / "cart.sqlite3"
        for keyword in ("--sort", "--sort=price-desc", "--sort price-desc"):
            with self.subTest(keyword=keyword):
                result = self.run_shop(["catalog", keyword], db=db)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def test_sort_price_desc_db_unavailable(self):
        """数据库无法打开时降序形式同样只报数据库不可用退出 1，无商品行。"""
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
