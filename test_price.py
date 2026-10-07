#!/usr/bin/env python3
"""shop.py price 命令“修改目录商品单价”语义的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

所有用例均在独立临时目录中运行：显式传入的数据库文件位于该目录，
未传 --db 时也把子进程的工作目录切到该目录，因此不会接触
项目或用户已有的 shop.sqlite3。每次 run_shop 都是全新进程，
随后的 catalog/show 读取即等价于“重启后”核对持久化结果。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 单价上界：SQLite INTEGER 的最大值
MAX_PRICE = 9223372036854775807
OVER_MAX = MAX_PRICE + 1

# 固定样例：两件 P001、一件 P002
SAMPLE = (("P001", "2"), ("P002", "1"))

# 未改过价格时的样例目录与购物车
INITIAL_CATALOG = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)

# price P001 001500 后的目录与购物车：P001 单价 1500、小计 3000，
# P002 单价仍为 2500；编号、名称、数量均不变
PRICED_CATALOG = (
    "P001 虚拟笔记本 1500",
    "P002 虚拟马克杯 2500",
)
PRICED_SHOW = (
    "P001 虚拟笔记本 1500 2 3000",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 5500",
)

# price P001 000 后：商品仍在目录和购物车中，P001 小计为零
ZERO_PRICE_CATALOG = (
    "P001 虚拟笔记本 0",
    "P002 虚拟马克杯 2500",
)
ZERO_PRICE_SHOW = (
    "P001 虚拟笔记本 0 2 0",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 2500",
)

# price 不接受的单价：空字符串、纯空白、正负号、小数、全角数字、
# 混入其他字符、首尾空白、科学计数法、千分位等
INVALID_PRICES = (
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

# 超长单价：超过 Python 3.11 默认的数字转换位数限制（4300 位）。
LONG_NINES = "9" * 5000
# 超长数字中夹入一个字母：整体仍是格式错误（且优先于范围与编号错误）
LONG_WITH_LETTER = "9" * 2500 + "a" + "9" * 2499


class ShopPriceTests(unittest.TestCase):
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

    def seed_sample(self, db=..., cwd=None):
        """用公开的 add 语义准备固定样例购物车（P001 两件、P002 一件）。"""
        for product_id, quantity in SAMPLE:
            result = self.run_shop(
                ["add", product_id, quantity], db=db, cwd=cwd
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return self.tmpdir / "cart.sqlite3" if db is ... else db

    def assert_show(self, db, expected_lines, cwd=None):
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_catalog(self, db, expected_lines, cwd=None):
        """另起进程调用 catalog，核对持久化后的完整目录输出。"""
        result = self.run_shop(["catalog"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

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

    def assert_cart_product_ids(self, db, expected_ids):
        """直接读库核对购物车条目编号（含“不产生条目”的空列表情形）。"""
        conn = sqlite3.connect(str(db))
        try:
            cart = conn.execute(
                "SELECT product_id FROM cart ORDER BY product_id"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in cart], expected_ids)

    def test_price_fixed_sample_to_1500_then_zero(self):
        """固定验收样例：001500 按 1500 显示并落盘；再设 000 后小计为零。"""
        db = self.seed_sample()

        result = self.run_shop(["price", "P001", "001500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 1500\n")
        self.assertEqual(result.stderr, "")

        # 全新进程（重启）查看目录与购物车：单价、小计、总数、总额正确，
        # 商品编号、名称与数量均未变化
        self.assert_catalog(db, PRICED_CATALOG)
        self.assert_show(db, PRICED_SHOW)

        result = self.run_shop(["price", "P001", "000"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 0\n")
        self.assertEqual(result.stderr, "")

        # 单价为零时商品仍在目录和购物车中，P001 小计为零，总额只剩 P002
        self.assert_catalog(db, ZERO_PRICE_CATALOG)
        self.assert_show(db, ZERO_PRICE_SHOW)

    def test_price_persists_across_restarts(self):
        """价格在重启后保留：连续多个全新进程读到的都是保存后的单价。"""
        db = self.seed_sample()

        result = self.run_shop(["price", "P001", "001500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_catalog(db, PRICED_CATALOG)
        self.assert_show(db, PRICED_SHOW)
        # 再重启一次仍然保留，且不会自行回到目录初始价 1200
        self.assert_catalog(db, PRICED_CATALOG)
        self.assert_show(db, PRICED_SHOW)

    def test_price_same_value_succeeds(self):
        """同值改价也成功：重复设为同一单价输出一致，状态不变。"""
        db = self.seed_sample()

        for price_text in ("1500", "001500"):
            result = self.run_shop(["price", "P001", price_text], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "P001 单价 1500\n")
            self.assertEqual(result.stderr, "")
        self.assert_show(db, PRICED_SHOW)

        # 单价为零时再设零同样成功，商品仍留在目录与购物车
        result = self.run_shop(["price", "P001", "000"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(["price", "P001", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 0\n")
        self.assert_catalog(db, ZERO_PRICE_CATALOG)
        self.assert_show(db, ZERO_PRICE_SHOW)

    def test_price_without_cart_does_not_create_cart_entry(self):
        """未入车商品改价成功但不产生购物车条目，show 仍为空车。"""
        db = self.tmpdir / "fresh.sqlite3"

        result = self.run_shop(["price", "P001", "900"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 900\n")
        self.assertEqual(result.stderr, "")

        # 目录价格已更新，购物车没有任何记录
        self.assert_catalog(
            db,
            ("P001 虚拟笔记本 900", "P002 虚拟马克杯 2500"),
        )
        self.assert_show(db, ("总数量 0", "总金额 0"))
        self.assert_cart_product_ids(db, [])

        # 购物车中已有 P002 时，给未入车的 P001 改价也不新增条目
        result = self.run_shop(["add", "P002", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(["price", "P001", "800"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_cart_product_ids(db, ["P002"])
        self.assert_show(
            db,
            ("P002 虚拟马克杯 2500 1 2500", "总数量 1", "总金额 2500"),
        )

    def test_price_only_changes_target_price(self):
        """改价只影响指定商品单价：另一商品单价与两车数量均不变。"""
        db = self.seed_sample()

        result = self.run_shop(["price", "P002", "3300"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 单价 3300\n")

        self.assert_catalog(
            db,
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 3300"),
        )
        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 2 2400",
                "P002 虚拟马克杯 3300 1 3300",
                "总数量 3",
                "总金额 5700",
            ),
        )

    def test_default_db_in_temp_cwd_supports_price(self):
        """不传 --db：使用临时工作目录下的 shop.sqlite3，改价同样生效且持久。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        self.seed_sample(db=None, cwd=workdir)
        self.assertTrue(default_db.is_file())

        result = self.run_shop(
            ["price", "P001", "001500"], db=None, cwd=workdir
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 单价 1500\n")
        self.assertEqual(result.stderr, "")

        # 重启后默认库中的价格保留
        self.assert_catalog(None, PRICED_CATALOG, cwd=workdir)
        self.assert_show(None, PRICED_SHOW, cwd=workdir)

    def test_explicit_db_files_have_independent_prices(self):
        """两个独立数据库：一个库的改价不影响另一个库的目录与购物车。"""
        db_a = self.seed_sample()
        db_b = self.tmpdir / "b.sqlite3"

        result = self.run_shop(["add", "P002", "1"], db=db_b)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["price", "P001", "1500"], db=db_a)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(["price", "P001", "1800"], db=db_b)
        self.assertEqual(result.returncode, 0, result.stderr)

        # 两库 P001 单价互不影响
        self.assert_catalog(
            db_a,
            ("P001 虚拟笔记本 1500", "P002 虚拟马克杯 2500"),
        )
        self.assert_catalog(
            db_b,
            ("P001 虚拟笔记本 1800", "P002 虚拟马克杯 2500"),
        )
        self.assert_show(db_a, PRICED_SHOW)
        self.assert_show(
            db_b,
            ("P002 虚拟马克杯 2500 1 2500", "总数量 1", "总金额 2500"),
        )

    def test_price_rejects_invalid_formats_and_preserves_state(self):
        """各类非非负整数单价均被拒绝，每次失败后商品资料与购物车不变。"""
        db = self.seed_sample()
        # 先把 P001 改成 1500，确认失败调用连价格也不会改回或改动
        result = self.run_shop(["price", "P001", "1500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.snapshot(db)

        for price_text in INVALID_PRICES:
            with self.subTest(price=price_text):
                result = self.run_shop(["price", "P001", price_text], db=db)
                self.assert_failure(result, 2, "单价必须为非负整数")
                self.assertEqual(self.snapshot(db), before)
        self.assert_catalog(db, PRICED_CATALOG)
        self.assert_show(db, PRICED_SHOW)

    def test_price_at_max_succeeds_and_persists(self):
        """单价恰为上界时成功，落库后读回仍恰为上界，小计按整数分计算。"""
        db = self.seed_sample()

        result = self.run_shop(
            ["price", "P001", str(MAX_PRICE)], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"P001 单价 {MAX_PRICE}\n")
        self.assertEqual(result.stderr, "")

        # 数据库中保存为精确整数，而非浮点或文本
        products, _ = self.snapshot(db)
        self.assertEqual(products[0], ("P001", "虚拟笔记本", MAX_PRICE))

        self.assert_show(
            db,
            (
                f"P001 虚拟笔记本 {MAX_PRICE} 2 {2 * MAX_PRICE}",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 3",
                f"总金额 {2 * MAX_PRICE + 2500}",
            ),
        )

    def test_price_over_max_is_range_error_and_preserves_state(self):
        """单价超过上界报范围错误且不写入；商品资料与购物车原样保留。"""
        db = self.seed_sample()
        result = self.run_shop(["price", "P001", "1500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.snapshot(db)

        result = self.run_shop(["price", "P001", str(OVER_MAX)], db=db)
        self.assert_failure(result, 2, "单价超出范围")
        self.assertEqual(self.snapshot(db), before)

        # 带前导零的超界值同样按范围错误处理
        result = self.run_shop(
            ["price", "P001", "000" + str(OVER_MAX)], db=db
        )
        self.assert_failure(result, 2, "单价超出范围")
        self.assertEqual(self.snapshot(db), before)

        self.assert_catalog(db, PRICED_CATALOG)
        self.assert_show(db, PRICED_SHOW)

    def test_overlong_price_has_deterministic_result(self):
        """五千个 9 的参数报单价超出范围，不出现异常堆栈，状态保持不变。"""
        db = self.seed_sample()
        before = self.snapshot(db)

        result = self.run_shop(["price", "P001", LONG_NINES], db=db)
        self.assert_failure(result, 2, "单价超出范围")
        self.assertEqual(self.snapshot(db), before)
        self.assert_show(db, SAMPLE_SHOW)

    def test_price_unknown_product_with_valid_price(self):
        """有效单价配未知编号：报未知商品，不新增商品或购物车条目。"""
        db = self.seed_sample()
        before = self.snapshot(db)

        result = self.run_shop(["price", "P999", "100"], db=db)
        self.assert_failure(result, 2, "未知商品")
        self.assertEqual(self.snapshot(db), before)
        self.assert_cart_product_ids(db, ["P001", "P002"])
        self.assert_show(db, SAMPLE_SHOW)

        # 全新空库上同样只报未知商品，购物车保持为空
        fresh = self.tmpdir / "fresh.sqlite3"
        result = self.run_shop(["price", "P999", "100"], db=fresh)
        self.assert_failure(result, 2, "未知商品")
        self.assert_cart_product_ids(fresh, [])

    def test_price_error_priority_format_range_then_product(self):
        """编号和单价同时无效：按格式、范围、编号的顺序报告唯一错误。"""
        db = self.seed_sample()
        before = self.snapshot(db)

        # P999 为未知编号：仍先报告单价侧错误
        for price_text, message in (
            ("abc", "单价必须为非负整数"),
            ("", "单价必须为非负整数"),
            (str(OVER_MAX), "单价超出范围"),
            (LONG_NINES, "单价超出范围"),
            (LONG_WITH_LETTER, "单价必须为非负整数"),
        ):
            with self.subTest(price=price_text):
                result = self.run_shop(["price", "P999", price_text], db=db)
                self.assert_failure(result, 2, message)
                self.assertEqual(self.snapshot(db), before)

        # 单价有效时才轮到编号校验
        result = self.run_shop(["price", "P999", "100"], db=db)
        self.assert_failure(result, 2, "未知商品")
        self.assertEqual(self.snapshot(db), before)
        self.assert_show(db, SAMPLE_SHOW)

    def test_price_without_enough_args_is_argument_error(self):
        """price 缺少编号或单价：报参数错误，商品资料与购物车不变。"""
        db = self.seed_sample()

        result = self.run_shop(["price"], db=db)
        self.assert_failure(result, 2, "参数错误")

        result = self.run_shop(["price", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_catalog(db, INITIAL_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

    def test_price_with_extra_arg_is_argument_error_and_no_db_created(self):
        """price 多带参数：报参数错误；全新路径上不创建数据库文件。"""
        fresh = self.tmpdir / "never.sqlite3"
        self.assertFalse(fresh.exists())

        result = self.run_shop(["price", "P001", "1", "2"], db=fresh)
        self.assert_failure(result, 2, "参数错误")
        self.assertFalse(fresh.exists())

    def test_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：不输出成功信息，只报数据库不可用并退出 1。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["price", "P001", "1500"], db=directory)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_state_after_all_failures_matches_before(self):
        """汇总：一连串失败后重新查看可用数据库，商品资料与购物车与操作前完全一致。"""
        db = self.seed_sample()
        result = self.run_shop(["price", "P001", "1500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.snapshot(db)

        for args, code, message in (
            (["price", "P001", ""], 2, "单价必须为非负整数"),
            (["price", "P001", "  "], 2, "单价必须为非负整数"),
            (["price", "P001", "-1"], 2, "单价必须为非负整数"),
            (["price", "P001", "1.5"], 2, "单价必须为非负整数"),
            (["price", "P001", "１"], 2, "单价必须为非负整数"),
            (["price", "P001", "1a"], 2, "单价必须为非负整数"),
            (["price", "P001", str(OVER_MAX)], 2, "单价超出范围"),
            (["price", "P001", LONG_NINES], 2, "单价超出范围"),
            (["price", "P999", "100"], 2, "未知商品"),
            (["price", "P999", "x"], 2, "单价必须为非负整数"),
            (["price", "P001"], 2, "参数错误"),
            (["price", "P001", "1", "2"], 2, "参数错误"),
        ):
            with self.subTest(args=args):
                result = self.run_shop(args, db=db)
                self.assert_failure(result, code, message)
                self.assertEqual(self.snapshot(db), before)

        self.assert_catalog(db, PRICED_CATALOG)
        self.assert_show(db, PRICED_SHOW)


if __name__ == "__main__":
    unittest.main()
