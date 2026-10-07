#!/usr/bin/env python3
"""shop.py price 命令“修改商品单价”语义的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

所有用例均在独立临时目录中运行：显式传入的数据库文件位于该目录，
未传 --db 时也把子进程的工作目录切到该目录，因此不会接触
项目或用户已有的 shop.sqlite3。每个用例各自新建临时目录与数据库，
重复执行结果一致。
"""

import os
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

# 样例购物车在改价前的 show 输出（P001 单价仍为目录默认的 1200）
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)
SAMPLE_CATALOG = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)

# price P001 001500 后：目录与购物车读取到的单价均为 1500
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

# P001 单价再改为 0 后：商品仍在目录和购物车中，小计为零
ZERO_CATALOG = (
    "P001 虚拟笔记本 0",
    "P002 虚拟马克杯 2500",
)
ZERO_SHOW = (
    "P001 虚拟笔记本 0 2 0",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 2500",
)

EMPTY_CART_SHOW = ("总数量 0", "总金额 0")

# price 不接受的单价：空字符串、纯空白、正负号、小数、全角数字、
# 混入其他字符（含首尾空白与下划线）
INVALID_PRICES = (
    "",        # 空字符串
    "   ",     # 纯空白（空格）
    "\t",      # 纯空白（制表符）
    "-1",      # 负号
    "+1",      # 正号
    "1.5",     # 小数
    ".5",      # 小数点开头
    "１",      # 单个全角数字
    "１５００",  # 全角数字串
    "12a",     # 混入字母
    "12元",    # 混入中文
    "1_000",   # 下划线分隔
    " 1500",   # 前导空白
    "1500 ",   # 尾部空白
)

