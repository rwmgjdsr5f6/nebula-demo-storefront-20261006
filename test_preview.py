#!/usr/bin/env python3
"""shop.py preview 命令的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest test_preview
    python -m unittest discover

覆盖内容：

- 正常样例（两件 P001、一件 P002）经公开的 add 入口准备后调用
  preview，核对完整商品明细与四行汇总：编号升序、总数量 3、
  总金额 4900、优惠金额 0、应付金额 4900；
- 固定满减门槛：仅一件 P002 的购物车经公开的 price 入口分别准备
  4999/5000/5001/10000 四种总金额，逐档核对优惠与应付，证明门槛
  包含等号且 10000 时优惠仍只减一次（不叠加）；
- 空购物车只有四行零汇总；P002 单价置零后仍显示一件商品和零小计；
- 纯只读：同一已初始化样例库连续预览两次，商品名称、单价、购物车
  数量、表结构均不变，也没有新增订单表或优惠状态；改价后按新价格
  重新计算，show 仍只展示优惠前金额；
- 错误路径：额外业务参数报“参数错误”（退出码 2）；数据库父目录
  不存在、购物车表缺少 quantity 列时报“数据库不可用”（退出码 1），
  标准输出为空、标准错误只有一行、不出现异常堆栈，异常样例的表结构
  与记录保持原样。

所有期望文本均为本文件手写的独立字面量，不导入也不调用 shop.py 的
内部计算函数。所有用例均在独立临时目录中运行：显式传入的数据库位于
该目录，结束即清理，不会接触项目或用户已有的 shop.sqlite3，因此连续
执行任意次数结果都相同。
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

# 正常样例的完整 preview 输出：商品行沿用 show 的公开格式
# （编号 名称 单价 数量 小计），按编号升序；其后依次是 preview 约定的
# 四行汇总，标签与值之间一个空格，最后一行以换行结束。
SAMPLE_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
    "优惠金额 0",
    "应付金额 4900",
)

# 空购物车：没有商品行，四行汇总均为零
EMPTY_PREVIEW = (
    "总数量 0",
    "总金额 0",
    "优惠金额 0",
    "应付金额 0",
)

# P002 单价置零、购物车一件时的完整输出：商品行仍在，小计为零，
# 总数量为 1，其余汇总为零
ZERO_PRICE_PREVIEW = (
    "P002 虚拟马克杯 0 1 0",
    "总数量 1",
    "总金额 0",
    "优惠金额 0",
    "应付金额 0",
)

# 门槛样例：仅一件 P002，经 price 把单价（即总金额）依次设为各值。
# 4999 低于门槛无优惠；5000 恰好达到门槛减 500；5001 同样减 500；
# 10000 远高于门槛但优惠仍只有 500（不叠加），应付 9500。
THRESHOLD_CASES = [
    ("4999", (
        "P002 虚拟马克杯 4999 1 4999",
        "总数量 1",
        "总金额 4999",
        "优惠金额 0",
        "应付金额 4999",
    )),
    ("5000", (
        "P002 虚拟马克杯 5000 1 5000",
        "总数量 1",
        "总金额 5000",
        "优惠金额 500",
        "应付金额 4500",
    )),
    ("5001", (
        "P002 虚拟马克杯 5001 1 5001",
        "总数量 1",
        "总金额 5001",
        "优惠金额 500",
        "应付金额 4501",
    )),
    ("10000", (
        "P002 虚拟马克杯 10000 1 10000",
        "总数量 1",
        "总金额 10000",
        "优惠金额 500",
        "应付金额 9500",
    )),
]

# P002 改价为 2600 后（2400 + 2600 = 5000，恰好达到门槛）的输出
REPRICED_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2600 1 2600",
    "总数量 3",
    "总金额 5000",
    "优惠金额 500",
    "应付金额 4500",
)

# 同一时刻 show 的输出：只有优惠前的两行汇总，没有优惠与应付
REPRICED_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2600 1 2600",
    "总数量 3",
    "总金额 5000",
)


def build_broken_db(path):
    """构造可正常打开但 cart 表缺少 quantity 列的异常样例数据库。

    products 表结构正常并含 P001、P002；cart 表保留 product_id 列
    和一条 P001 记录，唯独没有 quantity 列，使 preview 的明细查询
    失败。
    """
    conn = sqlite3.connect(str(path))
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
        """以子进程运行 shop.py，每次都是全新进程（等价于重启后核对）。

        db 缺省使用临时目录中的固定文件；db=None 表示不传 --db。
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
            cwd=str(self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    def seed_sample(self, db=...):
        """用公开的 add 入口准备固定样例购物车：两件 P001、一件 P002。"""
        if db is ...:
            db = self.tmpdir / "cart.sqlite3"
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return db

    def assert_preview(self, db, expected_lines):
        """另起进程调用 preview，核对完整标准输出与成功状态。"""
        result = self.run_shop(["preview"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        expected = "\n".join(expected_lines) + "\n"
        self.assertEqual(result.stdout, expected)
        # 最后一行以换行结束，且不存在多余空行
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout.endswith("\n\n"))
        return result

    def assert_show(self, db, expected_lines):
        """另起进程调用 show，核对完整标准输出与成功状态。"""
        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")
        return result

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

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
            cart = conn.execute("SELECT * FROM cart").fetchall()
        finally:
            conn.close()
        return objects, products, cart

    def test_preview_sample_cart(self):
        """正常样例：两件 P001、一件 P002，明细升序，满减前后金额逐行核对。"""
        db = self.seed_sample()

        # 连续两次预览，每次都独立核对完整文本（而非仅相互一致）
        self.assert_preview(db, SAMPLE_PREVIEW)
        self.assert_preview(db, SAMPLE_PREVIEW)

    def test_preview_threshold_boundary_and_no_stacking(self):
        """门槛含等号且优惠不叠加：4999/5000/5001/10000 四档各有明确预期。"""
        db = self.seed_sample_cart_with_single_p002()
        for price_text, expected_lines in THRESHOLD_CASES:
            with self.subTest(price=price_text):
                result = self.run_shop(["price", "P002", price_text], db=db)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "", result.stderr)
                self.assertEqual(result.stdout, f"P002 单价 {price_text}\n")
                # 每档都核对完整文本：商品行、总数量、总金额、优惠、应付
                self.assert_preview(db, expected_lines)

    def seed_sample_cart_with_single_p002(self):
        """经公开 add 入口准备仅含一件 P002 的购物车。"""
        db = self.tmpdir / "threshold.sqlite3"
        result = self.run_shop(["add", "P002", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "P002 数量 1\n")
        return db

    def test_preview_empty_cart(self):
        """空购物车：没有商品行，四行汇总均为零，重复预览结果不变。"""
        db = self.tmpdir / "empty.sqlite3"

        self.assert_preview(db, EMPTY_PREVIEW)
        self.assert_preview(db, EMPTY_PREVIEW)

        # 预览后购物车确实仍为空，商品目录已正常初始化
        _, products, cart = self.read_db_state(db)
        self.assertEqual(
            products,
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
        )
        self.assertEqual(cart, [])

    def test_preview_zero_price_item_shows_zero_subtotal(self):
        """P002 单价为零：仍显示一件商品与零小计，总数量 1，金额汇总全零。"""
        db = self.tmpdir / "zero.sqlite3"
        result = self.run_shop(["add", "P002", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(["price", "P002", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 单价 0\n")
        self.assertEqual(result.stderr, "")

        self.assert_preview(db, ZERO_PRICE_PREVIEW)

    def test_preview_is_read_only_across_repeated_runs(self):
        """连续预览两次：文本各自核对，名称/单价/数量/表结构与记录均不变。"""
        db = self.seed_sample()
        before = self.read_db_state(db)

        self.assert_preview(db, SAMPLE_PREVIEW)
        self.assert_preview(db, SAMPLE_PREVIEW)

        self.assertEqual(self.read_db_state(db), before)

        # 明确核对未被改动的内容：商品名称与单价、购物车数量
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
        self.assertEqual(
            products,
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
        )
        self.assertEqual(cart, [("P001", 2), ("P002", 1)])
        # 只有初始化创建的两张表及其主键自动索引：
        # 没有新增订单表或保存优惠状态的表、索引、触发器
        self.assertEqual(
            objects,
            [
                ("table", "cart"),
                ("table", "products"),
                ("index", "sqlite_autoindex_cart_1"),
                ("index", "sqlite_autoindex_products_1"),
            ],
        )

    def test_preview_recomputes_after_price_change_while_show_keeps_raw_total(self):
        """改 P002 单价后预览按新价计满减；show 始终只展示优惠前金额。"""
        db = self.seed_sample()

        result = self.run_shop(["price", "P002", "2600"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 单价 2600\n")
        self.assertEqual(result.stderr, "")

        # 2400 + 2600 = 5000：预览按新价格计算并减 500
        self.assert_preview(db, REPRICED_PREVIEW)
        # show 不参与满减：只有优惠前的总数量与总金额
        self.assert_show(db, REPRICED_SHOW)

        # 改回原价后预览立即恢复无优惠结果，证明每次都按当前数据重算
        result = self.run_shop(["price", "P002", "2500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 单价 2500\n")
        self.assert_preview(db, SAMPLE_PREVIEW)

    def test_preview_extra_arg_is_argument_error(self):
        """preview 多带业务参数：标准输出为空、标准错误仅参数错误一行、退出码 2。"""
        db = self.tmpdir / "args.sqlite3"
        self.assertFalse(db.exists())

        result = self.run_shop(["preview", "extra"], db=db)
        self.assert_failure(result, 2, "参数错误")
        # 参数错误先于打开数据库：数据库文件不会被创建
        self.assertFalse(db.exists())

        # 对异常样例库同样优先报参数错误，且记录保持原样
        broken = self.tmpdir / "broken.sqlite3"
        build_broken_db(broken)
        before = self.read_db_state(broken)
        result = self.run_shop(["preview", "extra"], db=broken)
        self.assert_failure(result, 2, "参数错误")
        self.assertEqual(self.read_db_state(broken), before)

    def test_preview_missing_db_directory_is_unavailable(self):
        """数据库父目录不存在：标准输出为空、标准错误仅一行、退出码 1，无堆栈。"""
        db = self.tmpdir / "no_such_dir" / "cart.sqlite3"

        result = self.run_shop(["preview"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

    def test_preview_broken_cart_schema_is_db_unavailable(self):
        """cart 缺 quantity 列：仅报数据库不可用并退出 1，表结构与记录保持原样。"""
        db = self.tmpdir / "broken.sqlite3"
        build_broken_db(db)
        before = self.read_db_state(db)

        result = self.run_shop(["preview"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

        # 再次预览仍是相同错误，不能把无法读取的购物车当作空购物车
        result = self.run_shop(["preview"], db=db)
        self.assert_failure(result, 1, "数据库不可用")

        # 表结构、商品资料与那条 P001 记录均保持不变
        self.assertEqual(self.read_db_state(db), before)
        _, products, cart = self.read_db_state(db)
        self.assertEqual(
            products,
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
        )
        self.assertEqual(cart, [("P001",)])


if __name__ == "__main__":
    unittest.main()
