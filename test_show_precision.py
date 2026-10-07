#!/usr/bin/env python3
"""shop.py show 命令汇总精度的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

固定样例只使用 P001、P002：通过公开的 add/price 命令把两件商品的
数量与单价都准备成 SQLite 整数上界 9223372036854775807，随后核对
show 的行小计、总数量、总金额在超过单字段存储上界时仍以完整十进制
整数精确输出；再把 P001 单价改为零，核对零金额商品仍计入总数量、
不从购物车消失。

预期输出全部在本文件中以固定字面量独立列出，不调用 shop.py 的任何
内部计算函数生成，也不经算术推导，以保证样例本身就是独立基准。

所有用例均在独立临时目录中运行：显式传入的数据库文件位于该目录，
未传 --db 时也把子进程的工作目录切到该目录，因此不会接触
项目或用户已有的 shop.sqlite3。
"""

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# SQLite INTEGER 上界：单价与单件数量字段都准备到这个值
MAX_FIELD = "9223372036854775807"
# 9223372036854775807 ** 2：行小计，39 位，远超 64 位整数上界
SUBTOTAL = "85070591730234615847396907784232501249"
# 两件商品数量之和：2 * 9223372036854775807，20 位
TOTAL_QUANTITY = "18446744073709551614"
# 两行小计之和，39 位
TOTAL_AMOUNT_FULL = "170141183460469231694793815568465002498"

P001_NAME = "虚拟笔记本"
P002_NAME = "虚拟马克杯"

# 状态一：单价、数量均为上界时 show 的完整标准输出（含字段间距与末尾换行）
STATE_A_SHOW = (
    f"P001 {P001_NAME} {MAX_FIELD} {MAX_FIELD} {SUBTOTAL}",
    f"P002 {P002_NAME} {MAX_FIELD} {MAX_FIELD} {SUBTOTAL}",
    f"总数量 {TOTAL_QUANTITY}",
    f"总金额 {TOTAL_AMOUNT_FULL}",
)

# 状态二：P001 单价改零后 show 的完整标准输出。
# P001 行继续存在、数量不变、小计为零；P002 明细不变；总数量不变，
# 总金额只剩 P002 一行小计（与 SUBTOTAL 相同的字面量）。
STATE_B_SHOW = (
    f"P001 {P001_NAME} 0 {MAX_FIELD} 0",
    f"P002 {P002_NAME} {MAX_FIELD} {MAX_FIELD} {SUBTOTAL}",
    f"总数量 {TOTAL_QUANTITY}",
    f"总金额 {SUBTOTAL}",
)

STATE_A_PRODUCTS = [
    ("P001", P001_NAME, int(MAX_FIELD)),
    ("P002", P002_NAME, int(MAX_FIELD)),
]
STATE_B_PRODUCTS = [
    ("P001", P001_NAME, 0),
    ("P002", P002_NAME, int(MAX_FIELD)),
]
# 改价不触碰购物车：两个状态下购物车记录完全相同
EXPECTED_CART = [
    ("P001", int(MAX_FIELD)),
    ("P002", int(MAX_FIELD)),
]

DECIMAL_RE = re.compile(r"[0-9]+")


