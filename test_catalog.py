#!/usr/bin/env python3
"""shop.py catalog 命令的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

所有用例均在独立临时目录中运行：显式传入的数据库文件位于该目录，
未传 --db 时也把子进程的工作目录切到该目录，因此不会接触
项目或用户已有的 shop.sqlite3。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 首次创建数据库时写入的固定目录
INITIAL_CATALOG = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)

# 测试数据准备：先加入两件 P001、一件 P002
SAMPLE = (("P001", "2"), ("P002", "1"))

# 直接把库中 P001 改名为“演示笔记本”、单价改为 1500 分后的目录与购物车
RENAMED_CATALOG = (
    "P001 演示笔记本 1500",
    "P002 虚拟马克杯 2500",
)
RENAMED_CART_SHOW = (
    "P001 演示笔记本 1500 2 3000",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 5500",
)

# 未改过名称与价格时的样例购物车
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)

EMPTY_CART_SHOW = ("总数量 0", "总金额 0")


class ShopCatalogTests(unittest.TestCase):
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

    def seed_sample_cart(self, db=...):
        """用公开的 add 语义准备购物车：两件 P001、一件 P002。"""
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return self.tmpdir / "cart.sqlite3" if db is ... else db

    def rename_p001_in_db(self, db, name="演示笔记本", price=1500):
        """直接改库准备测试数据（不是 shop.py 提供的业务功能）。"""
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE products SET name = ?, price = ? WHERE id = ?",
                (name, price, "P001"),
            )
            conn.commit()

    def assert_catalog(self, db, expected_lines, cwd=None):
        """另起进程调用 catalog，逐行核对编号升序的目录输出与干净的错误流。"""
        result = self.run_shop(["catalog"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        expected = "\n".join(expected_lines) + "\n"
        self.assertEqual(result.stdout, expected)
        # 每行都以换行结束，且按编号升序排列
        self.assertTrue(result.stdout.endswith("\n"))
        lines = result.stdout.splitlines()
        self.assertEqual([line.split()[0] for line in lines], sorted(line.split()[0] for line in lines))
        return result

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

    def assert_cart_table_empty(self, db):
        """直接查库：catalog 只读商品表，不应生成任何购物车记录。"""
        with sqlite3.connect(str(db)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM cart").fetchone()[0]
        self.assertEqual(count, 0)

    def test_catalog_creates_fresh_db_and_lists_initial_products(self):
        """对不存在的库执行 catalog：建库、输出两行固定商品，且不碰购物车。"""
        db = self.tmpdir / "demo.sqlite3"
        self.assertFalse(db.exists())

        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        # 恰好两行，每行以换行结束，按编号升序
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n",
        )
        self.assertTrue(db.is_file())

        # 目录输出不掺杂任何购物车数量、行小计或汇总
        self.assertNotIn("数量", result.stdout)
        self.assertNotIn("小计", result.stdout)
        self.assertNotIn("总金额", result.stdout)

        # 首次查看目录后购物车仍为空：show 只有两行汇总，cart 表没有记录
        self.assert_show(db, EMPTY_CART_SHOW)
        self.assert_cart_table_empty(db)

        # 再次查看结果一致，初始化逻辑不会重复插入或改写已有商品
        self.assert_catalog(db, INITIAL_CATALOG)

    def test_catalog_respects_saved_name_and_price_and_keeps_cart(self):
        """catalog 以库中已保存的名称与整数分价格为准，重复查看不重置、不动购物车。"""
        db = self.seed_sample_cart()
        self.rename_p001_in_db(db)

        # 查看目录前先核对购物车：改名改价后行小计按新价格计算
        self.assert_show(db, RENAMED_CART_SHOW)

        # catalog 反映库内资料：P001 为演示笔记本 1500，P002 保持原值
        self.assert_catalog(db, RENAMED_CATALOG)

        # 重复查看（每次均为独立新进程）结果相同，不恢复为初始化价格
        self.assert_catalog(db, RENAMED_CATALOG)
        self.assert_catalog(db, RENAMED_CATALOG)
        with sqlite3.connect(str(db)) as conn:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
        self.assertEqual(
            products,
            [("P001", "演示笔记本", 1500), ("P002", "虚拟马克杯", 2500)],
        )

        # 查看目录后购物车数量、行小计与汇总保持不变
        self.assert_show(db, RENAMED_CART_SHOW)

    def test_catalog_with_extra_arg_is_argument_error(self):
        """catalog 多带一个商品编号：仅报参数错误退出 2，已有购物车不变。"""
        db = self.seed_sample_cart()

        result = self.run_shop(["catalog", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")

        # 失败后目录仍可正常查看，购物车内容与调用前完全一致
        self.assert_catalog(db, INITIAL_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

    def test_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：仅输出数据库不可用退出 1，无目录内容与堆栈。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["catalog"], db=directory)
        self.assert_failure(result, 1, "数据库不可用")

    def test_default_db_in_temp_cwd_lists_catalog(self):
        """省略 --db：catalog 使用当前工作目录下的 shop.sqlite3，行为一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"
        self.assertFalse(default_db.exists())

        self.assert_catalog(None, INITIAL_CATALOG, cwd=workdir)
        self.assertTrue(default_db.is_file())

        # 默认入口下 catalog 同样不生成购物车记录，重复查看结果一致
        self.assert_show(None, EMPTY_CART_SHOW, cwd=workdir)
        self.assert_cart_table_empty(default_db)
        self.assert_catalog(None, INITIAL_CATALOG, cwd=workdir)


if __name__ == "__main__":
    unittest.main()
