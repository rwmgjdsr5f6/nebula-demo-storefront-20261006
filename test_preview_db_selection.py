#!/usr/bin/env python3
"""shop.py preview 命令的数据库选择隔离回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest test_preview_db_selection
    python -m unittest discover

覆盖内容：

- 每个用例在独立临时目录中准备两个已初始化的固定样例库：当前工作目录
  默认的 shop.sqlite3（两件 P001、一件 P002，保持演示价格）与显式指定的
  “另一 店铺.sqlite3”（仅一件 P001，经公开的 price 入口把单价设为
  5000 分）；
- 省略 --db 时 preview 只读取当前工作目录的默认库：商品行 P001/P002
  按演示价格计算，四行汇总为总数量 3、总金额 4900、优惠金额 0、
  应付金额 4900；
- 用 ``--db 路径`` 与 ``--db=路径`` 两种已支持形式选择另一库时，preview
  只显示该库的一件 P001（单价 5000），四行汇总为 1、5000、500、4500，
  证明商品、数量与优惠全部来自本次选定的数据库；
- 交替选择两个库并重复预览，每次结果仍分别符合各自样例；
- 选库失败不借用默认库：默认库仍有有效商品时，把显式路径指向临时目录
  中一个已存在的目录，preview 退出 1、标准输出为空、标准错误仅为
  “数据库不可用”一行并以换行结束，不出现异常堆栈或任何预览明细；
  随后重新选择两个正常库，各自预览仍得到原结果；
- 成功与失败调用前后都核对两个库保存的商品编号、名称、单价与购物车
  数量不变，也没有新增订单表或优惠状态等任何数据库对象。

所有期望文本均为本文件手写的独立字面量，不导入也不调用 shop.py 的
内部汇总函数。所有用例均在独立临时目录中运行：默认库与指定库都放在
该目录内，结束即清理，不会接触项目或用户已有的 shop.sqlite3，因此
连续执行任意次数结果都相同。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 默认库：省略 --db 时 shop.py 使用当前工作目录下的该文件
DEFAULT_DB_NAME = "shop.sqlite3"
# 指定库：文件名含空格与非 ASCII 字符，按一个完整参数传递
OTHER_DB_NAME = "另一 店铺.sqlite3"

# 默认库样例：两件 P001、一件 P002，保持演示价格。
# 商品行沿用 show/preview 的公开格式（编号 名称 单价 数量 小计），
# 按编号升序；其后依次是 preview 约定的四行汇总，标签与值之间一个
# 空格，最后一行以换行结束。
DEFAULT_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
    "优惠金额 0",
    "应付金额 4900",
)

# 指定库样例：仅一件 P001，单价经 price 改为 5000 分；总金额恰好达到
# 满减门槛，优惠 500，应付 4500。
OTHER_PREVIEW = (
    "P001 虚拟笔记本 5000 1 5000",
    "总数量 1",
    "总金额 5000",
    "优惠金额 500",
    "应付金额 4500",
)

# 两库各自应保存的商品目录与购物车记录（编号、名称、单价、数量）
DEFAULT_PRODUCTS = [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)]
DEFAULT_CART = [("P001", 2), ("P002", 1)]
OTHER_PRODUCTS = [("P001", "虚拟笔记本", 5000), ("P002", "虚拟马克杯", 2500)]
OTHER_CART = [("P001", 1)]

# 初始化后数据库中应只有两张表及其主键自动索引：
# 没有订单表，也没有保存优惠状态的表、索引、触发器或视图
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

    def run_shop(self, args, db=None, db_equals=False):
        """以子进程运行 shop.py，每次都是全新进程（等价于重启后核对）。

        db 为 None 表示不传 --db（使用当前工作目录的默认库）；否则按
        ``--db 路径`` 形式传入，db_equals 为真时改用 ``--db=路径`` 形式。
        子进程的工作目录始终是本用例的临时目录。
        """
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
            if db_equals:
                cmd.append("--db=" + str(db))
            else:
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

    def seed_dbs(self):
        """用公开的 add/price 入口准备两个固定样例库。

        默认库经省略 --db 的调用建立在临时目录的 shop.sqlite3 中：
        两件 P001、一件 P002，保持演示价格。指定库只加入一件 P001，
        并经 price 把其单价设为 5000 分。每次准备调用都核对成功输出。
        """
        for product_id, quantity in (("P001", "2"), ("P002", "1")):
            result = self.run_shop(["add", product_id, quantity])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        self.assertTrue(self.default_db.exists())

        result = self.run_shop(["add", "P001", "1"], db=self.other_db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        result = self.run_shop(["price", "P001", "5000"], db=self.other_db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "P001 单价 5000\n")

    def assert_preview(self, expected_lines, db=None, db_equals=False):
        """另起进程调用 preview，核对完整标准输出与成功状态。"""
        result = self.run_shop(["preview"], db=db, db_equals=db_equals)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        expected = "\n".join(expected_lines) + "\n"
        self.assertEqual(result.stdout, expected)
        # 最后一行以换行结束，且不存在多余空行
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout.endswith("\n\n"))
        return result

    def read_db_state(self, db):
        """读取数据库全部对象结构、商品目录与购物车原始记录。

        结构按 sqlite_master 的每个对象（表、索引、触发器、视图）逐项
        比对，因此新增订单表或优惠状态等任何对象都会被发现。
        """
        conn = sqlite3.connect(str(db))
        try:
            objects = conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY name"
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
        """核对默认库的商品目录、购物车数量与全部数据库对象。"""
        objects, products, cart = self.read_db_state(self.default_db)
        self.assertEqual(products, DEFAULT_PRODUCTS)
        self.assertEqual(cart, DEFAULT_CART)
        self.assertEqual(
            [(obj_type, name) for obj_type, name, _ in objects],
            EXPECTED_OBJECTS,
        )

    def assert_other_db_state(self):
        """核对指定库的商品目录、购物车数量与全部数据库对象。"""
        objects, products, cart = self.read_db_state(self.other_db)
        self.assertEqual(products, OTHER_PRODUCTS)
        self.assertEqual(cart, OTHER_CART)
        self.assertEqual(
            [(obj_type, name) for obj_type, name, _ in objects],
            EXPECTED_OBJECTS,
        )

    def assert_both_db_states(self):
        self.assert_default_db_state()
        self.assert_other_db_state()

    def test_default_db_preview_uses_cwd_shop_db(self):
        """省略 --db：preview 只读取当前工作目录的默认库，与指定库互不影响。"""
        self.seed_dbs()
        self.assert_both_db_states()

        # 连续两次预览，每次都独立核对完整文本（而非仅相互一致）
        self.assert_preview(DEFAULT_PREVIEW)
        self.assert_preview(DEFAULT_PREVIEW)

        # 默认库是临时目录中的 shop.sqlite3，而非指定库
        self.assert_both_db_states()

    def test_explicit_db_selection_with_space_and_equals_forms(self):
        """--db 路径与 --db=路径两种形式选择指定库，均只显示该库内容。"""
        self.seed_dbs()
        self.assert_both_db_states()

        self.assert_preview(OTHER_PREVIEW, db=self.other_db)
        self.assert_preview(OTHER_PREVIEW, db=self.other_db, db_equals=True)

        # 指定库的预览不影响默认库，两库内容各自保持样例
        self.assert_both_db_states()

    def test_alternating_selection_repeated_previews_stay_isolated(self):
        """交替选择两个库并重复预览：每次结果分别符合各自样例。"""
        self.seed_dbs()
        self.assert_both_db_states()

        for _ in range(2):
            self.assert_preview(DEFAULT_PREVIEW)
            self.assert_preview(OTHER_PREVIEW, db=self.other_db)
            self.assert_preview(OTHER_PREVIEW, db=self.other_db, db_equals=True)
            self.assert_preview(DEFAULT_PREVIEW)

        self.assert_both_db_states()

    def test_unavailable_db_does_not_borrow_default_cart(self):
        """显式路径指向已存在的目录：退出 1、无明细，不借用默认库购物车。"""
        self.seed_dbs()
        self.assert_both_db_states()
        before_default = self.read_db_state(self.default_db)
        before_other = self.read_db_state(self.other_db)

        # 已存在的目录不是可用的数据库文件
        bad_db = self.tmpdir / "不是数据库"
        bad_db.mkdir()
        self.assertTrue(bad_db.is_dir())

        result = self.run_shop(["preview"], db=bad_db)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)

        # --db=路径 形式同样只报数据库不可用，不出现任何预览明细
        result = self.run_shop(["preview"], db=bad_db, db_equals=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)

        # 失败调用没有改动任何一个正常库
        self.assertEqual(self.read_db_state(self.default_db), before_default)
        self.assertEqual(self.read_db_state(self.other_db), before_other)

        # 随后重新选择两个正常库，各自预览仍得到原结果
        self.assert_preview(DEFAULT_PREVIEW)
        self.assert_preview(OTHER_PREVIEW, db=self.other_db)
        self.assert_preview(OTHER_PREVIEW, db=self.other_db, db_equals=True)

        self.assert_both_db_states()


if __name__ == "__main__":
    unittest.main()