class ShopShowPrecisionTests(unittest.TestCase):
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

    def db_path(self, db):
        """解析 run_shop 的 db 占位为实际路径，便于返回给调用方。"""
        return self.tmpdir / "cart.sqlite3" if db is ... else db

    def assert_command_ok(self, result, expected_stdout):
        """核对成功调用：退出码 0、标准错误为空、标准输出逐字节一致。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, expected_stdout)

    def prepare_max_cart_and_prices(self, db=..., cwd=None):
        """用公开 add/price 命令准备状态一：两件商品数量、单价均为上界。"""
        result = self.run_shop(["add", "P001", MAX_FIELD], db=db, cwd=cwd)
        self.assert_command_ok(result, f"P001 数量 {MAX_FIELD}\n")
        result = self.run_shop(["add", "P002", MAX_FIELD], db=db, cwd=cwd)
        self.assert_command_ok(result, f"P002 数量 {MAX_FIELD}\n")
        result = self.run_shop(
            ["price", "P001", MAX_FIELD], db=db, cwd=cwd
        )
        self.assert_command_ok(result, f"P001 单价 {MAX_FIELD}\n")
        result = self.run_shop(
            ["price", "P002", MAX_FIELD], db=db, cwd=cwd
        )
        self.assert_command_ok(result, f"P002 单价 {MAX_FIELD}\n")
        return self.db_path(db) if db is ... else db

    def read_db_state(self, db):
        """直接读取数据库的表结构、商品资料与购物车记录，用于核对未被改动。"""
        conn = sqlite3.connect(db)
        try:
            schema = conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' ORDER BY name"
            ).fetchall()
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        finally:
            conn.close()
        return schema, products, cart

    def assert_numeric_tokens_are_full_decimals(self, stdout):
        """所有数值字段必须是纯十进制整数：不得出现浮点/科学计数/符号写法。"""
        lines = stdout.splitlines()
        self.assertEqual(len(lines), 4)
        item_tokens = [line.split(" ") for line in lines[:2]]
        for tokens in item_tokens:
            self.assertEqual(len(tokens), 5)
            for token in tokens[2:]:  # 单价、数量、小计
                self.assertIsNotNone(
                    DECIMAL_RE.fullmatch(token), f"非完整十进制整数: {token!r}"
                )
        for line in lines[2:]:  # 两行汇总
            label, value = line.split(" ", 1)
            self.assertIn(label, ("总数量", "总金额"))
            self.assertIsNotNone(
                DECIMAL_RE.fullmatch(value), f"非完整十进制整数: {value!r}"
            )
        # 任何浮点近似都会带上小数点或指数标记
        self.assertNotIn(".", stdout)
        self.assertNotIn("e", stdout)
        self.assertNotIn("E", stdout)

    def view_twice_and_check_unchanged(
        self, db, expected_lines, expected_products, cwd=None
    ):
        """重新启动进程连续查看两次：输出一致，且查看不改动数据库。

        第一次查看前记录商品资料、购物车记录与表结构；两次 show 之后
        再记录一次并逐字节比对，证明查看动作本身不产生任何修改。
        """
        before = self.read_db_state(db)
        self.assertEqual(before[1], expected_products)
        self.assertEqual(before[2], EXPECTED_CART)

        expected_stdout = "\n".join(expected_lines) + "\n"
        for _ in range(2):
            result = self.run_shop(["show"], db=db, cwd=cwd)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, expected_stdout)
            self.assert_numeric_tokens_are_full_decimals(result.stdout)

        after = self.read_db_state(db)
        self.assertEqual(after, before)

    def test_expected_constants_themselves_exceed_sqlite_range(self):
        """样例常量本身确实越过单字段存储上界，保证测试真的覆盖精度场景。

        这里只对本文件的固定字面量做大小核对，不涉及 shop.py 任何函数。
        """
        sqlite_max = 2**63 - 1
        self.assertEqual(int(MAX_FIELD), sqlite_max)
        self.assertEqual(len(SUBTOTAL), 38)
        self.assertGreater(int(SUBTOTAL), sqlite_max)
        self.assertEqual(int(TOTAL_QUANTITY), 2 * sqlite_max)
        self.assertGreater(int(TOTAL_QUANTITY), sqlite_max)
        self.assertEqual(len(TOTAL_AMOUNT_FULL), 39)
        self.assertGreater(int(TOTAL_AMOUNT_FULL), 2**64 - 1)

    def test_show_max_price_and_quantity_totals_are_exact(self):
        """状态一：两行小计、总数量、总金额超 64 位上界仍精确显示。

        通过 add/price 把 P001、P002 的数量和单价都准备成 SQLite 整数
        上界；另起进程连续 show 两次，逐字节核对商品顺序（编号升序）、
        名称、字段间距、两行汇总与末尾换行，并确认退出码 0、标准错误
        为空、数据库不被查看动作修改。数值不得截断、绕回或浮点近似。
        """
        db = self.tmpdir / "precision.sqlite3"
        self.prepare_max_cart_and_prices(db=db)

        self.view_twice_and_check_unchanged(
            db, STATE_A_SHOW, STATE_A_PRODUCTS
        )

    def test_show_zero_price_item_still_counts_in_total_quantity(self):
        """状态二：P001 单价改零后行仍存在，总数量不变、总金额只剩 P002。

        零金额商品必须继续计入总数量，而不是从购物车消失；P002 明细
        与状态一完全一致。改价是预期的状态变化，由改价前后两组快照
        分别证明查看动作不产生额外修改。
        """
        db = self.tmpdir / "zero_price.sqlite3"
        self.prepare_max_cart_and_prices(db=db)
        self.view_twice_and_check_unchanged(
            db, STATE_A_SHOW, STATE_A_PRODUCTS
        )

        result = self.run_shop(["price", "P001", "0"], db=db)
        self.assert_command_ok(result, "P001 单价 0\n")

        # 改价后的预期变化：仅 P001 单价变零；购物车数量与 P002 均不变
        _, products, cart = self.read_db_state(db)
        self.assertEqual(products, STATE_B_PRODUCTS)
        self.assertEqual(cart, EXPECTED_CART)

        self.view_twice_and_check_unchanged(
            db, STATE_B_SHOW, STATE_B_PRODUCTS
        )

    def test_show_precision_with_default_db_location(self):
        """不传 --db 时默认 shop.sqlite3 入口下精度规则完全一致。

        工作目录切到独立临时目录，确认默认库文件就建在那里，且连续
        两次查看的完整输出与 --db 入口相同，查看前后数据库不变。
        """
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        self.prepare_max_cart_and_prices(db=None, cwd=workdir)
        self.assertTrue(default_db.is_file())

        self.view_twice_and_check_unchanged(
            default_db, STATE_A_SHOW, STATE_A_PRODUCTS, cwd=workdir
        )

        # 默认入口下改零单价：输出规则同样一致
        result = self.run_shop(
            ["price", "P001", "0"], db=None, cwd=workdir
        )
        self.assert_command_ok(result, "P001 单价 0\n")

        self.view_twice_and_check_unchanged(
            default_db, STATE_B_SHOW, STATE_B_PRODUCTS, cwd=workdir
        )


if __name__ == "__main__":
    unittest.main()
