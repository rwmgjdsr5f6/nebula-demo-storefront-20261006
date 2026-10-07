#!/usr/bin/env python3
"""shop.py set 命令“直接设定确定件数”语义的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

所有用例均在独立临时目录中运行：显式传入的数据库文件位于该目录，
未传 --db 时也把子进程的工作目录切到该目录，因此不会接触
项目或用户已有的 shop.sqlite3。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 数量上界：SQLite INTEGER 的最大值
MAX_QUANTITY = 9223372036854775807
OVER_MAX = MAX_QUANTITY + 1

# 固定样例：两件 P001、一件 P002
SAMPLE = (("P001", "2"), ("P002", "1"))
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)
# set P001 3 后：P001 三件、P002 一件
SET_THREE_SHOW = (
    "P001 虚拟笔记本 1200 3 3600",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 4",
    "总金额 6100",
)
# set P001 0 后：仅剩 P002 一件
ONLY_P002_SHOW = (
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 1",
    "总金额 2500",
)

CATALOG_OUTPUT = "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n"

# set 不接受的目标数量：正负号、小数、空字符串、字母、前后空白、
# 制表符、全角数字。注意与 add/decrease 不同："0" 与 "000" 对 set 合法。
INVALID_QUANTITIES = (
    "-1",
    "1.5",
    "",
    "abc",
    "+1",
    " 1",
    "1 ",
    "\t1",
    "１",
)

# 超长数量：超过 Python 3.11 默认的数字转换位数限制（4300 位）。
LONG_NINES = "9" * 5000
LONG_LEADING_ZEROS_ONE = "0" * 5000 + "1"
LONG_ZEROS = "0" * 5000
# 超长数字中夹入一个字母：整体仍是格式错误（且优先于编号错误）
LONG_WITH_LETTER = "9" * 2500 + "a" + "9" * 2499


class ShopSetTests(unittest.TestCase):
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

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_set_fixed_sample_to_three_then_zero(self):
        """固定验收样例：设为 3 后小计 3600、总数 4、总额 6100；再设 0 只剩 P002。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", "3"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 3\n")
        self.assertEqual(result.stderr, "")

        # 另起进程确认覆盖落盘（不是累计：2 没有变成 5）
        self.assert_show(db, SET_THREE_SHOW)

        result = self.run_shop(["set", "P001", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 0\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db, ONLY_P002_SHOW)

    def test_set_same_quantity_succeeds(self):
        """目标与当前数量相同也成功，数量保持不变。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 2\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db, SAMPLE_SHOW)

    def test_set_accepts_leading_zeros_and_normalizes_output(self):
        """前导零按数值解释：003 与 3 等价，000 与 0 等价，输出按数值规范化。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", "0003"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 3\n")

        result = self.run_shop(["set", "P001", "000"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 0\n")

        self.assert_show(db, ONLY_P002_SHOW)

    def test_set_is_absolute_not_accumulation_or_decrease(self):
        """已有 2 件时设为 1：结果恰好是 1，既不是 2+1 也不按减少量另作解释。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 1\n")

        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 1 1200",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 2",
                "总金额 3700",
            ),
        )

    def test_set_to_zero_keeps_catalog_and_allows_readd(self):
        """设为零只删购物车记录：目录保持原样，之后仍可 add 重新加入。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 0\n")

        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, CATALOG_OUTPUT)

        # 已移除后 set 不负责首次加入
        result = self.run_shop(["set", "P001", "2"], db=db)
        self.assert_failure(result, 2, "商品不在购物车")

        # add 后恢复正常
        result = self.run_shop(["add", "P001", "3"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 3\n")
        self.assert_show(db, SET_THREE_SHOW)

    def test_set_does_not_first_add_even_to_zero(self):
        """购物车没有该商品：即使目标为零也报商品不在购物车，不创建记录。"""
        db = self.tmpdir / "cart.sqlite3"
        result = self.run_shop(["add", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["set", "P002", "0"], db=db)
        self.assert_failure(result, 2, "商品不在购物车")

        result = self.run_shop(["set", "P002", "3"], db=db)
        self.assert_failure(result, 2, "商品不在购物车")

        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 2 2400", "总数量 2", "总金额 2400"),
        )

    def test_set_on_empty_cart_reports_not_in_cart(self):
        """全新空库上 set：目录存在但购物车为空，报商品不在购物车。"""
        db = self.tmpdir / "empty.sqlite3"

        result = self.run_shop(["set", "P001", "1"], db=db)
        self.assert_failure(result, 2, "商品不在购物车")

        self.assert_show(db, ("总数量 0", "总金额 0"))

    def test_set_leaves_other_quantities_unchanged(self):
        """设定只改指定商品：其他商品数量与名称、单价均不变。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", "5"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 5 6000",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 6",
                "总金额 8500",
            ),
        )

        result = self.run_shop(["set", "P002", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 数量 0\n")

        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 5 6000", "总数量 5", "总金额 6000"),
        )

    def test_set_unknown_product(self):
        """未知编号 P999：数量有效时报未知商品，购物车原样保留。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P999", "0"], db=db)
        self.assert_failure(result, 2, "未知商品")

        result = self.run_shop(["set", "P999", "1"], db=db)
        self.assert_failure(result, 2, "未知商品")

        self.assert_show(db, SAMPLE_SHOW)

    def test_set_rejects_invalid_quantities_and_preserves_cart(self):
        """各类非非负整数目标均被拒绝，每次失败后购物车不变。"""
        db = self.seed_sample()

        for quantity in INVALID_QUANTITIES:
            with self.subTest(quantity=quantity):
                result = self.run_shop(["set", "P001", quantity], db=db)
                self.assert_failure(result, 2, "数量必须为非负整数")
                self.assert_show(db, SAMPLE_SHOW)

    def test_set_error_priority_format_range_then_product(self):
        """错误优先级固定：格式错误 > 范围错误 > 未知商品 > 商品不在购物车。"""
        db = self.seed_sample()

        # P999 未知编号：仍先报数量侧错误
        for quantity, message in (
            ("abc", "数量必须为非负整数"),
            (str(OVER_MAX), "数量超出范围"),
            (LONG_NINES, "数量超出范围"),
            (LONG_WITH_LETTER, "数量必须为非负整数"),
        ):
            with self.subTest(quantity=quantity):
                result = self.run_shop(["set", "P999", quantity], db=db)
                self.assert_failure(result, 2, message)
                self.assert_show(db, SAMPLE_SHOW)

        # 全新库：P002 在目录但购物车为空，数量侧错误仍优先于“不在购物车”
        fresh = self.tmpdir / "fresh.sqlite3"
        result = self.run_shop(["set", "P002", "x"], db=fresh)
        self.assert_failure(result, 2, "数量必须为非负整数")
        result = self.run_shop(["set", "P002", str(OVER_MAX)], db=fresh)
        self.assert_failure(result, 2, "数量超出范围")
        result = self.run_shop(["set", "P002", "1"], db=fresh)
        self.assert_failure(result, 2, "商品不在购物车")

    def test_set_at_max_quantity_succeeds_and_persists(self):
        """目标恰为上界时成功，落库后恰为上界。"""
        db = self.seed_sample()

        result = self.run_shop(
            ["set", "P001", str(MAX_QUANTITY)], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"P001 数量 {MAX_QUANTITY}\n")
        self.assertEqual(result.stderr, "")

        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], f"P001 虚拟笔记本 1200 {MAX_QUANTITY} {1200 * MAX_QUANTITY}")
        self.assertEqual(lines[1], "P002 虚拟马克杯 2500 1 2500")
        self.assertEqual(lines[2], f"总数量 {MAX_QUANTITY + 1}")

    def test_set_over_max_is_range_error_and_preserves_cart(self):
        """目标超过上界：报范围错误且不写入，购物车原样保留。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", str(OVER_MAX)], db=db)
        self.assert_failure(result, 2, "数量超出范围")

        result = self.run_shop(
            ["set", "P001", "000" + str(OVER_MAX)], db=db
        )
        self.assert_failure(result, 2, "数量超出范围")

        self.assert_show(db, SAMPLE_SHOW)

    def test_overlong_quantities_have_deterministic_results(self):
        """五千位数字文本：格式/范围/成功各有确定结果，不出现异常堆栈。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", LONG_NINES], db=db)
        self.assert_failure(result, 2, "数量超出范围")
        self.assert_show(db, SAMPLE_SHOW)

        result = self.run_shop(
            ["set", "P001", LONG_LEADING_ZEROS_ONE], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        self.assertEqual(result.stderr, "")

        result = self.run_shop(["set", "P001", LONG_ZEROS], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 0\n")
        self.assert_show(db, ONLY_P002_SHOW)

    def test_set_without_enough_args_is_argument_error(self):
        """set 缺少编号或目标数量：报参数错误，且不创建数据库。"""
        db = self.seed_sample()

        result = self.run_shop(["set"], db=db)
        self.assert_failure(result, 2, "参数错误")

        result = self.run_shop(["set", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, SAMPLE_SHOW)

    def test_set_with_extra_arg_is_argument_error_and_no_db_created(self):
        """set 多带参数：报参数错误；全新路径上不创建数据库文件。"""
        fresh = self.tmpdir / "never.sqlite3"
        self.assertFalse(fresh.exists())

        result = self.run_shop(["set", "P001", "1", "2"], db=fresh)
        self.assert_failure(result, 2, "参数错误")
        self.assertFalse(fresh.exists())

    def test_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：仅输出数据库不可用并退出 1，无成功信息与堆栈。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["set", "P001", "1"], db=directory)
        self.assert_failure(result, 1, "数据库不可用")

    def test_default_db_in_temp_cwd_shares_set_semantics(self):
        """不传 --db：使用临时工作目录下的 shop.sqlite3，set 语义一致且持久。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        self.seed_sample(db=None, cwd=workdir)
        self.assertTrue(default_db.is_file())

        result = self.run_shop(["set", "P001", "003"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 3\n")
        self.assertEqual(result.stderr, "")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(SET_THREE_SHOW) + "\n")

        result = self.run_shop(["set", "P001", "0"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "P001 数量 0\n")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(ONLY_P002_SHOW) + "\n")

    def test_explicit_db_files_are_independent(self):
        """显式指定文件：在一个库中 set 不影响另一个库的目录与购物车。"""
        db_a = self.seed_sample()
        db_b = self.tmpdir / "b.sqlite3"

        result = self.run_shop(["add", "P001", "7"], db=db_b)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["set", "P001", "3"], db=db_a)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 3\n")

        self.assert_show(db_a, SET_THREE_SHOW)
        self.assert_show(
            db_b,
            ("P001 虚拟笔记本 1200 7 8400", "总数量 7", "总金额 8400"),
        )


if __name__ == "__main__":
    unittest.main()
