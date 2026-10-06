#!/usr/bin/env python3
"""shop.py decrease 命令按数量减少语义的可重复回归测试。

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

# 固定样例：两件 P001、一件 P002
SAMPLE = (("P001", "2"), ("P002", "1"))
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)
# decrease P001 01 后：两件商品各一件
ONE_EACH_SHOW = (
    "P001 虚拟笔记本 1200 1 1200",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 2",
    "总金额 3700",
)
# P001 恰好减至零后：仅剩 P002 一件
ONLY_P002_SHOW = (
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 1",
    "总金额 2500",
)

# 各类不被接受的数量：零、全零、负数、小数、空字符串、字母、
# 显式正号、带空格的数字、全角数字
INVALID_QUANTITIES = (
    "0",
    "000",
    "-1",
    "1.5",
    "",
    "abc",
    "+1",
    " 1",
    "１",
)


class ShopDecreaseTests(unittest.TestCase):
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

    def test_decrease_fixed_sample_partial_then_exact_zero(self):
        """固定样例：减一件保留一件，再减一件移除，重复减少报不在购物车。"""
        db = self.seed_sample()

        # 数量表示本次减少多少件；01 与 1 等价，输出剩余数量
        result = self.run_shop(["decrease", "P001", "01"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        self.assertEqual(result.stderr, "")

        # 另起进程：两件商品各一件，总数量 2、总金额 3700 分
        self.assert_show(db, ONE_EACH_SHOW)

        # 恰好减至零：仍输出数量 0，记录从购物车移除
        result = self.run_shop(["decrease", "P001", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 0\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db, ONLY_P002_SHOW)

        # 已移除后再次减少：报商品不在购物车，购物车保持仅剩 P002
        result = self.run_shop(["decrease", "P001", "1"], db=db)
        self.assert_failure(result, 2, "商品不在购物车")
        self.assert_show(db, ONLY_P002_SHOW)

    def test_decrease_to_zero_keeps_catalog_and_allows_readd(self):
        """减至零只删购物车记录：目录保持原样，之后仍可 add 重新加入。"""
        db = self.seed_sample()

        result = self.run_shop(["decrease", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 0\n")

        # 目录未受影响：名称与单价仍以库中内容为准
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n",
        )

        result = self.run_shop(["add", "P001", "3"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 3\n")
        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 3 3600",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 4",
                "总金额 6100",
            ),
        )

    def test_partial_decrease_preserves_other_quantities(self):
        """部分减少只改指定商品：其余数量、他项与金额均不变。"""
        db = self.seed_sample()

        result = self.run_shop(["decrease", "P001", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 1\n")

        result = self.run_shop(["decrease", "P002", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 数量 0\n")

        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 1 1200", "总数量 1", "总金额 1200"),
        )

    def test_decrease_more_than_cart_quantity_rejected(self):
        """减少量超过现有数量：报错且不自动清空、不产生负数，原样保留。"""
        db = self.seed_sample()

        result = self.run_shop(["decrease", "P001", "3"], db=db)
        self.assert_failure(result, 2, "减少数量超过购物车数量")

        # 恰好相等是允许的，只有“超过”才拒绝
        result = self.run_shop(["decrease", "P002", "2"], db=db)
        self.assert_failure(result, 2, "减少数量超过购物车数量")

        self.assert_show(db, SAMPLE_SHOW)

    def test_decrease_catalog_product_not_in_cart(self):
        """目录中存在但未加入购物车：报商品不在购物车，原有内容不变。"""
        db = self.tmpdir / "cart.sqlite3"
        result = self.run_shop(["add", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["decrease", "P002", "1"], db=db)
        self.assert_failure(result, 2, "商品不在购物车")

        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 2 2400", "总数量 2", "总金额 2400"),
        )

    def test_decrease_unknown_product(self):
        """未知编号 P999：数量有效时报未知商品，购物车原样保留。"""
        db = self.seed_sample()

        result = self.run_shop(["decrease", "P999", "1"], db=db)
        self.assert_failure(result, 2, "未知商品")

        self.assert_show(db, SAMPLE_SHOW)

    def test_decrease_rejects_invalid_quantities_and_preserves_cart(self):
        """各类非正整数数量均被拒绝，每次失败后购物车不变。"""
        db = self.seed_sample()

        for quantity in INVALID_QUANTITIES:
            with self.subTest(quantity=quantity):
                result = self.run_shop(
                    ["decrease", "P001", quantity], db=db
                )
                self.assert_failure(result, 2, "数量必须为正整数")
                self.assert_show(db, SAMPLE_SHOW)

    def test_decrease_quantity_error_has_priority_over_unknown_product(self):
        """P999：数量无效时即使编号未知也优先报数量错误。"""
        db = self.seed_sample()

        for quantity in ("0", "abc", "-1", "000"):
            with self.subTest(quantity=quantity):
                result = self.run_shop(
                    ["decrease", "P999", quantity], db=db
                )
                self.assert_failure(result, 2, "数量必须为正整数")

        self.assert_show(db, SAMPLE_SHOW)

    def test_decrease_without_enough_args_is_argument_error(self):
        """decrease 缺少编号或数量：报参数错误，购物车不变。"""
        db = self.seed_sample()

        result = self.run_shop(["decrease"], db=db)
        self.assert_failure(result, 2, "参数错误")

        result = self.run_shop(["decrease", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, SAMPLE_SHOW)

    def test_decrease_with_extra_arg_is_argument_error(self):
        """decrease 多带参数：报参数错误，不执行任何减少。"""
        db = self.seed_sample()

        result = self.run_shop(["decrease", "P001", "1", "2"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, SAMPLE_SHOW)

    def test_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：仅输出数据库不可用并退出 1，无成功信息与堆栈。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["decrease", "P001", "1"], db=directory)
        self.assert_failure(result, 1, "数据库不可用")

    def test_default_db_in_temp_cwd_shares_decrease_semantics(self):
        """不传 --db：使用临时工作目录下的 shop.sqlite3，减少语义一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        self.seed_sample(db=None, cwd=workdir)
        self.assertTrue(default_db.is_file())

        result = self.run_shop(["decrease", "P001", "01"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        self.assertEqual(result.stderr, "")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(ONE_EACH_SHOW) + "\n")

        # 减至零并跨进程持久化
        result = self.run_shop(["decrease", "P001", "1"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "P001 数量 0\n")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(ONLY_P002_SHOW) + "\n")

        # 默认入口同样适用失败语义：减少过多被拒绝，状态保持
        result = self.run_shop(["decrease", "P002", "9"], db=None, cwd=workdir)
        self.assert_failure(result, 2, "减少数量超过购物车数量")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(ONLY_P002_SHOW) + "\n")

    def test_explicit_db_files_are_independent(self):
        """显式指定文件：在一个库中减少不影响另一个库的目录与购物车。"""
        db_a = self.tmpdir / "a.sqlite3"
        db_b = self.tmpdir / "b.sqlite3"
        self.seed_sample(db_a)

        result = self.run_shop(["add", "P001", "3"], db=db_b)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["decrease", "P001", "1"], db=db_a)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db_a, ONE_EACH_SHOW)
        self.assert_show(
            db_b,
            ("P001 虚拟笔记本 1200 3 3600", "总数量 3", "总金额 3600"),
        )


if __name__ == "__main__":
    unittest.main()
