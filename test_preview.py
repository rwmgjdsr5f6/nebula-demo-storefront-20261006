#!/usr/bin/env python3
"""shop.py preview 命令的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

preview 只读：不创建订单、不清空购物车、不保存优惠状态；商品行沿用
show 的格式与编号升序，其后依次输出总数量、总金额、优惠金额、应付
金额。固定满减规则：原始总金额达到 5000 分优惠 500 分，每次预览只
减一次，不叠加。

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

# 固定满减：与产品源码取值无关，仅作为独立标尺
THRESHOLD = 5000
DISCOUNT = 500

# 固定样例：两件 P001、一件 P002（总金额 4900，低于门槛，优惠为零）
SAMPLE = (("P001", "2"), ("P002", "1"))
SAMPLE_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
    "优惠金额 0",
    "应付金额 4900",
)
EMPTY_CART_PREVIEW = (
    "总数量 0",
    "总金额 0",
    "优惠金额 0",
    "应付金额 0",
)


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

    def assert_preview(self, db, expected_lines):
        """另起进程调用 preview，核对完整输出。"""
        result = self.run_shop(["preview"], db=db)
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
        """直接读取数据库的表结构、商品目录与购物车记录，用于核对未被改动。"""
        conn = sqlite3.connect(db)
        try:
            schema = conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' ORDER BY name"
            ).fetchall()
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute("SELECT * FROM cart").fetchall()
        finally:
            conn.close()
        return schema, products, cart

    def test_preview_acceptance_default_price_hits_threshold(self):
        """验收：仅两件默认单价 P002 时 2/5000/500/4500。"""
        db = self.tmpdir / "acc.sqlite3"
        result = self.run_shop(["add", "P002", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_preview(
            db,
            (
                "P002 虚拟马克杯 2500 2 5000",
                "总数量 2",
                "总金额 5000",
                "优惠金额 500",
                "应付金额 4500",
            ),
        )

    def test_preview_acceptance_below_threshold_after_price_change(self):
        """验收：P002 单价改为 2499 后 2/4998/0/4998，数量仍为 2。"""
        db = self.tmpdir / "acc.sqlite3"
        self.run_shop(["add", "P002", "2"], db=db)
        result = self.run_shop(["price", "P002", "2499"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_preview(
            db,
            (
                "P002 虚拟马克杯 2499 2 4998",
                "总数量 2",
                "总金额 4998",
                "优惠金额 0",
                "应付金额 4998",
            ),
        )

    def test_preview_empty_cart_is_four_zero_lines(self):
        """空购物车：不显示商品行，四行汇总均为零。"""
        db = self.tmpdir / "cart.sqlite3"
        self.assert_preview(db, EMPTY_CART_PREVIEW)

    def test_preview_first_use_initializes_db(self):
        """首次使用不存在的数据库：自动创建并初始化，预览显示空车。"""
        db = self.tmpdir / "fresh.sqlite3"
        self.assertFalse(db.exists())

        self.assert_preview(db, EMPTY_CART_PREVIEW)

        self.assertTrue(db.is_file())
        _, products, cart = self.read_db_state(db)
        self.assertEqual(
            products,
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
        )
        self.assertEqual(cart, [])

    def test_preview_below_threshold_gives_zero_discount(self):
        """总金额低于门槛（4900）：优惠为零，应付等于总金额。"""
        db = self.seed_sample()
        self.assert_preview(db, SAMPLE_PREVIEW)

    def test_preview_threshold_is_inclusive_and_not_stacked(self):
        """恰好 5000 减 500；跨过 10000 也只减一次，不叠加。"""
        db = self.tmpdir / "th.sqlite3"
        # 两件 P002：单价 2500 × 2 = 5000，恰好达到门槛
        self.run_shop(["add", "P002", "2"], db=db)
        self.assert_preview(
            db,
            (
                "P002 虚拟马克杯 2500 2 5000",
                "总数量 2",
                "总金额 5000",
                "优惠金额 500",
                "应付金额 4500",
            ),
        )

        # 四件 P002：10000，已越过两个门槛，仍只减一次 500
        self.run_shop(["set", "P002", "4"], db=db)
        self.assert_preview(
            db,
            (
                "P002 虚拟马克杯 2500 4 10000",
                "总数量 4",
                "总金额 10000",
                "优惠金额 500",
                "应付金额 9500",
            ),
        )

    def test_preview_uses_saved_name_and_price(self):
        """已保存的商品名称与单价以数据库内容为准，而非代码内置目录。"""
        db = self.seed_sample()
        conn = sqlite3.connect(db)
        try:
            conn.execute(
                "UPDATE products SET name = ?, price = ? WHERE id = ?",
                ("改名笔记本", 1500, "P001"),
            )
            conn.commit()
        finally:
            conn.close()

        # 总金额 1500*2 + 2500 = 5500，达到门槛
        self.assert_preview(
            db,
            (
                "P001 改名笔记本 1500 2 3000",
                "P002 虚拟马克杯 2500 1 2500",
                "总数量 3",
                "总金额 5500",
                "优惠金额 500",
                "应付金额 5000",
            ),
        )

    def test_preview_zero_price_row_shows_qty_and_zero_subtotal(self):
        """零单价商品照常显示数量与零小计，并计入总数量。"""
        db = self.seed_sample()
        result = self.run_shop(["price", "P002", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        # 总金额仅剩 P001 的 2400，低于门槛：优惠 0，应付 2400
        self.assert_preview(
            db,
            (
                "P001 虚拟笔记本 1200 2 2400",
                "P002 虚拟马克杯 0 1 0",
                "总数量 3",
                "总金额 2400",
                "优惠金额 0",
                "应付金额 2400",
            ),
        )

    def test_preview_does_not_persist_and_is_repeatable(self):
        """预览不改动商品资料与购物车，数据不变时结果一致。"""
        db = self.seed_sample()
        before = self.read_db_state(db)

        for _ in range(3):
            self.assert_preview(db, SAMPLE_PREVIEW)
            self.assertEqual(self.read_db_state(db), before)

    def test_preview_recomputes_after_quantity_change(self):
        """调量后按新内容重新计算，且不保存上次优惠状态。"""
        db = self.seed_sample()  # 4900，无优惠
        # P002 再加一件：2500*2 + 2400 = 7400，达到门槛
        result = self.run_shop(["add", "P002", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_preview(
            db,
            (
                "P001 虚拟笔记本 1200 2 2400",
                "P002 虚拟马克杯 2500 2 5000",
                "总数量 4",
                "总金额 7400",
                "优惠金额 500",
                "应付金额 6900",
            ),
        )

    def test_preview_works_with_default_db(self):
        """不传 --db 时按当前工作目录下的 shop.sqlite3 预览。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        self.run_shop(["add", "P002", "2"], db=None, cwd=workdir)

        result = self.run_shop(["preview"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            "P002 虚拟马克杯 2500 2 5000\n"
            "总数量 2\n总金额 5000\n优惠金额 500\n应付金额 4500\n",
        )

    def test_preview_broken_cart_schema_is_db_unavailable(self):
        """cart 表缺 quantity 列：仅报数据库不可用并退出 1，数据库保持原样。"""
        db = self.tmpdir / "broken.sqlite3"
        build_broken_db(db)
        before = self.read_db_state(db)

        result = self.run_shop(["preview"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

        # 重复预览返回相同错误，不能把无法读取的购物车当作空购物车
        result = self.run_shop(["preview"], db=db)
        self.assert_failure(result, 1, "数据库不可用")
        self.assertEqual(self.read_db_state(db), before)

    def test_preview_broken_default_db_is_db_unavailable(self):
        """不传 --db 时默认数据库遇到同样的读取错误：输出规则一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        build_broken_db(workdir / "shop.sqlite3")

        result = self.run_shop(["preview"], db=None, cwd=workdir)
        self.assert_failure(result, 1, "数据库不可用")

    def test_preview_with_extra_arg_is_argument_error(self):
        """preview 不接受业务参数：多带参数报参数错误，先于打开数据库。"""
        db = self.tmpdir / "broken.sqlite3"
        build_broken_db(db)

        result = self.run_shop(["preview", "extra"], db=db)
        self.assert_failure(result, 2, "参数错误")

        # 参数错误优先于数据库读取：数据库未被触碰
        _, _, cart = self.read_db_state(db)
        self.assertEqual(cart, [("P001",)])

    def test_preview_extra_arg_creates_no_db(self):
        """对尚不存在的库带额外参数：报参数错误且不创建库文件。"""
        db = self.tmpdir / "not_created.sqlite3"
        result = self.run_shop(["preview", "x"], db=db)
        self.assert_failure(result, 2, "参数错误")
        self.assertFalse(db.exists())

    def test_preview_missing_db_directory_is_unavailable(self):
        """--db 指向不存在目录中的文件：仅输出数据库不可用并退出 1。"""
        db = self.tmpdir / "no_such_dir" / "cart.sqlite3"

        result = self.run_shop(["preview"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

    def test_show_still_shows_raw_amounts_alongside_preview(self):
        """show 继续展示原始金额：与 preview 并存且不含优惠两行。"""
        db = self.seed_sample()
        # 样例总金额 4900 不达门槛，这里换成达门槛的状态验证差异
        self.run_shop(["add", "P002", "1"], db=db)

        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200 2 2400\n"
            "P002 虚拟马克杯 2500 2 5000\n"
            "总数量 4\n总金额 7400\n",
        )


if __name__ == "__main__":
    unittest.main()
