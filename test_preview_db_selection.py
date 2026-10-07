#!/usr/bin/env python3
"""shop.py preview 命令的数据库选择隔离回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest test_preview_db_selection
    python -m unittest discover

覆盖内容：

- 同一临时目录中准备两个已初始化的固定样例库：默认库 shop.sqlite3
  保持演示价格，购物车为两件 P001、一件 P002；另一库“另一 店铺.sqlite3”
  仅一件 P001，并经公开的 price 入口把单价改为 5000 分；
- 省略 --db 时 preview 读取当前工作目录下的 shop.sqlite3；以
  ``--db 路径`` 与 ``--db=路径`` 两种已支持形式选择另一库时，商品行、
  数量与满减汇总全部来自本次选定的数据库，互不借用；
- 交替选择两库并重复预览，结果仍分别符合各自样例；
- 选库失败不借用默认库：显式路径指向一个已存在的目录时，preview
  退出 1，标准输出为空，标准错误仅“数据库不可用”一行，无异常堆栈、
  无任何预览明细；随后重新选择两个正常库，各自预览仍得到原结果；
- 成功与失败调用前后，两库保存的商品编号、名称、单价与购物车数量
  一致，也没有新增订单或优惠状态等任何数据库对象。

所有期望文本均为本文件手写的独立字面量，不导入也不调用 shop.py 的
内部汇总函数。每个用例使用独立临时目录，默认库与指定库都放在其中，
结束即清理，不接触项目或用户已有的数据库。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 默认库文件名：不指定 --db 时 shop.py 使用当前工作目录下的该文件
DEFAULT_DB_NAME = "shop.sqlite3"

# 另一库文件名：含空格，作为单个参数整体传入
OTHER_DB_NAME = "另一 店铺.sqlite3"

# 默认库样例：演示价格，两件 P001、一件 P002。
# 商品行按编号升序，其后依次是 preview 约定的四行汇总，
# 标签与值之间一个空格，最后一行以换行结束。
DEFAULT_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
    "优惠金额 0",
    "应付金额 4900",
)

# 另一库样例：仅一件 P001，单价经 price 改为 5000 分，
# 恰好达到满减门槛，减 500 后应付 4500
OTHER_PREVIEW = (
    "P001 虚拟笔记本 5000 1 5000",
    "总数量 1",
    "总金额 5000",
    "优惠金额 500",
    "应付金额 4500",
)

# 两库应保存的商品目录与购物车内容（编号、名称、单价、数量）
DEFAULT_PRODUCTS = [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)]
DEFAULT_CART = [("P001", 2), ("P002", 1)]
OTHER_PRODUCTS = [("P001", "虚拟笔记本", 5000), ("P002", "虚拟马克杯", 2500)]
OTHER_CART = [("P001", 1)]

# 初始化后每个库只有两张表及其主键自动索引：
# 没有订单表，也没有保存优惠状态的表、索引、触发器
EXPECTED_OBJECTS = [
    ("table", "cart"),
    ("table", "products"),
    ("index", "sqlite_autoindex_cart_1"),
    ("index", "sqlite_autoindex_products_1"),
]


class ShopPreviewDbSelectionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.default_db = self.tmpdir / DEFAULT_DB_NAME
        self.other_db = self.tmpdir / OTHER_DB_NAME

    def run_shop(self, args, db=...):
        """以子进程运行 shop.py，每次都是全新进程。

        db 缺省为临时目录中的另一库；db=None 表示不传 --db，
        此时 shop.py 使用当前工作目录（即临时目录）下的 shop.sqlite3。
        """
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
            if db is ...:
                db = self.other_db
            cmd += ["--db", str(db)]
        cmd += list(args)
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"  # 强制子进程按 UTF-8 输出，结果与环境语言无关
        return subprocess.run(
            cmd,
            cwd=str(self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    def run_shop_db_equals(self, args, db):
        """以 --db=路径 形式运行 shop.py，路径与选项写在同一参数中。"""
        cmd = [sys.executable, str(SHOP), f"--db={db}"] + list(args)
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            cmd,
            cwd=str(self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    def seed_dbs(self):
        """经公开入口准备两个固定样例库。

        默认库（不传 --db，落在临时目录的 shop.sqlite3）：两件 P001、
        一件 P002，保持演示价格；另一库：一件 P001，单价改为 5000 分。
        每步都核对成功状态，保证样例本身已正确初始化。
        """
        for product_id, quantity in (("P001", "2"), ("P002", "1")):
            result = self.run_shop(["add", product_id, quantity], db=None)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")

        result = self.run_shop(["add", "P001", "1"], db=self.other_db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        result = self.run_shop(["price", "P001", "5000"], db=self.other_db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "P001 单价 5000\n")

    def assert_preview_success(self, result, expected_lines):
        """核对一次成功的 preview：退出码 0、标准错误为空、完整标准输出。

        完整输出逐行比对（含汇总标签与顺序、字段间单空格），
        末行以换行结束且不存在多余空行。
        """
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        expected = "\n".join(expected_lines) + "\n"
        self.assertEqual(result.stdout, expected)
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout.endswith("\n\n"))

    def assert_db_unavailable(self, result):
        """核对选库失败：退出码 1、标准输出为空、标准错误仅一行、无堆栈。"""
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)

    def read_db_state(self, db):
        """读取数据库全部对象结构、商品目录与购物车原始记录。

        结构按 sqlite_master 的每个对象逐项比对，因此新增订单表或
        优惠状态等任何对象都会被发现。
        """
        conn = sqlite3.connect(str(db))
        try:
            objects = conn.execute(
                "SELECT type, name FROM sqlite_master ORDER BY name"
            ).fetchall()
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        finally:
            conn.close()
        return objects, products, cart

    def assert_default_db_state(self):
        """核对默认库保存的商品资料与购物车数量，且无新增数据库对象。"""
        objects, products, cart = self.read_db_state(self.default_db)
        self.assertEqual(objects, EXPECTED_OBJECTS)
        self.assertEqual(products, DEFAULT_PRODUCTS)
        self.assertEqual(cart, DEFAULT_CART)

    def assert_other_db_state(self):
        """核对另一库保存的商品资料与购物车数量，且无新增数据库对象。"""
        objects, products, cart = self.read_db_state(self.other_db)
        self.assertEqual(objects, EXPECTED_OBJECTS)
        self.assertEqual(products, OTHER_PRODUCTS)
        self.assertEqual(cart, OTHER_CART)

    def assert_both_db_states(self):
        """成功与失败调用前后共用的两库完整状态核对。"""
        self.assert_default_db_state()
        self.assert_other_db_state()

    def test_preview_uses_default_db_when_option_omitted(self):
        """省略 --db：preview 读取当前工作目录下的 shop.sqlite3。"""
        self.seed_dbs()
        self.assert_both_db_states()

        result = self.run_shop(["preview"], db=None)
        self.assert_preview_success(result, DEFAULT_PREVIEW)

        # 只读预览不改动任何一个库
        self.assert_both_db_states()

    def test_preview_db_option_selects_other_db_in_both_forms(self):
        """--db 路径 与 --db=路径 两种形式都选中另一库，输出互不相同。"""
        self.seed_dbs()
        self.assert_both_db_states()

        result = self.run_shop(["preview"], db=self.other_db)
        self.assert_preview_success(result, OTHER_PREVIEW)

        result = self.run_shop_db_equals(["preview"], self.other_db)
        self.assert_preview_success(result, OTHER_PREVIEW)

        # 同一时刻省略 --db 仍是默认库的结果，确认两次选择互不影响
        result = self.run_shop(["preview"], db=None)
        self.assert_preview_success(result, DEFAULT_PREVIEW)

        self.assert_both_db_states()

    def test_preview_alternating_dbs_repeatedly_stays_isolated(self):
        """交替选择两库并重复预览：每次结果都分别符合各自样例。"""
        self.seed_dbs()

        for _ in range(2):
            result = self.run_shop(["preview"], db=None)
            self.assert_preview_success(result, DEFAULT_PREVIEW)
            result = self.run_shop(["preview"], db=self.other_db)
            self.assert_preview_success(result, OTHER_PREVIEW)
            result = self.run_shop_db_equals(["preview"], self.other_db)
            self.assert_preview_success(result, OTHER_PREVIEW)

        self.assert_both_db_states()

    def test_preview_db_path_to_directory_fails_without_borrowing_default(self):
        """显式路径指向已存在的目录：退出 1，不借用默认库的购物车。"""
        self.seed_dbs()
        self.assert_both_db_states()

        # 默认库仍有有效商品时，把 --db 指向临时目录中一个已存在的目录
        bad_dir = self.tmpdir / "不是数据库"
        bad_dir.mkdir()
        result = self.run_shop(["preview"], db=bad_dir)
        self.assert_db_unavailable(result)

        # --db=路径 形式同样失败，且同样不产生任何预览明细
        result = self.run_shop_db_equals(["preview"], bad_dir)
        self.assert_db_unavailable(result)

        # 失败调用不改动两个正常库
        self.assert_both_db_states()

        # 随后重新选择两个正常库，各自预览仍得到原结果
        result = self.run_shop(["preview"], db=None)
        self.assert_preview_success(result, DEFAULT_PREVIEW)
        result = self.run_shop(["preview"], db=self.other_db)
        self.assert_preview_success(result, OTHER_PREVIEW)

        self.assert_both_db_states()


if __name__ == "__main__":
    unittest.main()
