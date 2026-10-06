#!/usr/bin/env python3
"""shop.py add 命令数量语义的可重复回归测试。

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

# 固定样例：一件 P002、七件 P001（007 验证前导零按数值解释）
SAMPLE = (("P002", "1"), ("P001", "007"))
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 7 8400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 8",
    "总金额 10900",
)
ACCUMULATED_SHOW = (
    "P001 虚拟笔记本 1200 9 10800",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 10",
    "总金额 13300",
)

# 一律被拒绝的数量写法：零、负数、小数、空串、文本、符号、空白、全角数字
INVALID_QUANTITIES = ("0", "000", "-1", "1.5", "", "abc", "+1", " 1", "１２３")


class ShopAddTests(unittest.TestCase):
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

    def seed_sample(self, db=...):
        """用公开的 add 语义准备固定样例购物车。"""
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            expected = int(quantity)
            self.assertEqual(result.stdout, f"{product_id} 数量 {expected}\n")
        return self.tmpdir / "cart.sqlite3" if db is ... else db

    def assert_show(self, db, expected_lines):
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_add_accumulates_and_leading_zeros_are_numeric(self):
        """成功场景：前导零按数值解释，重复加入累计而非覆盖，他项不变。"""
        db = self.tmpdir / "cart.sqlite3"

        result = self.run_shop(["add", "P002", "1"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P002 数量 1\n")
        self.assertEqual(result.stderr, "")

        result = self.run_shop(["add", "P001", "007"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 7\n")
        self.assertEqual(result.stderr, "")

        # 重复加入同一商品：数量累计为 7 + 2 = 9，而非覆盖为 2
        result = self.run_shop(["add", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 9\n")
        self.assertEqual(result.stderr, "")

        # 另起进程查看：按编号升序，P002 的数量与单价均未受影响
        self.assert_show(db, ACCUMULATED_SHOW)

    def test_add_rejects_invalid_quantities_and_preserves_cart(self):
        """非法数量逐一验证：退出码 2、空标准输出、单行错误，购物车原样保留。"""
        db = self.seed_sample()

        for quantity in INVALID_QUANTITIES:
            with self.subTest(quantity=quantity):
                result = self.run_shop(["add", "P001", quantity], db=db)
                self.assert_failure(result, 2, "数量必须为正整数")
                self.assert_show(db, SAMPLE_SHOW)

    def test_add_invalid_quantity_checked_before_unknown_product(self):
        """数量与编号同时无效时优先报告数量错误，购物车不变。"""
        db = self.seed_sample()

        result = self.run_shop(["add", "P999", "abc"], db=db)
        self.assert_failure(result, 2, "数量必须为正整数")

        self.assert_show(db, SAMPLE_SHOW)

    def test_add_unknown_product_with_valid_quantity(self):
        """未知编号 P999 配合合法数量：仅报未知商品，购物车原样保留。"""
        db = self.seed_sample()

        result = self.run_shop(["add", "P999", "1"], db=db)
        self.assert_failure(result, 2, "未知商品")

        self.assert_show(db, SAMPLE_SHOW)

    def test_add_missing_quantity_is_argument_error(self):
        """add 缺少数量：仅报参数错误，购物车不变。"""
        db = self.seed_sample()

        result = self.run_shop(["add", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, SAMPLE_SHOW)

    def test_add_with_extra_arg_is_argument_error(self):
        """add 多带参数：仅报参数错误，不执行任何加入。"""
        db = self.seed_sample()

        result = self.run_shop(["add", "P001", "2", "3"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, SAMPLE_SHOW)

    def test_default_db_in_temp_cwd_preserves_state_after_quantity_error(self):
        """不传 --db：临时工作目录下的 shop.sqlite3 在数量错误后保持原状。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        for product_id, quantity in SAMPLE:
            result = self.run_shop(
                ["add", product_id, quantity], db=None, cwd=workdir
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(default_db.is_file())

        result = self.run_shop(["add", "P001", "abc"], db=None, cwd=workdir)
        self.assert_failure(result, 2, "数量必须为正整数")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(SAMPLE_SHOW) + "\n")

    def test_explicit_db_preserves_state_after_quantity_error(self):
        """显式 --db：临时文件中的库在数量错误后保持原状。"""
        db = self.seed_sample()

        result = self.run_shop(["add", "P002", "0"], db=db)
        self.assert_failure(result, 2, "数量必须为正整数")

        self.assert_show(db, SAMPLE_SHOW)


if __name__ == "__main__":
    unittest.main()
