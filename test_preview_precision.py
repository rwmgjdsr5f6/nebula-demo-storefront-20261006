#!/usr/bin/env python3
"""shop.py preview 大整数结算精度的命令行回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest test_preview_precision
    python -m unittest discover

本文件专门验证 preview 在单价与数量分别合法、而汇总值越过 SQLite
INTEGER 上界 9223372036854775807 时仍以完整十进制整数精确结算：

- 样例一（单价各 1 分）：P001、P002 数量均为上界，行小计各自等于
  上界（仍合法），只有总数量与总金额越界；总金额达到满减门槛，
  优惠 500，应付为总金额减 500；
- 样例二（单价各 2 分）：行小计本身即越界，总数量不变，总金额与
  应付同步越界，优惠仍只减一次 500。

两个样例都经公开的 add 与 price 命令准备，随后各在两个独立的命令
进程中预览一次，每次都与本文件手写的固定完整预期逐字节比较（不以
两次输出一致代替正确性校验）；预览前后直接读库比对商品资料、购物车
记录与全部数据库对象结构，确认预览不保存优惠状态、不创建订单、不改
数量。金额必须保持完整十进制整数：不因汇总越界报“数量超出范围”或
“数据库不可用”，也不出现小数、科学记数法、截断或异常堆栈。

所有期望数字均为本文件手写的独立字面量（每个大数也可由两个独立的
小数乘法/加法核对），既不导入也不调用 shop.py 的任何内部计算函数。
所有用例均在独立临时目录中运行：显式传入的数据库位于该目录，结束即
清理，不会接触项目或用户已有的 shop.sqlite3，因此连续执行任意次数
结果都相同。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 独立手写的固定边界与期望结果（与产品源码取值无关，只作为命令行
# 参数与库读回的独立标尺）：
# 数量：两个样例都把 P001、P002 准备到 SQLite INTEGER 上界
MAX_QTY = 9223372036854775807
# 总数量 = 上界 + 上界（越界，两个样例相同）
TOTAL_QTY = 18446744073709551614

# 样例一：单价各 1 分。行小计 = 1 * 上界 = 上界（单字段仍合法），
# 只有汇总越界：总金额 = 上界 + 上界；达到满减门槛，优惠 500。
PRICE_ONE = 1
SUBTOTAL_ONE = 9223372036854775807
TOTAL_ONE = 18446744073709551614
PAYABLE_ONE = 18446744073709551114

# 样例二：单价各 2 分。行小计 = 2 * 上界（行小计本身越界），
# 总金额 = 两个行小计之和 = 4 * 上界；优惠仍只减一次 500。
PRICE_TWO = 2
SUBTOTAL_TWO = 18446744073709551614
TOTAL_TWO = 36893488147419103228
PAYABLE_TWO = 36893488147419102728

# 固定满减：优惠金额在两个样例中都恰好是 500
DISCOUNT = 500

# 样例一 preview 的完整标准输出：商品行按编号升序，字段间各一个
# 空格；其后依次是总数量、总金额、优惠金额、应付金额四行汇总，
# 最后一行以换行结束。
PREVIEW_PRICE_ONE = (
    f"P001 虚拟笔记本 {PRICE_ONE} {MAX_QTY} {SUBTOTAL_ONE}\n"
    f"P002 虚拟马克杯 {PRICE_ONE} {MAX_QTY} {SUBTOTAL_ONE}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_ONE}\n"
    f"优惠金额 {DISCOUNT}\n"
    f"应付金额 {PAYABLE_ONE}\n"
)

# 样例二 preview 的完整标准输出：格式与样例一完全一致
PREVIEW_PRICE_TWO = (
    f"P001 虚拟笔记本 {PRICE_TWO} {MAX_QTY} {SUBTOTAL_TWO}\n"
    f"P002 虚拟马克杯 {PRICE_TWO} {MAX_QTY} {SUBTOTAL_TWO}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_TWO}\n"
    f"优惠金额 {DISCOUNT}\n"
    f"应付金额 {PAYABLE_TWO}\n"
)

# 同一时刻 show 的输出：只展示优惠前的总数量与总金额，没有优惠与
# 应付两行（show 不参与满减）
SHOW_PRICE_ONE = (
    f"P001 虚拟笔记本 {PRICE_ONE} {MAX_QTY} {SUBTOTAL_ONE}\n"
    f"P002 虚拟马克杯 {PRICE_ONE} {MAX_QTY} {SUBTOTAL_ONE}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_ONE}\n"
)
SHOW_PRICE_TWO = (
    f"P001 虚拟笔记本 {PRICE_TWO} {MAX_QTY} {SUBTOTAL_TWO}\n"
    f"P002 虚拟马克杯 {PRICE_TWO} {MAX_QTY} {SUBTOTAL_TWO}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_TWO}\n"
)


class ShopPreviewPrecisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db):
        """以全新子进程运行 shop.py --db 指定临时库，返回完成的进程结果。"""
        cmd = [sys.executable, str(SHOP), "--db", str(db)] + list(args)
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"  # 强制子进程按 UTF-8 输出，结果与环境语言无关
        return subprocess.run(
            cmd,
            cwd=str(self.tmpdir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

    def prepare_sample(self, db, price):
        """用公开的 add / price 命令准备样例并逐命令核对成功结果。

        P001、P002 的数量都经 add 到达上界，单价都经 price 设为同一
        给定值；每条准备命令都核对退出码 0、stderr 为空、stdout 与
        独立预期完全一致。
        """
        steps = (
            (["add", "P001", str(MAX_QTY)], f"P001 数量 {MAX_QTY}\n"),
            (["add", "P002", str(MAX_QTY)], f"P002 数量 {MAX_QTY}\n"),
            (["price", "P001", str(price)], f"P001 单价 {price}\n"),
            (["price", "P002", str(price)], f"P002 单价 {price}\n"),
        )
        for args, expected_stdout in steps:
            with self.subTest(args=args):
                result = self.run_shop(args, db)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "", result.stderr)
                self.assertEqual(result.stdout, expected_stdout)

    def read_db_state(self, db):
        """直接读库，返回 (数据库对象结构, 商品资料, 购物车记录)。

        结构按 sqlite_master 的每个对象（表、索引、触发器、视图）逐项
        比对，因此预览若新增订单表或保存优惠状态的任何对象都会被发现。
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

    def assert_preview_exact(self, db, expected_stdout):
        """在一个全新进程中预览，核对完整标准输出与成功状态。

        退出码 0、stderr 为空；stdout 与固定预期逐字节一致，因此不会
        出现小数、科学记数法、截断或异常堆栈，也不会把汇总越界误报成
        “数量超出范围”或“数据库不可用”。
        """
        result = self.run_shop(["preview"], db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, expected_stdout)
        # 大数均以完整十进制整数出现：输出中没有小数点或科学记数法标记
        self.assertNotIn(".", result.stdout)
        self.assertNotIn("e", result.stdout)
        self.assertNotIn("E", result.stdout)
        self.assertNotIn("Traceback", result.stderr)
        # 最后一行以换行结束，且不存在多余空行
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertFalse(result.stdout.endswith("\n\n"))
        return result

    def assert_preview_twice_read_only(self, db, expected_stdout, price):
        """在两个独立命令进程中各预览一次，每次都独立核对固定预期。

        预览前后直接读库比对：商品名称与单价、购物车数量、全部数据库
        对象结构均不变——预览不保存优惠状态、不创建订单、不改数量。
        """
        for _ in range(2):
            before = self.read_db_state(db)
            self.assert_preview_exact(db, expected_stdout)
            self.assertEqual(self.read_db_state(db), before)

        # 明确核对未被改动的内容：单价为样例给定值，数量仍为上界
        objects, products, cart = self.read_db_state(db)
        self.assertEqual(
            products,
            [
                ("P001", "虚拟笔记本", price),
                ("P002", "虚拟马克杯", price),
            ],
        )
        self.assertEqual(cart, [("P001", MAX_QTY), ("P002", MAX_QTY)])
        # 只有初始化创建的两张表及其主键自动索引：没有订单表或优惠状态
        self.assertEqual(
            [(obj_type, name) for obj_type, name, _ in objects],
            [
                ("table", "cart"),
                ("table", "products"),
                ("index", "sqlite_autoindex_cart_1"),
                ("index", "sqlite_autoindex_products_1"),
            ],
        )

    def assert_int_literal_facts(self):
        """用独立算术核对本文件手写的大数字面量自身正确（不接触产品）。

        保证 SUBTOTAL / TOTAL / PAYABLE 不是笔误：它们应分别等于
        单价乘上界、两个行小计相加、总金额减固定优惠 500。
        """
        # 样例一：行小计仍是合法单字段，只有汇总越界
        self.assertEqual(PRICE_ONE * MAX_QTY, SUBTOTAL_ONE)
        self.assertEqual(SUBTOTAL_ONE + SUBTOTAL_ONE, TOTAL_ONE)
        self.assertEqual(MAX_QTY + MAX_QTY, TOTAL_QTY)
        self.assertEqual(TOTAL_ONE - DISCOUNT, PAYABLE_ONE)
        self.assertLessEqual(SUBTOTAL_ONE, MAX_QTY)
        self.assertGreater(TOTAL_ONE, MAX_QTY)
        self.assertGreater(TOTAL_QTY, MAX_QTY)

        # 样例二：行小计与汇总同时越界
        self.assertEqual(PRICE_TWO * MAX_QTY, SUBTOTAL_TWO)
        self.assertEqual(SUBTOTAL_TWO + SUBTOTAL_TWO, TOTAL_TWO)
        self.assertEqual(TOTAL_TWO - DISCOUNT, PAYABLE_TWO)
        self.assertGreater(SUBTOTAL_TWO, MAX_QTY)
        self.assertGreater(TOTAL_TWO, MAX_QTY)

        # 两个样例的总金额都达到满减门槛，优惠各只减一次 500
        self.assertGreaterEqual(TOTAL_ONE, 5000)
        self.assertGreaterEqual(TOTAL_TWO, 5000)

    def test_preview_only_total_overflows(self):
        """单价各 1 分：行小计合法、仅汇总越界，preview 精确结算满减。"""
        self.assert_int_literal_facts()
        db = self.tmpdir / "only_total.sqlite3"
        self.prepare_sample(db, PRICE_ONE)

        self.assert_preview_twice_read_only(db, PREVIEW_PRICE_ONE, PRICE_ONE)

        # show 仍只展示优惠前金额：没有优惠金额与应付金额两行
        result = self.run_shop(["show"], db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, SHOW_PRICE_ONE)

    def test_preview_subtotal_and_total_overflow(self):
        """单价各 2 分：行小计与汇总同时越界，preview 精确结算满减。"""
        self.assert_int_literal_facts()
        db = self.tmpdir / "subtotal_total.sqlite3"
        self.prepare_sample(db, PRICE_TWO)

        self.assert_preview_twice_read_only(db, PREVIEW_PRICE_TWO, PRICE_TWO)

        # show 仍只展示优惠前金额：没有优惠金额与应付金额两行
        result = self.run_shop(["show"], db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, SHOW_PRICE_TWO)

    def test_preview_repeated_runs_leave_only_temp_db(self):
        """重复预览不产生额外文件：临时目录中只有显式传入的独立临时库。"""
        db = self.tmpdir / "repeat.sqlite3"
        self.prepare_sample(db, PRICE_ONE)
        for _ in range(3):
            self.assert_preview_exact(db, PREVIEW_PRICE_ONE)
        files = sorted(p.name for p in self.tmpdir.iterdir())
        self.assertEqual(files, ["repeat.sqlite3"])


if __name__ == "__main__":
    unittest.main()
