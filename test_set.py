#!/usr/bin/env python3
"""shop.py set 命令直接设定数量语义的可重复回归测试。

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

# 固定样例：两件 P001、一件 P002
SAMPLE = (("P001", "2"), ("P002", "1"))
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)
# set P001 3 后：P001 三件、P002 一件
SET_TO_THREE_SHOW = (
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

CATALOG_SHOW = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)

# 各类不被接受的非负整数数量：空字符串、负数、显式正号、小数、
# 字母、带空格的数字、全角数字、混入其他字符
INVALID_QUANTITIES = (
    "",
    "-1",
    "+1",
    "1.5",
    ".0",
    "abc",
    " 1",
    "1 ",
    "\t0",
    "１",
    "1a",
    "0x1",
)

# 超长数量：超过 Python 3.11 默认的数字转换位数限制（4300 位）。
# 五千位的数字串若直接 int() 会抛 ValueError，必须仍走确定的业务结果。
LONG_NINES = "9" * 5000
LONG_LEADING_ZEROS_ONE = "0" * 5000 + "1"
LONG_ZEROS = "0" * 5000
# 超长数字中夹入一个字母：整体仍是格式错误（且优先于编号错误）
LONG_WITH_LETTER = "0" * 2500 + "a" + "0" * 2499


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

    def test_set_fixed_sample_then_zero(self):
        """固定样例：set P001 3 后小计 3600、总数 4、总额 6100；再置零只剩 P002。"""
        db = self.seed_sample()

        # 目标数量是确定件数：两件直接变成三件，而非累计成五件
        result = self.run_shop(["set", "P001", "3"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 3\n")
        self.assertEqual(result.stderr, "")

        # 另起进程核对持久化结果
        self.assert_show(db, SET_TO_THREE_SHOW)

        # 目标为零：仍输出数量 0，记录从购物车移除，目录保留
        result = self.run_shop(["set", "P001", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 0\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db, ONLY_P002_SHOW)

    def test_set_same_quantity_succeeds(self):
        """目标与当前数量相同也成功，数量与金额保持不变。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 2\n")
        self.assertEqual(result.stderr, "")

        # 前导零等价：0002 与 2 是同一目标，同样成功
        result = self.run_shop(["set", "P001", "0002"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 2\n")

        self.assert_show(db, SAMPLE_SHOW)

    def test_set_direct_quantity_is_not_accumulation_or_decrease(self):
        """set 是直接设定：调高调低都恰好等于目标，其他商品数量不变。"""
        db = self.seed_sample()

        # 调低：两件直接变成一件，不按减少量之外的方式解释
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

        # 调高：一件直接变成五件（若误按累计会变成六件）
        result = self.run_shop(["set", "P001", "5"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 5\n")
        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 5 6000",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 6",
                "总金额 8500",
            ),
        )

    def test_set_all_zero_forms_remove_record(self):
        """0 与 000 等全零文本都按零处理：移除记录并输出数量 0。"""
        for text in ("0", "000", "00000"):
            with self.subTest(text=text):
                # 每个文本使用独立数据库，避免 add 的累计语义干扰
                db = self.tmpdir / f"zero-{len(text)}.sqlite3"
                self.seed_sample(db=db)
                result = self.run_shop(["set", "P001", text], db=db)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "P001 数量 0\n")
                self.assertEqual(result.stderr, "")
                self.assert_show(db, ONLY_P002_SHOW)

    def test_set_zero_keeps_catalog_and_allows_readd(self):
        """置零只删购物车记录：目录保持原样，之后仍可 add 重新加入。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 0\n")

        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "\n".join(CATALOG_SHOW) + "\n")

        result = self.run_shop(["add", "P001", "3"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 3\n")
        self.assert_show(db, SET_TO_THREE_SHOW)

    def test_set_does_not_first_add_product(self):
        """这个入口不负责首次加入：目录存在但购物车没有该商品时报错。"""
        db = self.tmpdir / "cart.sqlite3"
        result = self.run_shop(["add", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        # 目标为正：不新建购物车记录
        result = self.run_shop(["set", "P002", "1"], db=db)
        self.assert_failure(result, 2, "商品不在购物车")

        # 目标为零也一样：不静默成功，也不创建记录
        result = self.run_shop(["set", "P002", "0"], db=db)
        self.assert_failure(result, 2, "商品不在购物车")

        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 2 2400", "总数量 2", "总金额 2400"),
        )

    def test_set_after_remove_or_decrease_to_zero_reports_not_in_cart(self):
        """记录已被 remove/decrease 移除后 set 报商品不在购物车。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["set", "P001", "3"], db=db)
        self.assert_failure(result, 2, "商品不在购物车")
        self.assert_show(db, ONLY_P002_SHOW)

    def test_set_unknown_product(self):
        """未知编号 P999：数量有效时报未知商品，购物车原样保留。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P999", "1"], db=db)
        self.assert_failure(result, 2, "未知商品")

        # 目标为零也不能借置零绕过编号校验
        result = self.run_shop(["set", "P999", "0"], db=db)
        self.assert_failure(result, 2, "未知商品")

        self.assert_show(db, SAMPLE_SHOW)

    def test_set_rejects_invalid_quantities_and_preserves_cart(self):
        """各类非非负整数数量均被拒绝，每次失败后购物车不变。"""
        db = self.seed_sample()

        for quantity in INVALID_QUANTITIES:
            with self.subTest(quantity=quantity):
                result = self.run_shop(["set", "P001", quantity], db=db)
                self.assert_failure(result, 2, "数量必须为非负整数")
                self.assert_show(db, SAMPLE_SHOW)

    def test_set_format_error_priority_over_range_and_unknown(self):
        """格式错误优先于范围错误，两者优先于未知编号。"""
        db = self.seed_sample()

        # P999 未知：格式错误仍最先报告
        for quantity in ("abc", "-1", "", "1a"):
            with self.subTest(quantity=quantity):
                result = self.run_shop(["set", "P999", quantity], db=db)
                self.assert_failure(result, 2, "数量必须为非负整数")

        # 超出上界的数字串中夹入字母：仍是格式错误先于范围错误
        beyond = str(MAX_QUANTITY + 1) + "x"
        result = self.run_shop(["set", "P001", beyond], db=db)
        self.assert_failure(result, 2, "数量必须为非负整数")

        # 格式正确但越界：范围错误优先于未知编号
        result = self.run_shop(
            ["set", "P999", str(MAX_QUANTITY + 1)], db=db
        )
        self.assert_failure(result, 2, "数量超出范围")

        self.assert_show(db, SAMPLE_SHOW)

    def test_set_at_max_succeeds_and_over_max_rejected(self):
        """目标恰为上界成功落库；超过上界拒绝且购物车不变。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", str(MAX_QUANTITY)], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"P001 数量 {MAX_QUANTITY}\n")

        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"P001 虚拟笔记本 1200 {MAX_QUANTITY} ", result.stdout)

        # 越界拒绝：数量停在上界，P002 一件不变
        result = self.run_shop(
            ["set", "P001", str(MAX_QUANTITY + 1)], db=db
        )
        self.assert_failure(result, 2, "数量超出范围")

        result = self.run_shop(["set", "P001", str(MAX_QUANTITY)], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"P001 数量 {MAX_QUANTITY}\n")

    def test_set_without_enough_args_is_argument_error(self):
        """set 缺少编号或数量：报参数错误，购物车不变。"""
        db = self.seed_sample()

        result = self.run_shop(["set"], db=db)
        self.assert_failure(result, 2, "参数错误")

        result = self.run_shop(["set", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, SAMPLE_SHOW)

    def test_set_with_extra_arg_is_argument_error(self):
        """set 多带参数：报参数错误，不执行任何变更。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", "1", "2"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, SAMPLE_SHOW)

    def test_set_argument_error_does_not_create_database(self):
        """参数错误在打开数据库之前判定：不会创建数据库文件。"""
        db = self.tmpdir / "never.sqlite3"
        self.assertFalse(db.exists())

        result = self.run_shop(["set"], db=db)
        self.assert_failure(result, 2, "参数错误")
        self.assertFalse(db.exists())

        result = self.run_shop(["set", "P001", "1", "2"], db=db)
        self.assert_failure(result, 2, "参数错误")
        self.assertFalse(db.exists())

    def test_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：仅输出数据库不可用并退出 1，无成功信息与堆栈。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["set", "P001", "1"], db=directory)
        self.assert_failure(result, 1, "数据库不可用")

    def test_default_db_in_temp_cwd_shares_set_semantics(self):
        """不传 --db：使用临时工作目录下的 shop.sqlite3，set 语义一致并持久化。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        self.seed_sample(db=None, cwd=workdir)
        self.assertTrue(default_db.is_file())

        result = self.run_shop(["set", "P001", "3"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 3\n")
        self.assertEqual(result.stderr, "")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(SET_TO_THREE_SHOW) + "\n")

        # 置零并跨进程持久化
        result = self.run_shop(["set", "P001", "0"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "P001 数量 0\n")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(ONLY_P002_SHOW) + "\n")

    def test_explicit_db_files_are_independent(self):
        """显式指定文件：在一个库中 set 不影响另一个库的目录与购物车。"""
        db_a = self.tmpdir / "a.sqlite3"
        db_b = self.tmpdir / "b.sqlite3"
        self.seed_sample(db_a)
        self.seed_sample(db_b)

        result = self.run_shop(["set", "P001", "3"], db=db_a)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 3\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db_a, SET_TO_THREE_SHOW)
        self.assert_show(db_b, SAMPLE_SHOW)

    def test_overlong_quantity_is_range_error_without_traceback(self):
        """五千个 9 作为目标：报范围错误（退出 2），无异常堆栈，购物车不变。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", LONG_NINES], db=db)
        self.assert_failure(result, 2, "数量超出范围")

        self.assert_show(db, SAMPLE_SHOW)

    def test_overlong_quantity_with_leading_zeros_succeeds(self):
        """五千个前导零后接 1：与 1 等价，成功把 P001 设为一件并持久化。"""
        db = self.seed_sample()

        result = self.run_shop(
            ["set", "P001", LONG_LEADING_ZEROS_ONE], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 1 1200",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 2",
                "总金额 3700",
            ),
        )

    def test_overlong_all_zero_quantity_removes_record(self):
        """五千个 0：按零处理，成功移除记录并输出数量 0。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", LONG_ZEROS], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 0\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db, ONLY_P002_SHOW)

    def test_overlong_quantity_with_letter_reports_format_error_first(self):
        """超长数字中夹入字母：即使编号 P999 未知，也先报数量格式错误。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P999", LONG_WITH_LETTER], db=db)
        self.assert_failure(result, 2, "数量必须为非负整数")

        self.assert_show(db, SAMPLE_SHOW)

    def test_overlong_quantity_range_error_before_unknown_product(self):
        """有效格式但越界的超长数量配未知编号：范围错误先于未知商品。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P999", LONG_NINES], db=db)
        self.assert_failure(result, 2, "数量超出范围")

        self.assert_show(db, SAMPLE_SHOW)


if __name__ == "__main__":
    unittest.main()
