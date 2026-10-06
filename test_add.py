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

# 从空库依次执行 add P002 1 / add P001 007 / add P001 2 后的完整购物车
CART_SHOW = (
    "P001 虚拟笔记本 1200 9 10800",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 10",
    "总金额 13300",
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

    def seed_accumulated_cart(self, db=..., cwd=None):
        """从空库按公开命令构造已知购物车：P002=1、P001=007→7、P001 再 +2=9。"""
        result = self.run_shop(["add", "P002", "1"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "P002 数量 1\n")

        result = self.run_shop(["add", "P001", "007"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 前导零按数值解释：007 与 7 等价
        self.assertEqual(result.stdout, "P001 数量 7\n")

        result = self.run_shop(["add", "P001", "2"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 重复加入是累计（7+2）而非覆盖
        self.assertEqual(result.stdout, "P001 数量 9\n")

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

    def test_add_success_leading_zero_and_accumulation_persist(self):
        """成功场景：前导零按数值解释、重复加入累计、排序与金额正确落盘。"""
        db = self.seed_accumulated_cart()

        # 另起进程查看：按编号升序，P001 累计为 9，P002 数量与单价不受影响
        self.assert_show(db, CART_SHOW)

    def test_explicit_db_rejects_invalid_quantities_and_preserves_cart(self):
        """显式 --db 入口：各类非正整数数量均被拒绝，每次失败后购物车不变。"""
        db = self.seed_accumulated_cart()

        for quantity in INVALID_QUANTITIES:
            with self.subTest(quantity=quantity):
                result = self.run_shop(["add", "P001", quantity], db=db)
                self.assert_failure(result, 2, "数量必须为正整数")
                # 每次失败后重新查看：完整购物车内容与调用前相同
                self.assert_show(db, CART_SHOW)

    def test_add_unknown_product_quantity_error_has_priority(self):
        """P999：数量无效时优先报数量错误；数量有效时仅报未知商品。"""
        db = self.seed_accumulated_cart()

        for quantity in ("0", "abc", "-1"):
            with self.subTest(quantity=quantity):
                result = self.run_shop(["add", "P999", quantity], db=db)
                self.assert_failure(result, 2, "数量必须为正整数")

        result = self.run_shop(["add", "P999", "1"], db=db)
        self.assert_failure(result, 2, "未知商品")

        self.assert_show(db, CART_SHOW)

    def test_add_without_quantity_is_argument_error(self):
        """add 缺少数量：仅报参数错误，购物车不变。"""
        db = self.seed_accumulated_cart()

        result = self.run_shop(["add", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, CART_SHOW)

    def test_add_with_extra_arg_is_argument_error(self):
        """add 多出一个参数：仅报参数错误，购物车不变。"""
        db = self.seed_accumulated_cart()

        result = self.run_shop(["add", "P001", "1", "2"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, CART_SHOW)

    def test_default_db_in_temp_cwd_rejects_bad_quantity_and_preserves(self):
        """默认数据库入口：工作目录置于临时目录，数量错误后状态仍保留。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        self.seed_accumulated_cart(db=None, cwd=workdir)
        self.assertTrue(default_db.is_file())

        result = self.run_shop(["add", "P002", "0"], db=None, cwd=workdir)
        self.assert_failure(result, 2, "数量必须为正整数")

        # 另一次 show 调用确认失败结果没有落盘，完整购物车保持不变
        self.assert_show(None, CART_SHOW, cwd=workdir)


if __name__ == "__main__":
    unittest.main()
