#!/usr/bin/env python3
"""shop.py preview 命令（固定满减结算预览）的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest test_preview
    python -m unittest discover

所有用例均在独立临时目录中运行：虚构商品与 SQLite 数据库都准备在该
目录内，结束即清理，不会接触项目或用户已有的 shop.sqlite3 等店铺文件；
连续执行两次本测试得到相同结果。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 固定样例：两件 P001、一件 P002
SAMPLE = (("P001", "2"), ("P002", "1"))
SAMPLE_PRODUCTS = [
    ("P001", "虚拟笔记本", 1200),
    ("P002", "虚拟马克杯", 2500),
]
SAMPLE_CART = [("P001", 2), ("P002", 1)]
SAMPLE_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
    "优惠金额 0",
    "应付金额 4900",
)
EMPTY_CART_PREVIEW = ("总数量 0", "总金额 0", "优惠金额 0", "应付金额 0")


def build_broken_db(path):
    """构造可正常打开但 cart 表缺少 quantity 列的异常样例数据库。

    products 表结构正常并含 P001、P002；cart 表保留 product_id 列
    和一条 P001 记录，唯独没有 quantity 列，使 preview 的查询失败。
    """
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE products ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO products (id, name, price) VALUES (?, ?, ?)",
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
        )
        conn.execute("CREATE TABLE cart (product_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO cart (product_id) VALUES ('P001')")
        conn.commit()
    finally:
        conn.close()


class ShopPreviewTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=...):
        """以子进程运行 shop.py，每次都是全新进程（等价于重启后核对）。"""
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
            cwd=str(self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    def seed_sample(self, db=...):
        """用公开的 add 语义准备固定样例购物车：两件 P001、一件 P002。"""
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return self.tmpdir / "cart.sqlite3" if db is ... else db

    def set_price(self, db, product_id, price_text, saved_price):
        """用公开的 price 语义修改单价，并核对成功输出。"""
        result = self.run_shop(["price", product_id, price_text], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, f"{product_id} 单价 {saved_price}\n")

    def assert_preview(self, db, expected_lines):
        """另起进程调用 preview，核对完整输出：退出码 0、标准错误为空、
        商品行沿用 show 格式、汇总标签及顺序沿用 preview 约定、
        标签与值之间一个空格、最后一行以换行结束。"""
        result = self.run_shop(["preview"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")
        return result

    def assert_show(self, db, expected_lines):
        """另起进程调用 show，核对优惠前的原始金额展示。"""
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

    def read_db_state(self, db):
        """直接读取数据库的全部表结构、商品目录与购物车记录。"""
        conn = sqlite3.connect(str(db))
        try:
            schema = conn.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY name"
            ).fetchall()
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute("SELECT * FROM cart").fetchall()
        finally:
            conn.close()
        return schema, products, cart

    def test_preview_sample_cart(self):
        """正常场景：两件 P001、一件 P002，按编号升序显示并汇总，未达门槛。"""
        db = self.seed_sample()

        self.assert_preview(db, SAMPLE_PREVIEW)

    def test_preview_twice_is_read_only_and_keeps_state(self):
        """已初始化样例库上连续预览两次：输出一致，数据与表结构不变，
        不新增订单或优惠状态。"""
        db = self.seed_sample()
        before = self.read_db_state(db)

        self.assert_preview(db, SAMPLE_PREVIEW)
        self.assert_preview(db, SAMPLE_PREVIEW)

        schema, products, cart = self.read_db_state(db)
        # 商品名称、单价、购物车数量与已有表结构均未改变
        self.assertEqual((schema, products, cart), before)
        self.assertEqual(products, SAMPLE_PRODUCTS)
        self.assertEqual(sorted(cart), SAMPLE_CART)
        # 只有 products 与 cart 两张表：没有新增订单表或优惠状态表
        tables = sorted(name for kind, name, _ in schema if kind == "table")
        self.assertEqual(tables, ["cart", "products"])

    def test_preview_discount_threshold_boundary(self):
        """仅一件 P002：总金额 4999/5000/5001/10000 分别优惠 0/500/500/500，
        证明门槛包含等号且优惠不叠加。"""
        cases = (
            # (单价, 优惠金额, 应付金额)
            ("4999", 0, 4999),
            ("5000", 500, 4500),
            ("5001", 500, 4501),
            ("10000", 500, 9500),
        )
        for price_text, discount, payable in cases:
            with self.subTest(price=price_text):
                db = self.tmpdir / f"case_{price_text}.sqlite3"
                result = self.run_shop(["add", "P002", "1"], db=db)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "P002 数量 1\n")
                self.set_price(db, "P002", price_text, price_text)

                self.assert_preview(
                    db,
                    (
                        f"P002 虚拟马克杯 {price_text} 1 {price_text}",
                        "总数量 1",
                        f"总金额 {price_text}",
                        f"优惠金额 {discount}",
                        f"应付金额 {payable}",
                    ),
                )

    def test_preview_empty_cart(self):
        """空购物车：没有商品行，四行汇总均为零。"""
        db = self.tmpdir / "cart.sqlite3"
        self.assert_preview(db, EMPTY_CART_PREVIEW)

    def test_preview_zero_price_product(self):
        """P002 单价设为零：仍显示一件商品及零小计，总数量为 1，其余汇总为零。"""
        db = self.tmpdir / "cart.sqlite3"
        result = self.run_shop(["add", "P002", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 数量 1\n")
        self.set_price(db, "P002", "0", "0")

        self.assert_preview(
            db,
            (
                "P002 虚拟马克杯 0 1 0",
                "总数量 1",
                "总金额 0",
                "优惠金额 0",
                "应付金额 0",
            ),
        )

    def test_preview_uses_updated_price_and_show_stays_raw(self):
        """修改 P002 单价后预览按新价格计算满减；show 仍只展示优惠前金额。"""
        db = self.seed_sample()
        self.set_price(db, "P002", "3000", "3000")

        # 新总价 2400 + 3000 = 5400，达到门槛优惠 500
        self.assert_preview(
            db,
            (
                "P001 虚拟笔记本 1200 2 2400",
                "P002 虚拟马克杯 3000 1 3000",
                "总数量 3",
                "总金额 5400",
                "优惠金额 500",
                "应付金额 4900",
            ),
        )
        # show 继续只展示优惠前的原始金额，没有优惠相关行
        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 2 2400",
                "P002 虚拟马克杯 3000 1 3000",
                "总数量 3",
                "总金额 5400",
            ),
        )

    def test_preview_with_extra_arg_is_argument_error(self):
        """preview 多带业务参数：仅报参数错误并退出 2，购物车保持原样。"""
        db = self.seed_sample()
        before = self.read_db_state(db)

        result = self.run_shop(["preview", "extra"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assertEqual(self.read_db_state(db), before)

    def test_preview_missing_db_directory_is_unavailable(self):
        """--db 指向不存在目录中的文件：仅输出数据库不可用并退出 1。"""
        db = self.tmpdir / "no_such_dir" / "cart.sqlite3"

        result = self.run_shop(["preview"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

    def test_preview_broken_cart_schema_is_db_unavailable(self):
        """cart 表缺 quantity 列：仅报数据库不可用并退出 1，数据库保持原样。"""
        db = self.tmpdir / "broken.sqlite3"
        build_broken_db(db)
        before = self.read_db_state(db)

        result = self.run_shop(["preview"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

        # 再次预览返回相同错误，不能把无法读取的购物车当作空购物车
        result = self.run_shop(["preview"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

        # 异常样例的表结构、商品资料与那条 P001 记录均保持原样
        self.assertEqual(self.read_db_state(db), before)
        _, products, cart = self.read_db_state(db)
        self.assertEqual(products, SAMPLE_PRODUCTS)
        self.assertEqual(cart, [("P001",)])


if __name__ == "__main__":
    unittest.main()
