#!/usr/bin/env python3
"""shop.py remove 行为的回归测试。

只依赖 Python 3 标准库，在项目目录执行：

    python -m unittest discover

所有用例都在独立临时目录中使用各自的数据库文件，不接触项目或用户
已有的 shop.sqlite3；默认数据库用例通过子进程的工作目录隔离，因此
可重复执行并得到相同结果。
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"
DEFAULT_DB_NAME = "shop.sqlite3"

# 固定样例：P001 两件、P002 一件。
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400\n"
    "P002 虚拟马克杯 2500 1 2500\n"
    "总数量 3\n"
    "总金额 4900\n"
)
SHOW_AFTER_REMOVE_P001 = (
    "P002 虚拟马克杯 2500 1 2500\n"
    "总数量 1\n"
    "总金额 2500\n"
)
SHOW_EMPTY_CART = "总数量 0\n总金额 0\n"

ERR_NOT_IN_CART = "商品不在购物车"
ERR_UNKNOWN_PRODUCT = "未知商品"
ERR_ARGS = "参数错误"
ERR_DB = "数据库不可用"


class ShopTestBase(unittest.TestCase):
    """每个用例使用独立临时目录，测试结束即清理。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="shop-test-")
        self.db_path = os.path.join(self.tmpdir, "cart.sqlite3")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def run_shop(self, *args, db=None, cwd=None):
        """运行一次 shop.py，返回 CompletedProcess。

        db 为 None 时不传 --db（使用当前工作目录下的默认数据库），
        否则传入指定路径；cwd 控制子进程工作目录，用于默认数据库的
        工作目录隔离。
        """
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
            cmd += ["--db", db]
        cmd += list(args)
        return subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def run_with_db(self, *args):
        return self.run_shop(*args, db=self.db_path)

    def add(self, product_id, quantity):
        result = self.run_with_db("add", product_id, str(quantity))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def remove(self, product_id):
        return self.run_with_db("remove", product_id)

    def show(self):
        return self.run_with_db("show")

    def seed_sample(self):
        """准备固定样例：两件 P001、一件 P002。"""
        self.add("P001", 2)
        self.add("P002", 1)

    def assert_failed(
        self, result, code, message, *, success_output_allowed=False
    ):
        """断言失败结果：退出码、stdout、独占一行的 stderr。"""
        self.assertEqual(result.returncode, code, result.stderr)
        if not success_output_allowed:
            self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, f"{message}\n")
        # 不得输出异常堆栈。
        self.assertNotIn("Traceback", result.stderr)


class TestRemoveSuccess(ShopTestBase):
    """成功移除：持久化结果、全部数量、目录保留。"""

    def test_remove_deletes_whole_quantity_and_persists(self):
        self.seed_sample()

        result = self.remove("P001")
        self.assertEqual(result.returncode, 0)
        # 标准输出只有移除提示这一行，标准错误为空。
        self.assertEqual(result.stdout, "P001 已移除\n")
        self.assertEqual(result.stderr, "")

        # 重新调用 show（新进程）证明结果已经落盘：
        # 移除的是全部数量，P002 数量与金额没有变化。
        shown = self.show()
        self.assertEqual(shown.returncode, 0)
        self.assertEqual(shown.stdout, SHOW_AFTER_REMOVE_P001)

    def test_remove_last_product_leaves_empty_cart(self):
        self.seed_sample()
        self.assertEqual(self.remove("P001").returncode, 0)

        result = self.remove("P002")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P002 已移除\n")
        self.assertEqual(result.stderr, "")

        shown = self.show()
        self.assertEqual(shown.returncode, 0)
        self.assertEqual(shown.stdout, SHOW_EMPTY_CART)

    def test_readd_after_remove_succeeds_catalog_kept(self):
        self.seed_sample()
        self.assertEqual(self.remove("P001").returncode, 0)
        self.assertEqual(self.remove("P002").returncode, 0)

        # 删除购物车记录不影响商品目录，重新加入仍然成功。
        readd = self.add("P001", 1)
        self.assertEqual(readd.stdout, "P001 数量 1\n")

        shown = self.show()
        self.assertEqual(
            shown.stdout,
            "P001 虚拟笔记本 1200 1 1200\n总数量 1\n总金额 1200\n",
        )


