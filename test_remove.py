#!/usr/bin/env python3
"""shop.py remove 命令的可重复回归测试。

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
ONLY_P002_SHOW = (
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 1",
    "总金额 2500",
)
EMPTY_CART_SHOW = ("总数量 0", "总金额 0")


class ShopRemoveTests(unittest.TestCase):
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
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
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

    def test_remove_success_persists_and_preserves_other_product(self):
        """成功场景：整条移除、结果持久化、他项不变、目录仍可重新加入。"""
        db = self.seed_sample()

        result = self.run_shop(["remove", "P001"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 已移除\n")
        self.assertEqual(result.stderr, "")

        # 重新调用 show：P001 全部数量已删除，P002 未受影响且已落盘
        self.assert_show(db, ONLY_P002_SHOW)

        result = self.run_shop(["remove", "P002"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P002 已移除\n")
        self.assertEqual(result.stderr, "")
        self.assert_show(db, EMPTY_CART_SHOW)

        # 删除购物车记录不等于删除商品目录：P001 仍可重新加入
        result = self.run_shop(["add", "P001", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        self.assertEqual(result.stderr, "")
        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 1 1200", "总数量 1", "总金额 1200"),
        )

    def test_remove_twice_second_call_rejected(self):
        """重复移除：第二次报商品不在购物车，购物车维持第一次移除后的状态。"""
        db = self.seed_sample()

        result = self.run_shop(["remove", "P001"], db=db)
        self.assertEqual(result.returncode, 0)

        result = self.run_shop(["remove", "P001"], db=db)
        self.assert_failure(result, 2, "商品不在购物车")

        self.assert_show(db, ONLY_P002_SHOW)

    def test_remove_catalog_product_not_in_cart(self):
        """目录中存在但未加入购物车：报商品不在购物车，原有内容不变。"""
        db = self.tmpdir / "cart.sqlite3"
        result = self.run_shop(["add", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["remove", "P002"], db=db)
        self.assert_failure(result, 2, "商品不在购物车")

        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 2 2400", "总数量 2", "总金额 2400"),
        )

    def test_remove_unknown_product(self):
        """未知编号 P999：报未知商品，购物车原样保留。"""
        db = self.seed_sample()

        result = self.run_shop(["remove", "P999"], db=db)
        self.assert_failure(result, 2, "未知商品")

        self.assert_show(db, SAMPLE_SHOW)

    def test_remove_without_id_is_argument_error(self):
        """remove 缺少编号：报参数错误，购物车不变。"""
        db = self.seed_sample()

        result = self.run_shop(["remove"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, SAMPLE_SHOW)

    def test_remove_with_extra_arg_is_argument_error(self):
        """remove 多带参数：报参数错误，不执行任何移除。"""
        db = self.seed_sample()

        result = self.run_shop(["remove", "P001", "9"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, SAMPLE_SHOW)

    def test_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：仅输出数据库不可用并退出 1，无成功信息与堆栈。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["remove", "P001"], db=directory)
        self.assert_failure(result, 1, "数据库不可用")

    def test_default_db_in_temp_cwd_shares_remove_semantics(self):
        """不传 --db：使用临时工作目录下的 shop.sqlite3，移除语义一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        for product_id, quantity in SAMPLE:
            result = self.run_shop(
                ["add", product_id, quantity], db=None, cwd=workdir
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(default_db.is_file())

        result = self.run_shop(["remove", "P001"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 已移除\n")
        self.assertEqual(result.stderr, "")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(ONLY_P002_SHOW) + "\n")

        # 默认入口同样适用失败语义：重复移除被拒绝，状态保持
        result = self.run_shop(["remove", "P001"], db=None, cwd=workdir)
        self.assert_failure(result, 2, "商品不在购物车")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(ONLY_P002_SHOW) + "\n")

    def test_explicit_db_files_are_independent(self):
        """显式指定文件：在一个库中移除不影响另一个库的目录与购物车。"""
        db_a = self.tmpdir / "a.sqlite3"
        db_b = self.tmpdir / "b.sqlite3"
        self.seed_sample(db_a)

        result = self.run_shop(["add", "P001", "3"], db=db_b)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["remove", "P001"], db=db_a)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 已移除\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db_a, ONLY_P002_SHOW)
        self.assert_show(
            db_b,
            ("P001 虚拟笔记本 1200 3 3600", "总数量 3", "总金额 3600"),
        )


if __name__ == "__main__":
    unittest.main()
