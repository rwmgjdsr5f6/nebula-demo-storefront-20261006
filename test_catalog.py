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

# 空库首次查看目录时的完整输出：按编号升序，每行以换行结束
INITIAL_CATALOG = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)

# P001 在测试中被直接改名为「演示笔记本」、单价改为 1500 分后的目录输出
RENAMED_CATALOG = (
    "P001 演示笔记本 1500",
    "P002 虚拟马克杯 2500",
)

# 改名改价后的购物车：行小计随数据库中保存的整数分单价计算
RENAMED_CART_SHOW = (
    "P001 演示笔记本 1500 2 3000",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 5500",
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
                db = self.tmpdir / "demo.sqlite3"
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
        """用公开的 add 语义准备固定样例购物车：P001 两件、P002 一件。"""
        for product_id, quantity in (("P001", "2"), ("P002", "1")):
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return self.tmpdir / "demo.sqlite3" if db is ... else db

    def rename_product(self, db, product_id, name, price):
        """直接改库准备测试数据（不对应任何业务命令）。"""
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE products SET name = ?, price = ? WHERE id = ?",
                (name, price, product_id),
            )
            conn.commit()

    def assert_catalog(self, db, expected_lines, cwd=None):
        """另起进程调用 catalog，核对完整目录输出与干净的标准错误。"""
        result = self.run_shop(["catalog"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")
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

    def test_catalog_creates_db_and_lists_saved_products(self):
        """空路径首次查看：创建数据库，按编号升序输出两件商品，购物车字段不出现。"""
        db = self.tmpdir / "demo.sqlite3"
        self.assertFalse(db.exists())

        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "\n".join(INITIAL_CATALOG) + "\n")
        self.assertTrue(db.is_file())

        # 目录只展示编号、名称、单价：不出现购物车数量、小计或汇总字样
        for word in ("数量", "小计", "总数量", "总金额"):
            self.assertNotIn(word, result.stdout)

        # 输出恰好两行，且每行都以换行结束
        self.assertEqual(result.stdout.count("\n"), 2)

    def test_catalog_reflects_saved_name_and_price_without_touching_cart(self):
        """目录以数据库内容为准：改名改价后重复查看一致，购物车数量与小计不变。"""
        db = self.seed_sample_cart()

        # 仅准备测试数据：把已保存的 P001 改名、改整数分单价
        self.rename_product(db, "P001", "演示笔记本", 1500)

        # 查看前核对购物车：数量不被改价影响，行小计按新单价计算
        self.assert_show(db, RENAMED_CART_SHOW)

        # 首次查看：P001 显示库中保存的名称与单价，P002 保持原值
        first = self.run_shop(["catalog"], db=db)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stderr, "")
        self.assertEqual(first.stdout, "\n".join(RENAMED_CATALOG) + "\n")

        # 重复查看结果相同：不能恢复为初始化价格
        second = self.run_shop(["catalog"], db=db)
        self.assertEqual(second.stdout, first.stdout)

        # 再开一个新进程查看，结果仍相同
        self.assert_catalog(db, RENAMED_CATALOG)

        # 查看后核对购物车：目录只读，数量、行小计、汇总均不变
        self.assert_show(db, RENAMED_CART_SHOW)

    def test_catalog_on_empty_cart_creates_no_cart_rows(self):
        """首次查看目录后购物车仍为空：只有两行汇总，不生成购物车记录。"""
        db = self.tmpdir / "demo.sqlite3"

        self.assert_catalog(db, INITIAL_CATALOG)

        self.assert_show(db, EMPTY_CART_SHOW)

        # 直接查库确认商品展示没有在 cart 表留下记录
        with sqlite3.connect(str(db)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM cart").fetchone()[0]
        self.assertEqual(count, 0)

    def test_catalog_with_extra_arg_is_argument_error(self):
        """catalog 多带一个商品编号：仅报参数错误，购物车内容保持不变。"""
        db = self.seed_sample_cart()

        result = self.run_shop(["catalog", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")

        # 失败调用不触碰购物车：改价样例之外的初始购物车原样保留
        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 2 2400",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 3",
                "总金额 4900",
            ),
        )

    def test_catalog_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：仅输出数据库不可用并退出 1，无成功输出与堆栈。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["catalog"], db=directory)
        self.assert_failure(result, 1, "数据库不可用")

    def test_default_db_in_temp_cwd_lists_catalog(self):
        """不传 --db：使用临时工作目录下的 shop.sqlite3，目录行为一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"
        self.assertFalse(default_db.exists())

        self.assert_catalog(None, INITIAL_CATALOG, cwd=workdir)
        self.assertTrue(default_db.is_file())

        # 默认库中的目录查看同样只读：购物车保持为空
        self.assert_show(None, EMPTY_CART_SHOW, cwd=workdir)

        # 默认库也能正常准备购物车，目录输出与购物车互不影响
        for product_id, quantity in (("P001", "2"), ("P002", "1")):
            result = self.run_shop(
                ["add", product_id, quantity], db=None, cwd=workdir
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_catalog(None, INITIAL_CATALOG, cwd=workdir)
        self.assert_show(
            None,
            (
                "P001 虚拟笔记本 1200 2 2400",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 3",
                "总金额 4900",
            ),
            cwd=workdir,
        )


if __name__ == "__main__":
    unittest.main()
