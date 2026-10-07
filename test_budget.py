#!/usr/bin/env python3
"""shop.py budget 命令的可重复回归测试。

覆盖按单件预算浏览商品目录的行为：

- 只输出数据库中当前单价小于或等于上限的商品，按编号升序，
  格式与 catalog 一致；等于上限也能入选；没有匹配时输出为空。
- 名称与单价始终以数据库已保存内容为准（不恢复内置演示价格），
  改价与改名在重启后继续生效，不同数据库互不影响。
- 比较只看单件单价，与购物车数量和 preview 满减无关，且纯只读。
- 参数校验顺序为调用结构、数字格式、数值范围，
  三类错误都在打开数据库之前拒绝，不创建数据库文件。

只使用 Python 标准库；在项目目录执行：

    python -m unittest test_budget
    python -m unittest discover

所有用例均在独立临时目录、显式指定的数据库文件上运行，
结束即清理，不会接触项目或用户已有的 shop.sqlite3。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 首次创建数据库时写入的固定目录
INITIAL_PRODUCTS = (
    ("P001", "虚拟笔记本", 1200),
    ("P002", "虚拟马克杯", 2500),
)

# SQLite INTEGER 上界
MAX_PRICE = 9223372036854775807


class ShopBudgetTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py，每次都是全新进程（等价于重启后核对）。

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

    def assert_budget(self, db, limit, expected_lines, cwd=None):
        """调用 budget 并逐字节核对：行格式与 catalog 相同，末行带换行；
        expected_lines 为空元组时标准输出必须是空串（零字节）。
        """
        result = self.run_shop(["budget", str(limit)], db=db, cwd=cwd)
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

    def assert_rejected_before_open(self, args, code, message, db=None):
        """参数类错误必须在打开数据库前拒绝：目标数据库文件不得被创建。"""
        if db is None:
            db = self.tmpdir / "never_created.sqlite3"
        self.assertFalse(db.exists())
        result = self.run_shop(args, db=db)
        self.assert_failure(result, code, message)
        self.assertFalse(db.exists(), "参数错误不应创建数据库文件")

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

    # ---- 基本筛选与边界 -------------------------------------------------

    def test_budget_equal_to_limit_is_included(self):
        """默认场景 budget 1200 完整输出只有一行 P001：等于上限也能入选。"""
        db = self.tmpdir / "fresh.sqlite3"
        self.assert_budget(db, 1200, ("P001 虚拟笔记本 1200",))
        # 首次使用按原规则初始化了两件演示商品
        self.assertEqual(self.snapshot(db)[0], list(INITIAL_PRODUCTS))

    def test_budget_below_cheapest_product_is_empty_with_zero_bytes(self):
        """上限低于最低单价时标准输出为空（连换行也没有），退出码仍为 0。"""
        db = self.tmpdir / "fresh.sqlite3"
        result = self.run_shop(["budget", "1199"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")

    def test_budget_zero_on_fresh_db_is_empty(self):
        """两件演示商品单价都为正：budget 0 无任何输出。"""
        db = self.tmpdir / "fresh.sqlite3"
        self.assert_budget(db, 0, ())

    def test_budget_at_each_boundary_lists_products_in_id_order(self):
        """各边界点：2500 时两件都入选且按编号升序；1200 只有 P001。"""
        db = self.tmpdir / "fresh.sqlite3"
        self.assert_budget(
            db, 2500,
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 2500"),
        )
        self.assert_budget(db, 2499, ("P001 虚拟笔记本 1200",))
        self.assert_budget(db, MAX_PRICE, (
            "P001 虚拟笔记本 1200", "P002 虚拟马克杯 2500"))

    def test_budget_accepts_leading_zeros(self):
        """前导零不影响数值：01200 与 1200 同结果，000 即零。"""
        db = self.tmpdir / "fresh.sqlite3"
        self.assert_budget(db, "01200", ("P001 虚拟笔记本 1200",))
        self.assert_budget(db, "00000", ())

    def test_budget_output_uses_single_spaces_and_trailing_newline(self):
        """字段之间恰好一个空格，每个输出行都以换行结束。"""
        db = self.tmpdir / "fresh.sqlite3"
        result = self.run_shop(["budget", "2500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n",
        )
        for line in result.stdout.splitlines():
            parts = line.split(" ")
            self.assertEqual(len(parts), 3)
            self.assertNotIn("  ", line)
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertNotIn("\n\n", result.stdout)

    # ---- 使用数据库中保存的名称与单价 -----------------------------------

    def test_budget_uses_saved_prices_after_price_command(self):
        """price 改价后按保存的价格筛选；重启后仍生效，商品不被删除。"""
        db = self.tmpdir / "shop.sqlite3"
        result = self.run_shop(["price", "P002", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 单价 0\n")

        # budget 0 完整输出只有 P002 一行（P001 单价 1200 不入选）
        self.assert_budget(db, 0, ("P002 虚拟马克杯 0",))
        # 零单价商品仍保留在目录中
        catalog = self.run_shop(["catalog"], db=db)
        self.assertEqual(
            catalog.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 0\n",
        )

        # 再把 P001 也改成零：budget 0 两行按编号升序
        self.assertEqual(self.run_shop(["price", "P001", "000"], db=db).returncode, 0)
        self.assert_budget(
            db, 0,
            ("P001 虚拟笔记本 0", "P002 虚拟马克杯 0"),
        )

    def test_budget_uses_saved_names_and_prices_from_existing_db(self):
        """对已有数据库不恢复演示价格：直接改库后的名称和价格都参与筛选。"""
        db = self.tmpdir / "renamed.sqlite3"
        # 先由 shop.py 正常建库初始化
        self.assertEqual(self.run_shop(["catalog"], db=db).returncode, 0)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE products SET name = ?, price = ? WHERE id = ?",
                ("演示笔记本", 800, "P001"),
            )
            conn.commit()

        self.assert_budget(
            db, 800,
            ("P001 演示笔记本 800",),
        )
        self.assert_budget(db, 799, ())
        self.assert_budget(
            db, 2500,
            ("P001 演示笔记本 800", "P002 虚拟马克杯 2500"),
        )

    def test_budget_results_are_independent_between_databases(self):
        """不同数据库的改价互不影响：A 库 P002 为零，B 库仍是演示价格。"""
        db_a = self.tmpdir / "a.sqlite3"
        db_b = self.tmpdir / "b.sqlite3"
        self.assertEqual(self.run_shop(["price", "P002", "0"], db=db_a).returncode, 0)

        self.assert_budget(db_a, 0, ("P002 虚拟马克杯 0",))
        self.assert_budget(db_b, 0, ())
        self.assert_budget(db_b, 1200, ("P001 虚拟笔记本 1200",))

    def test_budget_supports_equals_form_db_option(self):
        """--db=路径 的现有传参方式对 budget 同样有效。"""
        db = self.tmpdir / "eq.sqlite3"
        result = self._run_with_raw_args(["--db=" + str(db), "budget", "1200"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 虚拟笔记本 1200\n")

    def _run_with_raw_args(self, raw_args, cwd=None):
        """直接以完整参数列表运行，用于 --db=路径 等需要原样拼接的场景。"""
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, str(SHOP)] + raw_args,
            cwd=str(cwd if cwd is not None else self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    # ---- 与购物车、满减无关且纯只读 -------------------------------------

    def test_budget_ignores_cart_quantities_and_discount(self):
        """筛选只看单件单价：购物车数量与 preview 满减都不影响结果。"""
        db = self.tmpdir / "cart.sqlite3"
        for product_id, quantity in (("P001", "10"), ("P002", "3")):
            self.assertEqual(
                self.run_shop(["add", product_id, quantity], db=db).returncode, 0
            )
        before = self.snapshot(db)
        # 购物车总金额 19500 已超过满减门槛，与单件预算无关：
        # 1200 的预算仍然只显示 P001
        self.assert_budget(db, 1200, ("P001 虚拟笔记本 1200",))
        self.assertEqual(self.snapshot(db), before)

    def test_budget_is_read_only(self):
        """budget 不修改商品资料和购物车，重复运行结果一致。"""
        db = self.tmpdir / "ro.sqlite3"
        self.run_shop(["add", "P002", "2"], db=db)
        before = self.snapshot(db)
        for limit in ("0", "1200", "2500", str(MAX_PRICE)):
            self.run_shop(["budget", limit], db=db)
        self.assertEqual(self.snapshot(db), before)

    def test_budget_does_not_persist_budget_condition(self):
        """预算条件不落库：先用极小预算空查，再用大预算仍能看到全部商品。"""
        db = self.tmpdir / "cond.sqlite3"
        self.assert_budget(db, 0, ())
        self.assert_budget(
            db, MAX_PRICE,
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 2500"),
        )

    def test_budget_with_default_db_in_working_directory(self):
        """不传 --db 时使用工作目录下的 shop.sqlite3。"""
        result = self.run_shop(["budget", "1200"], db=None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 虚拟笔记本 1200\n")
        self.assertTrue((self.tmpdir / "shop.sqlite3").exists())

    # ---- 数字格式错误 ---------------------------------------------------

    def test_budget_rejects_non_digit_formats(self):
        """空串、空白、正负号、小数、全角数字、混入字符都报格式错误。"""
        bad_args = [
            "", " ", "  ", "12 ", " 12", "1 2",
            "-1", "+1", "+0", "-0",
            "1.5", ".5", "0.", "1e3", "0x10",
            "１２３", "０", "1２３", "１2",
            "abc", "12分", "１２", "nine",
        ]
        for bad in bad_args:
            with self.subTest(bad=bad):
                self.assert_rejected_before_open(
                    ["budget", bad], 2, "价格上限必须为非负整数"
                )

    # ---- 数值范围错误 ---------------------------------------------------

    def test_budget_rejects_value_above_max(self):
        """上界 +1 报“价格上限超出范围”，且不打开数据库。"""
        self.assert_rejected_before_open(
            ["budget", str(MAX_PRICE + 1)], 2, "价格上限超出范围"
        )

    def test_budget_accepts_exact_max_value(self):
        """恰好等于上界合法。"""
        db = self.tmpdir / "max.sqlite3"
        self.assert_budget(
            db, str(MAX_PRICE),
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 2500"),
        )

    def test_budget_rejects_superlong_digit_strings(self):
        """超长纯数字串按范围错误处理，不出现数字转换异常堆栈。"""
        for length in (20, 30, 100, 5000):
            with self.subTest(length=length):
                self.assert_rejected_before_open(
                    ["budget", "9" * length], 2, "价格上限超出范围"
                )

    def test_budget_format_error_takes_precedence_over_range(self):
        """带负号的超长串是格式错误而非范围错误。"""
        self.assert_rejected_before_open(
            ["budget", "-" + "9" * 5000], 2, "价格上限必须为非负整数"
        )

    # ---- 调用结构错误 ---------------------------------------------------

    def test_budget_requires_exactly_one_argument(self):
        """缺少上限或带额外参数都报“参数错误”，不接受关键词和排序参数。"""
        bad_argv = [
            ["budget"],
            ["budget", "100", "200"],
            ["budget", "100", "笔记"],
            ["budget", "100", "--sort", "price"],
            ["budget", "--sort", "price"],
            ["budget", "100", "extra", "extra2"],
        ]
        for argv in bad_argv:
            with self.subTest(argv=argv):
                self.assert_rejected_before_open(argv, 2, "参数错误")

    def test_structure_validation_precedes_format_validation(self):
        """结构错误优先：两个参数即使都不是数字也只报参数错误。"""
        self.assert_rejected_before_open(
            ["budget", "abc", "def"], 2, "参数错误"
        )
        # 单个非数字参数则进入格式校验
        self.assert_rejected_before_open(
            ["budget", "abc"], 2, "价格上限必须为非负整数"
        )

    def test_unknown_command_still_parameter_error(self):
        """budget 的加入不影响未知子命令判定。"""
        self.assert_rejected_before_open(
            ["budgets", "100"], 2, "参数错误"
        )


if __name__ == "__main__":
    unittest.main()
