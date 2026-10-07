#!/usr/bin/env python3
"""shop.py show 汇总精度的命令行回归测试（大整数边界）。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

本文件专门验证 show 对超出单字段 INTEGER 上界的汇总值仍以完整十进制
整数精确显示：P001 与 P002 的数量、单价均到达 SQLite INTEGER 上界
9223372036854775807，此时行小计、总数量、总金额均超过该上界；
随后把 P001 单价设为零，验证零金额商品行继续存在、数量不变且仍计入
总数量。

期望结果由固定样例独立确定：所有期望数字都是本文件手写的字面量（其中
每个大数也可由两个独立的小数乘法/加法核对，例如 MAX_FIELD * MAX_FIELD
恰好等于 SUBTOTAL），既不导入也不调用 shop.py 的任何内部计算函数。

所有用例均在独立临时目录中运行：显式传入的数据库位于该目录，因此
不会接触项目或用户已有的 shop.sqlite3。每次 run_shop 都是全新进程，
对 show 的核对即等价于“重新启动命令后”核对持久化结果。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 独立手写的固定边界与期望结果：
# 单价、数量各自允许达到的 SQLite INTEGER 上界（与产品源码取值无关，
# 只作为命令行参数与库读回的独立标尺）
MAX_FIELD = 9223372036854775807
# 行小计 = 上界 * 上界（= 961 位数级别的 39 位精确整数）
SUBTOTAL = 85070591730234615847396907784232501249
# 两件商品数量之和（= 上界 + 上界）
TOTAL_QTY = 18446744073709551614
# 两个相同行小计之和
TOTAL_AMOUNT = 170141183460469231694793815568465002498
# 改价后仅剩一个行小计的总金额
TOTAL_AMOUNT_ONE = SUBTOTAL

# 准备阶段各命令成功时的标准输出（单字段仍在上界内，不涉及大汇总）
ADD_P001_OUT = f"P001 数量 {MAX_FIELD}\n"
ADD_P002_OUT = f"P002 数量 {MAX_FIELD}\n"
PRICE_P001_MAX_OUT = f"P001 单价 {MAX_FIELD}\n"
PRICE_P002_MAX_OUT = f"P002 单价 {MAX_FIELD}\n"
PRICE_P001_ZERO_OUT = "P001 单价 0\n"

# show 的完整标准输出：字段间各一个空格、行尾换行，末尾一个换行。
# 商品行按编号升序：P001 在前，P002 在后。
AT_MAX_SHOW = (
    f"P001 虚拟笔记本 {MAX_FIELD} {MAX_FIELD} {SUBTOTAL}\n"
    f"P002 虚拟马克杯 {MAX_FIELD} {MAX_FIELD} {SUBTOTAL}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_AMOUNT}\n"
)

# P001 单价置零后的完整 show 输出：
# P001 行继续存在、数量不变、小计为零；P002 明细不变；
# 总数量不变，总金额只剩一个行小计。
ZERO_PRICE_SHOW = (
    f"P001 虚拟笔记本 0 {MAX_FIELD} 0\n"
    f"P002 虚拟马克杯 {MAX_FIELD} {MAX_FIELD} {SUBTOTAL}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_AMOUNT_ONE}\n"
)


class ShopShowPrecisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.db = self.tmpdir / "precision.sqlite3"

    def run_shop(self, args):
        """以全新子进程运行 shop.py --db 临时库，返回完成的进程结果。"""
        cmd = [sys.executable, str(SHOP), "--db", str(self.db)] + list(args)
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

    def prepare_at_max(self):
        """用 add / price 命令准备“双上界”状态并逐命令核对成功结果。

        数量与单价分别经由公开 add 与 price 命令到达上界；每条准备命令
        都核对退出码 0、stderr 为空、stdout 与独立预期完全一致。
        """
        steps = (
            (["add", "P001", str(MAX_FIELD)], ADD_P001_OUT),
            (["add", "P002", str(MAX_FIELD)], ADD_P002_OUT),
            (["price", "P001", str(MAX_FIELD)], PRICE_P001_MAX_OUT),
            (["price", "P002", str(MAX_FIELD)], PRICE_P002_MAX_OUT),
        )
        for args, expected_stdout in steps:
            with self.subTest(args=args):
                result = self.run_shop(args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "", result.stderr)
                self.assertEqual(result.stdout, expected_stdout)

    def read_state(self):
        """直接读库，返回 (商品资料, 购物车记录)，供查看前后逐值比对。"""
        conn = sqlite3.connect(str(self.db))
        try:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        finally:
            conn.close()
        return products, cart

    def assert_show_twice_is_stable(self, expected_stdout):
        """在两个全新进程中连续 show 两次：完整 stdout、退出码、stderr 固定。

        同时核对查看前后商品资料与购物车记录没有变化（纯查看不产生
        任何修改）。每个 show 之前记录一次状态，之后再读一次比对。
        """
        for _ in range(2):
            before = self.read_state()
            result = self.run_shop(["show"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, expected_stdout)
            self.assertEqual(self.read_state(), before)

    def assert_int_literal_facts(self):
        """用独立算术核对本文件使用的大数字面量自身正确（不接触产品）。

        保证测试里手写的 SUBTOTAL / TOTAL_QTY / TOTAL_AMOUNT 不是笔误：
        它们应分别等于上界自乘、两个上界相加、两个行小计相加。
        """
        self.assertEqual(MAX_FIELD * MAX_FIELD, SUBTOTAL)
        self.assertEqual(MAX_FIELD + MAX_FIELD, TOTAL_QTY)
        self.assertEqual(SUBTOTAL + SUBTOTAL, TOTAL_AMOUNT)
        self.assertEqual(TOTAL_AMOUNT_ONE, SUBTOTAL)
        # 行小计、总数量、总金额确实都越过单字段 INTEGER 上界
        self.assertGreater(SUBTOTAL, MAX_FIELD)
        self.assertGreater(TOTAL_QTY, MAX_FIELD)
        self.assertGreater(TOTAL_AMOUNT, MAX_FIELD)

    def test_show_at_max_precision_then_zero_price(self):
        """双上界样例 show 精确汇总，改 P001 单价为零后零金额行仍计数。"""
        # 先独立核对本测试所依赖的固定大数字面量，避免期望值本身写错
        self.assert_int_literal_facts()

        # 用公开命令把 P001/P002 的数量与单价都准备到 INTEGER 上界
        self.prepare_at_max()

        # 准备完成后：连续两次“重启后”查看，结果一致且查看不改动数据库
        self.assert_show_twice_is_stable(AT_MAX_SHOW)

        # 直接读库：两个边界字段仍按精确整数保存，名称固定且按编号升序
        products, cart = self.read_state()
        self.assertEqual(
            products,
            [
                ("P001", "虚拟笔记本", MAX_FIELD),
                ("P002", "虚拟马克杯", MAX_FIELD),
            ],
        )
        self.assertEqual(
            cart,
            [("P001", MAX_FIELD), ("P002", MAX_FIELD)],
        )

        # 将 P001 单价设为零（这是预期的状态变化，不算查看产生的修改）
        result = self.run_shop(["price", "P001", "0"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, PRICE_P001_ZERO_OUT)

        # 改价后的新状态：同样连续重启查看两次，稳定且查看不改动数据库
        self.assert_show_twice_is_stable(ZERO_PRICE_SHOW)

        # 直接读库：P001 行仍在、数量不变、单价为零；P002 完全不变
        products, cart = self.read_state()
        self.assertEqual(
            products,
            [
                ("P001", "虚拟笔记本", 0),
                ("P002", "虚拟马克杯", MAX_FIELD),
            ],
        )
        self.assertEqual(
            cart,
            [("P001", MAX_FIELD), ("P002", MAX_FIELD)],
        )

    def test_prepared_state_persists_across_show_processes(self):
        """双上界状态在多次 show 后仍原样落库，纯 show 不写任何记录。"""
        self.prepare_at_max()
        before = self.read_state()

        # 连续三个全新进程查看（额外再做一次），记录始终与准备后一致
        for _ in range(3):
            result = self.run_shop(["show"])
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, AT_MAX_SHOW)
            self.assertEqual(self.read_state(), before)

        # 数据库目录中只有这一个独立临时库，未产生 shop.sqlite3
        files = sorted(p.name for p in self.tmpdir.iterdir())
        self.assertEqual(files, ["precision.sqlite3"])


if __name__ == "__main__":
    unittest.main()