# 超长单价：超过 Python 3.11 默认的数字转换位数限制（4300 位）
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
        """另起进程调用 catalog，核对持久化后的目录输出。"""
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

    def test_price_fixed_sample_output_exact_and_persists(self):
        """固定验收样例：price P001 001500 输出精确成功行，重启后目录与购物车读新价。"""
        db = self.seed_sample()

        result = self.run_shop(["price", "P001", "001500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 1500\n")
        self.assertEqual(result.stderr, "")

        # 重新启动查看目录：P001 单价为 1500，P002 单价仍为 2500，
        # 商品编号与名称不变
        self.assert_catalog(db, PRICED_CATALOG)

        # 重新启动查看购物车：P001 小计 3000，总数量 3、总金额 5500，
        # 编号、名称、数量均不变；P002 单价仍为 2500
        self.assert_show(db, PRICED_SHOW)

        # 再次重启仍是同一结果：价格确实落盘保留
        self.assert_catalog(db, PRICED_CATALOG)
        self.assert_show(db, PRICED_SHOW)

    def test_price_zero_keeps_product_in_catalog_and_cart(self):
        """单价设为 0：成功输出按 0 显示，商品仍在目录和购物车，小计为零。"""
        db = self.seed_sample()

        result = self.run_shop(["price", "P001", "001500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["price", "P001", "000"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 0\n")
        self.assertEqual(result.stderr, "")

        # 目录中仍有 P001（单价显示为 0）
        self.assert_catalog(db, ZERO_CATALOG)
        # show 与 cart 表做 JOIN：P001 仍出现在明细中说明购物车条目仍在，
        # 其小计为 0；P002 小计 2500，总数量仍为 3、总金额为 2500
        self.assert_show(db, ZERO_SHOW)

    def test_price_same_value_succeeds(self):
        """同值改价也成功：等于默认价、重复设置新价、重复设置零均照常输出成功。"""
        db = self.seed_sample()

        # 设成与目录默认价相同的值
        result = self.run_shop(["price", "P001", "01200"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 1200\n")
        self.assertEqual(result.stderr, "")
        self.assert_show(db, SAMPLE_SHOW)

        # 两次改成同一个新值，第二次（同值改价）也成功
        for _ in range(2):
            result = self.run_shop(["price", "P001", "001500"], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "P001 单价 1500\n")
            self.assertEqual(result.stderr, "")
        self.assert_show(db, PRICED_SHOW)

        # 同值改零同样成功
        for _ in range(2):
            result = self.run_shop(["price", "P001", "000"], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "P001 单价 0\n")
        self.assert_show(db, ZERO_SHOW)

    def test_price_without_cart_entry_does_not_create_one(self):
        """未入车商品改价成功但不产生购物车条目；新价对之后加入的商品生效。"""
        db = self.tmpdir / "cart.sqlite3"

        # 购物车只有 P002 一件；给不在购物车的 P001 改价
        result = self.run_shop(["add", "P002", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["price", "P001", "1500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 1500\n")
        self.assertEqual(result.stderr, "")

        # 明细中没有 P001：改价没有创建购物车条目；总数仍为 1
        self.assert_show(
            db,
            ("P002 虚拟马克杯 2500 1 2500", "总数量 1", "总金额 2500"),
        )
        # 目录中 P001 已是新价
        self.assert_catalog(db, PRICED_CATALOG)

        # 全新空库：购物车为空时改价也不创建条目
        fresh = self.tmpdir / "fresh.sqlite3"
        result = self.run_shop(["price", "P002", "3000"], db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 单价 3000\n")
        self.assert_show(fresh, EMPTY_CART_SHOW)
        self.assert_catalog(
            fresh,
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 3000"),
        )

        # 之后再加入 P002：按保存后的 3000 计价，证明价格已落盘
        result = self.run_shop(["add", "P002", "1"], db=fresh)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_show(
            fresh,
            ("P002 虚拟马克杯 3000 1 3000", "总数量 1", "总金额 3000"),
        )

    def test_price_default_db_in_temp_cwd(self):
        """省略 --db：使用临时工作目录下的 shop.sqlite3，改价同样持久。"""
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

        result = self.run_shop(["catalog"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "\n".join(PRICED_CATALOG) + "\n")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(PRICED_SHOW) + "\n")

        # 默认库上设零同样成立
        result = self.run_shop(["price", "P001", "000"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 单价 0\n")
        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(ZERO_SHOW) + "\n")

    def test_explicit_db_files_have_independent_prices(self):
        """两个独立数据库：一个库改价不影响另一个库的目录价格与购物车金额。"""
        db_a = self.seed_sample(db=self.tmpdir / "a.sqlite3")
        db_b = self.seed_sample(db=self.tmpdir / "b.sqlite3")

        result = self.run_shop(["price", "P001", "1500"], db=db_a)
        self.assertEqual(result.returncode, 0, result.stderr)

        # 库 a 用新价
        self.assert_show(db_a, PRICED_SHOW)
        self.assert_catalog(db_a, PRICED_CATALOG)
        # 库 b 仍是默认价
        self.assert_show(db_b, SAMPLE_SHOW)
        self.assert_catalog(db_b, SAMPLE_CATALOG)

        # 在库 b 改成另一个价格：库 a 不受影响
        result = self.run_shop(["price", "P001", "999"], db=db_b)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 999\n")
        self.assert_show(
            db_b,
            (
                "P001 虚拟笔记本 999 2 1998",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 3",
                "总金额 4498",
            ),
        )
        self.assert_show(db_a, PRICED_SHOW)
        self.assert_catalog(db_a, PRICED_CATALOG)

    def test_price_rejects_invalid_formats_and_preserves_state(self):
        """各类非非负整数单价统一报格式错误，每次失败后商品资料与购物车不变。"""
        db = self.seed_sample()
        # 先建立非默认价格基线：失败后价格必须仍是 1500
        result = self.run_shop(["price", "P001", "001500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        for price_text in INVALID_PRICES:
            with self.subTest(price_text=price_text):
                result = self.run_shop(["price", "P001", price_text], db=db)
                self.assert_failure(result, 2, "单价必须为非负整数")
                # 失败后重新查看可用数据库：商品资料和购物车与操作前完全一致
                self.assert_catalog(db, PRICED_CATALOG)
                self.assert_show(db, PRICED_SHOW)

    def test_price_at_max_succeeds_and_persists(self):
        """单价恰为上界 9223372036854775807 时成功，重启后仍为该值。"""
        db = self.seed_sample()

        result = self.run_shop(
            ["price", "P001", str(MAX_PRICE)], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"P001 单价 {MAX_PRICE}\n")
        self.assertEqual(result.stderr, "")

        max_subtotal = 2 * MAX_PRICE
        self.assert_catalog(
            db,
            (f"P001 虚拟笔记本 {MAX_PRICE}", "P002 虚拟马克杯 2500"),
        )
        self.assert_show(
            db,
            (
                f"P001 虚拟笔记本 {MAX_PRICE} 2 {max_subtotal}",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 3",
                f"总金额 {max_subtotal + 2500}",
            ),
        )

    def test_price_over_max_and_overlong_are_range_errors(self):
        """超过上界与五千个 9 均报单价超出范围，无堆栈且状态不变。"""
        db = self.seed_sample()
        result = self.run_shop(["price", "P001", "001500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        for price_text in (
            str(OVER_MAX),
            "000" + str(OVER_MAX),  # 前导零不改变越界判定
            LONG_NINES,             # 五千位纯数字：有确定结果，不触发转换异常
        ):
            with self.subTest(price_text=price_text):
                result = self.run_shop(["price", "P001", price_text], db=db)
                self.assert_failure(result, 2, "单价超出范围")
                self.assert_catalog(db, PRICED_CATALOG)
                self.assert_show(db, PRICED_SHOW)

    def test_price_unknown_product(self):
        """有效单价配未知编号 P999：报未知商品，商品资料与购物车不变。"""
        db = self.seed_sample()
        result = self.run_shop(["price", "P001", "001500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        for price_text in ("100", "000", str(MAX_PRICE)):
            with self.subTest(price_text=price_text):
                result = self.run_shop(["price", "P999", price_text], db=db)
                self.assert_failure(result, 2, "未知商品")
                self.assert_catalog(db, PRICED_CATALOG)
                self.assert_show(db, PRICED_SHOW)

        # 全新空库同样报未知商品（目录只有 P001/P002）
        fresh = self.tmpdir / "fresh.sqlite3"
        result = self.run_shop(["price", "P999", "100"], db=fresh)
        self.assert_failure(result, 2, "未知商品")
        self.assert_show(fresh, EMPTY_CART_SHOW)

    def test_price_error_priority_format_range_then_product(self):
        """编号与单价同时无效时，按格式 → 范围 → 编号的顺序报告唯一错误。"""
        db = self.seed_sample()
        result = self.run_shop(["price", "P001", "001500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        for price_text, message in (
            ("abc", "单价必须为非负整数"),
            ("", "单价必须为非负整数"),
            (LONG_WITH_LETTER, "单价必须为非负整数"),
            (str(OVER_MAX), "单价超出范围"),
            (LONG_NINES, "单价超出范围"),
            ("100", "未知商品"),
        ):
            with self.subTest(price_text=price_text):
                result = self.run_shop(["price", "P999", price_text], db=db)
                self.assert_failure(result, 2, message)
                # 任何一类失败后库状态都与操作前完全一致
                self.assert_catalog(db, PRICED_CATALOG)
                self.assert_show(db, PRICED_SHOW)

    def test_price_missing_or_extra_args_is_argument_error(self):
        """缺少或多余参数报参数错误；全新路径上不创建数据库文件。"""
        db = self.seed_sample()

        result = self.run_shop(["price"], db=db)
        self.assert_failure(result, 2, "参数错误")

        result = self.run_shop(["price", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, SAMPLE_SHOW)

        fresh = self.tmpdir / "never.sqlite3"
        self.assertFalse(fresh.exists())
        result = self.run_shop(["price", "P001", "1", "2"], db=fresh)
        self.assert_failure(result, 2, "参数错误")
        self.assertFalse(fresh.exists())

    def test_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：不输出成功信息，只报数据库不可用并以退出码 1 结束。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["price", "P001", "1500"], db=directory)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
