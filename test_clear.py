#!/usr/bin/env python3
"""shop.py clear 命令的可重复回归测试。

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
EMPTY_CART_SHOW = ("总数量 0", "总金额 0")
DEFAULT_CATALOG = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)


class ShopClearTests(unittest.TestCase):
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

    def assert_catalog(self, db, expected_lines):
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_clear_sample_cart_persists_and_products_remain(self):
        """成功场景：清空后 show 为空车汇总，目录保留，重新加入按保存资料计价。"""
        db = self.seed_sample()

        result = self.run_shop(["clear"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "购物车已清空\n")
        self.assertEqual(result.stderr, "")

        # 清空结果已持久化：另一次调用 show 只有空车汇总两行
        self.assert_show(db, EMPTY_CART_SHOW)

        # 重复清空不报错，结果相同
        result = self.run_shop(["clear"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "购物车已清空\n")
        self.assertEqual(result.stderr, "")
        self.assert_show(db, EMPTY_CART_SHOW)

        # 商品目录未被删除，资料与清空前一致
        self.assert_catalog(db, DEFAULT_CATALOG)

        # 清空后仍可重新加入：数量 1、小计 1200
        result = self.run_shop(["add", "P001", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 1 1200", "总数量 1", "总金额 1200"),
        )

    def test_clear_empty_cart_is_idempotent_success(self):
        """购物车本来为空：同样成功输出，退出码 0。"""
        db = self.tmpdir / "cart.sqlite3"
        # 先用 catalog 触发建库，保证 clear 面对的是已存在的空车库
        self.assertEqual(self.run_shop(["catalog"], db=db).returncode, 0)

        result = self.run_shop(["clear"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "购物车已清空\n")
        self.assertEqual(result.stderr, "")
        self.assert_show(db, EMPTY_CART_SHOW)
        self.assert_catalog(db, DEFAULT_CATALOG)

    def test_clear_missing_db_creates_with_demo_products(self):
        """对尚未创建的数据库执行 clear：按首次使用规则建库写演示商品，购物车为空。"""
        db = self.tmpdir / "brand_new.sqlite3"
        self.assertFalse(db.exists())

        result = self.run_shop(["clear"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "购物车已清空\n")
        self.assertEqual(result.stderr, "")
        self.assertTrue(db.is_file())

        self.assert_show(db, EMPTY_CART_SHOW)
        self.assert_catalog(db, DEFAULT_CATALOG)

    def test_clear_uses_saved_name_and_price_after_re_add(self):
        """已有名称或价格调整的库：清空只删购物车，重新加入以保存的数据计价。"""
        db = self.tmpdir / "modified.sqlite3"
        init = self.run_shop(["add", "P001", "1"], db=db)
        self.assertEqual(init.returncode, 0, init.stderr)
        import sqlite3

        conn = sqlite3.connect(db)
        try:
            conn.execute("UPDATE products SET name = ?, price = ? WHERE id = ?",
                         ("改名商品", 999, "P001"))
            conn.commit()
        finally:
            conn.close()

        result = self.run_shop(["clear"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["add", "P001", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_show(
            db,
            ("P001 改名商品 999 1 999", "总数量 1", "总金额 999"),
        )

    def test_clear_with_extra_arg_is_argument_error(self):
        """clear 多带参数：报参数错误退出 2，不触发清空，原有条目保持不变。"""
        db = self.seed_sample()

        result = self.run_shop(["clear", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")
        self.assert_show(db, SAMPLE_SHOW)

        result = self.run_shop(["clear", "1"], db=db)
        self.assert_failure(result, 2, "参数错误")
        self.assert_show(db, SAMPLE_SHOW)

    def test_clear_extra_arg_does_not_create_database(self):
        """参数错误优先于建库：库不存在时多带参数不创建任何文件。"""
        db = self.tmpdir / "never_created.sqlite3"
        result = self.run_shop(["clear", "x"], db=db)
        self.assert_failure(result, 2, "参数错误")
        self.assertFalse(db.exists())

    def test_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：仅输出数据库不可用并退出 1，无成功信息与堆栈。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["clear"], db=directory)
        self.assert_failure(result, 1, "数据库不可用")

    def test_default_db_in_temp_cwd_shares_clear_semantics(self):
        """不传 --db：使用临时工作目录下的 shop.sqlite3，清空语义一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        for product_id, quantity in SAMPLE:
            result = self.run_shop(
                ["add", product_id, quantity], db=None, cwd=workdir
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(default_db.is_file())

        result = self.run_shop(["clear"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "购物车已清空\n")
        self.assertEqual(result.stderr, "")

        result = self.run_shop(["show"], db=None, cwd=workdir)
        self.assertEqual(result.stdout, "\n".join(EMPTY_CART_SHOW) + "\n")

    def test_explicit_db_files_are_independent(self):
        """在一个库中清空不影响另一个库的购物车与目录。"""
        db_a = self.tmpdir / "a.sqlite3"
        db_b = self.tmpdir / "b.sqlite3"
        self.seed_sample(db_a)

        result = self.run_shop(["add", "P001", "3"], db=db_b)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["clear"], db=db_a)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "购物车已清空\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db_a, EMPTY_CART_SHOW)
        self.assert_show(
            db_b,
            ("P001 虚拟笔记本 1200 3 3600", "总数量 3", "总金额 3600"),
        )


if __name__ == "__main__":
    unittest.main()