class TestRemoveFailures(ShopTestBase):
    """失败场景：退出码 2、空 stdout、独占一行的 stderr、购物车保持一致。"""

    def assert_cart_unchanged(self, expected_show):
        """再次查看同一数据库文件，确认商品、数量和金额保持一致。"""
        shown = self.show()
        self.assertEqual(shown.returncode, 0)
        self.assertEqual(shown.stderr, "")
        self.assertEqual(shown.stdout, expected_show)

    def test_duplicate_remove_reports_not_in_cart(self):
        self.seed_sample()
        self.assertEqual(self.remove("P001").returncode, 0)

        result = self.remove("P001")
        self.assert_failed(result, 2, ERR_NOT_IN_CART)
        # 首次移除的结果保留，P002 不受影响。
        self.assert_cart_unchanged(SHOW_AFTER_REMOVE_P001)

    def test_remove_catalog_product_not_in_cart(self):
        # P002 在目录中但从未加入购物车。
        self.add("P001", 2)
        initial_show = (
            "P001 虚拟笔记本 1200 2 2400\n总数量 2\n总金额 2400\n"
        )

        result = self.remove("P002")
        self.assert_failed(result, 2, ERR_NOT_IN_CART)
        self.assert_cart_unchanged(initial_show)

    def test_remove_unknown_product_reports_unknown(self):
        self.seed_sample()

        result = self.remove("P999")
        self.assert_failed(result, 2, ERR_UNKNOWN_PRODUCT)
        self.assert_cart_unchanged(SAMPLE_SHOW)

    def test_remove_without_id_is_argument_error(self):
        self.seed_sample()

        result = self.run_with_db("remove")
        self.assert_failed(result, 2, ERR_ARGS)
        self.assert_cart_unchanged(SAMPLE_SHOW)

    def test_remove_with_extra_argument_is_argument_error(self):
        self.seed_sample()

        result = self.run_with_db("remove", "P001", "2")
        self.assert_failed(result, 2, ERR_ARGS)
        self.assert_cart_unchanged(SAMPLE_SHOW)

    def test_db_path_pointing_at_directory_is_unavailable(self):
        self.seed_sample()
        directory = os.path.join(self.tmpdir, "a-directory")
        os.mkdir(directory)

        result = self.run_shop("remove", "P001", db=directory)
        # 退出码 1：仅一行“数据库不可用”，无成功信息或异常堆栈。
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, f"{ERR_DB}\n")
        self.assertNotIn("Traceback", result.stderr)
        # 原文件购物车保持一致。
        self.assert_cart_unchanged(SAMPLE_SHOW)


class TestDatabaseEntryPoints(ShopTestBase):
    """默认数据库入口与显式 --db 入口共享同一套移除语义。"""

    def test_default_db_used_inside_working_directory(self):
        # 不传 --db：子进程在临时工作目录运行，默认文件应落在该目录，
        # 不会在项目目录或测试进程的工作目录创建 shop.sqlite3。
        default_db = os.path.join(self.tmpdir, DEFAULT_DB_NAME)
        self.assertFalse(os.path.exists(default_db))

        add = self.run_shop("add", "P001", "2", db=None, cwd=self.tmpdir)
        self.assertEqual(add.returncode, 0, add.stderr)
        self.assertTrue(os.path.isfile(default_db))

        remove = self.run_shop("remove", "P001", db=None, cwd=self.tmpdir)
        self.assertEqual(remove.returncode, 0)
        self.assertEqual(remove.stdout, "P001 已移除\n")
        self.assertEqual(remove.stderr, "")

        show = self.run_shop("show", db=None, cwd=self.tmpdir)
        self.assertEqual(show.stdout, SHOW_EMPTY_CART)

        # 默认入口同样遵循失败语义：重复移除。
        again = self.run_shop("remove", "P001", db=None, cwd=self.tmpdir)
        self.assertEqual(again.returncode, 2)
        self.assertEqual(again.stdout, "")
        self.assertEqual(again.stderr, f"{ERR_NOT_IN_CART}\n")

    def test_remove_in_one_db_does_not_affect_other_db(self):
        db_a = os.path.join(self.tmpdir, "a.sqlite3")
        db_b = os.path.join(self.tmpdir, "b.sqlite3")

        for pid, qty in (("P001", 2), ("P002", 1)):
            result = self.run_shop("add", pid, str(qty), db=db_a)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop("add", "P001", "1", db=db_b)
        self.assertEqual(result.returncode, 0, result.stderr)

        # 只在文件 A 中移除 P001。
        remove = self.run_shop("remove", "P001", db=db_a)
        self.assertEqual(remove.returncode, 0)
        self.assertEqual(remove.stdout, "P001 已移除\n")

        show_a = self.run_shop("show", db=db_a)
        self.assertEqual(show_a.stdout, SHOW_AFTER_REMOVE_P001)

        # 文件 B 的购物车完全不受影响。
        show_b = self.run_shop("show", db=db_b)
        self.assertEqual(
            show_b.stdout,
            "P001 虚拟笔记本 1200 1 1200\n总数量 1\n总金额 1200\n",
        )

        # 在文件 B 中移除 P001 同样成功，两个入口语义一致。
        remove_b = self.run_shop("remove", "P001", db=db_b)
        self.assertEqual(remove_b.returncode, 0)
        self.assertEqual(
            self.run_shop("show", db=db_b).stdout, SHOW_EMPTY_CART
        )
        # 文件 A 状态仍不随文件 B 的操作改变。
        self.assertEqual(
            self.run_shop("show", db=db_a).stdout, SHOW_AFTER_REMOVE_P001
        )


if __name__ == "__main__":
    unittest.main()
