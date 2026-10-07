#!/usr/bin/env python3
"""shop.py budget 命令“按单件预算浏览商品”语义的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

所有用例均在独立临时目录中运行：显式传入的数据库文件位于该目录，
未传 --db 时也把子进程的工作目录切到该目录，因此不会接触
项目或用户已有的 shop.sqlite3。每次 run_shop 都是全新进程，
随后的 budget/catalog/show 读取即等价于“重启后”核对持久化结果。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 价格上限上界：SQLite INTEGER 的最大值
MAX_PRICE = 9223372036854775807
OVER_MAX = MAX_PRICE + 1

# 未改过价格时的初始目录
INITIAL_CATALOG = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)

# budget 不接受的价格上限：空字符串、纯空白、正负号、小数、全角数字、
# 混入其他字符、首尾空白、科学计数法、千分位等
INVALID_LIMITS = (
    "",
    " ",
    "   ",
    "\t",
    " \t ",
    "+1",
    "-1",
    "+0",
    "1.5",
    ".5",
    "0.0",
    "１",
    "１２",
    "1a",
    " 1",
    "1 ",
    "1e3",
    "0x1",
    "1,000",
)

# 超长上限：超过 Python 3.11 默认的数字转换位数限制（4300 位）。
LONG_NINES = "9" * 5000
# 超长数字中夹入一个字母：整体仍是格式错误（且优先于范围错误）
LONG_WITH_LETTER = "9" * 2500 + "a" + "9" * 2499


class ShopBudgetTests(unittest.TestCase):
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

    def assert_budget(self, db, limit, expected_lines, cwd=None):
        """另起进程调用 budget，核对完整输出（含空输出的情形）。"""
        result = self.run_shop(["budget", limit], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        expected = "\n".join(expected_lines) + "\n" if expected_lines else ""
        self.assertEqual(result.stdout, expected)

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def snapshot(self, db):
        """重新打开数据库读取商品资料与购物车，用于失败前后逐字节对比。"""
        conn = sqlite3.connect(str(db))
        try:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        finally:
            conn.close()
        return products, cart

    def test_budget_at_limit_includes_equal_price(self):
        """固定验收样例：budget 1200 只输出 P001 一行，等于上限也入选。"""
        db = self.tmpdir / "cart.sqlite3"

        result = self.run_shop(["budget", "1200"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "P001 虚拟笔记本 1200\n")

        # 首次使用数据库时已按现有规则初始化两个演示商品
        self.assertEqual(
            self.snapshot(db),
            ([("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)], []),
        )

    def test_budget_zero_price_product_stays_in_catalog(self):
        """P002 单价设为 0 后 budget 0 只输出它；商品仍保留在目录中。"""
        db = self.tmpdir / "cart.sqlite3"

        result = self.run_shop(["price", "P002", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["budget", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "P002 虚拟马克杯 0\n")

        # 目录中两件商品都在，单价为保存后的值
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout, "P001 虚拟笔记本 1200\nP002 虚拟马克杯 0\n"
        )

    def test_budget_uses_saved_prices_across_restarts(self):
        """预算浏览始终按库中保存的单价筛选：重启后不恢复内置演示价格。"""
        db = self.tmpdir / "cart.sqlite3"

        result = self.run_shop(["price", "P001", "3000"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(["price", "P002", "800"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        # 全新进程（重启）按保存后的价格筛选：P001 3000 超出，P002 800 入选
        self.assert_budget(db, "1200", ["P002 虚拟马克杯 800"])
        # 再重启一次结果一致，不会回到演示价格 1200/2500
        self.assert_budget(db, "1200", ["P002 虚拟马克杯 800"])
        # 按编号升序输出，与单价高低无关
        self.assert_budget(db, "3000", [
            "P001 虚拟笔记本 3000",
            "P002 虚拟马克杯 800",
        ])

    def test_budget_all_and_none(self):
        """上限覆盖全部商品时按编号升序输出两行；过低时标准输出为空。"""
        db = self.tmpdir / "cart.sqlite3"

        self.assert_budget(db, "2500", list(INITIAL_CATALOG))
        self.assert_budget(db, "9999", list(INITIAL_CATALOG))
        # 没有符合条件的商品：退出码 0、标准输出与标准错误均为空
        self.assert_budget(db, "1199", [])
        self.assert_budget(db, "0", [])

    def test_budget_ignores_cart_and_discount(self):
        """预算比较只针对单件单价：购物车数量与 preview 满减均不影响。"""
        db = self.tmpdir / "cart.sqlite3"

        for args in (["add", "P001", "3"], ["add", "P002", "1"]):
            result = self.run_shop(args, db=db)
            self.assertEqual(result.returncode, 0, result.stderr)

        # 购物车小计 3600 超过上限，但 P001 单价 1200 仍入选
        self.assert_budget(db, "1200", ["P001 虚拟笔记本 1200"])
        # preview 满减后应付 4100，但 budget 2500 仍按原始单价输出两件
        self.assert_budget(db, "2500", list(INITIAL_CATALOG))
        # 购物车内容不受 budget 影响
        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200 3 3600\n"
            "P002 虚拟马克杯 2500 1 2500\n"
            "总数量 4\n总金额 6100\n",
        )

    def test_budget_leading_zeros_and_max(self):
        """前导零与零按数值解释；恰为上界时全部商品入选。"""
        db = self.tmpdir / "cart.sqlite3"

        self.assert_budget(db, "001200", ["P001 虚拟笔记本 1200"])
        self.assert_budget(db, "000", [])
        self.assert_budget(db, str(MAX_PRICE), list(INITIAL_CATALOG))
        self.assert_budget(db, "0" + str(MAX_PRICE), list(INITIAL_CATALOG))

    def test_budget_does_not_save_condition_or_touch_state(self):
        """budget 是纯只读：不保存预算条件，商品资料与购物车均不变。"""
        db = self.tmpdir / "cart.sqlite3"
        result = self.run_shop(["add", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.snapshot(db)

        self.assert_budget(db, "1200", ["P001 虚拟笔记本 1200"])
        self.assert_budget(db, "0", [])
        self.assertEqual(self.snapshot(db), before)

    def test_explicit_db_files_are_independent(self):
        """两个独立数据库：一个库的改价不影响另一个库的预算筛选结果。"""
        db_a = self.tmpdir / "a.sqlite3"
        db_b = self.tmpdir / "b.sqlite3"

        result = self.run_shop(["price", "P001", "500"], db=db_a)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_budget(db_a, "600", ["P001 虚拟笔记本 500"])
        self.assert_budget(db_b, "600", [])
        self.assert_budget(db_b, "1200", ["P001 虚拟笔记本 1200"])

    def test_default_db_in_temp_cwd_supports_budget(self):
        """不传 --db：使用临时工作目录下的 shop.sqlite3，预算筛选同样生效。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        result = self.run_shop(["budget", "1200"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 虚拟笔记本 1200\n")
        self.assertEqual(result.stderr, "")
        self.assertTrue(default_db.is_file())

    def test_budget_rejects_invalid_formats_before_opening_db(self):
        """各类非非负整数上限均被拒绝；全新路径上不创建数据库文件。"""
        db = self.tmpdir / "cart.sqlite3"
        result = self.run_shop(["price", "P001", "900"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.snapshot(db)

        for limit in INVALID_LIMITS:
            with self.subTest(limit=limit):
                result = self.run_shop(["budget", limit], db=db)
                self.assert_failure(result, 2, "价格上限必须为非负整数")
                self.assertEqual(self.snapshot(db), before)

        # 格式错误在打开数据库前拒绝：全新路径上不留下数据库文件
        fresh = self.tmpdir / "never.sqlite3"
        self.assertFalse(fresh.exists())
        for limit in ("", "  ", "-1", "1.5", "１２", "1a"):
            with self.subTest(limit=limit):
                result = self.run_shop(["budget", limit], db=fresh)
                self.assert_failure(result, 2, "价格上限必须为非负整数")
                self.assertFalse(fresh.exists())

    def test_budget_over_max_is_range_error_before_opening_db(self):
        """合法数字超过上界（含超长数字）报范围错误；全新路径上不建库。"""
        fresh = self.tmpdir / "never.sqlite3"
        self.assertFalse(fresh.exists())

        for limit in (str(OVER_MAX), "000" + str(OVER_MAX), LONG_NINES):
            with self.subTest(limit=limit[:30]):
                result = self.run_shop(["budget", limit], db=fresh)
                self.assert_failure(result, 2, "价格上限超出范围")
                self.assertFalse(fresh.exists())

    def test_budget_error_priority_arity_format_range(self):
        """调用结构先于数值格式，格式先于范围：每次只报告唯一错误。"""
        fresh = self.tmpdir / "never.sqlite3"

        # 缺少上限或带额外参数（含排序形式）：参数错误
        for args in (
            ["budget"],
            ["budget", "1200", "extra"],
            ["budget", "--sort", "price"],
            ["budget", "1200", "--sort", "price"],
        ):
            with self.subTest(args=args):
                result = self.run_shop(args, db=fresh)
                self.assert_failure(result, 2, "参数错误")
                self.assertFalse(fresh.exists())

        # 单个非数字参数（含关键词形式）：结构合法，按格式错误拒绝
        result = self.run_shop(["budget", "笔记本"], db=fresh)
        self.assert_failure(result, 2, "价格上限必须为非负整数")
        self.assertFalse(fresh.exists())

        # 带字母的超长数字：格式错误优先于范围错误
        result = self.run_shop(["budget", LONG_WITH_LETTER], db=fresh)
        self.assert_failure(result, 2, "价格上限必须为非负整数")
        self.assertFalse(fresh.exists())

    def test_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：只报数据库不可用并退出 1，标准输出为空。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["budget", "1200"], db=directory)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_state_after_all_failures_matches_before(self):
        """汇总：一连串失败后商品资料与购物车与操作前完全一致。"""
        db = self.tmpdir / "cart.sqlite3"
        result = self.run_shop(["add", "P002", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.snapshot(db)

        for args, code, message in (
            (["budget", ""], 2, "价格上限必须为非负整数"),
            (["budget", "  "], 2, "价格上限必须为非负整数"),
            (["budget", "-1"], 2, "价格上限必须为非负整数"),
            (["budget", "1.5"], 2, "价格上限必须为非负整数"),
            (["budget", "１"], 2, "价格上限必须为非负整数"),
            (["budget", "1a"], 2, "价格上限必须为非负整数"),
            (["budget", str(OVER_MAX)], 2, "价格上限超出范围"),
            (["budget", LONG_NINES], 2, "价格上限超出范围"),
            (["budget"], 2, "参数错误"),
            (["budget", "1", "2"], 2, "参数错误"),
        ):
            with self.subTest(args=args):
                result = self.run_shop(args, db=db)
                self.assert_failure(result, code, message)
                self.assertEqual(self.snapshot(db), before)

        # 合法调用也不改变状态
        self.assert_budget(db, "2500", list(INITIAL_CATALOG))
        self.assertEqual(self.snapshot(db), before)


if __name__ == "__main__":
    unittest.main()
