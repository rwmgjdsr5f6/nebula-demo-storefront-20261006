#!/usr/bin/env python3
"""shop.py preview 在数量调整（set / decrease）后的重算回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest test_preview_quantity
    python -m unittest discover

覆盖内容：

- 固定样例（两件 P001、一件 P002，单价 1200 / 2500 分）经公开的 add
  入口准备后，初始预览为总数量 3、总金额 4900、优惠金额 0、
  应付金额 4900；
- set P002 002（前导零允许）后预览按已保存的购物车重新结算：
  总数量 4、总金额 7400，达到 5000 分满减门槛，优惠 500、应付 6900；
- decrease P002 01 后预览恢复初始结果，证明满减随当前数量重新判断；
- decrease P002 1 恰好减至零：P002 记录移除，预览只剩 P001 两件，
  总数量 2、总金额与应付金额均为 2400、优惠金额 0；
- set P001 000 后购物车为空：预览没有商品行，四行汇总均为零；
- 每个状态都核对完整商品明细与四行汇总（编号升序、单空格分隔、
  末行换行），连续预览两次分别核对同一份固定预期，并确认预览前后
  数据库中保存的商品名称、单价与购物车数量不变；
- 拒绝路径：P002 为两件时 decrease P002 3 被拒绝，标准输出为空、
  标准错误仅一行“减少数量超过购物车数量”并以换行结束、退出码 2；
  拒绝后商品资料与购物车记录保持原样，下一次预览仍与总金额 7400、
  优惠 500、应付 6900 的完整预期一致。

所有命令都显式传入同一个位于独立临时目录的数据库，每次调用都是全新
子进程（等价于重启后核对），确认结果来自持久化数据而非进程内状态。
所有期望文本均为本文件手写的独立字面量，不导入也不调用 shop.py 的
内部计算函数。临时目录结束即清理，不会接触项目或用户已有的
shop.sqlite3，因此连续执行任意次数结果都相同。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 固定样例：两件 P001、一件 P002（单价分别为初始的 1200 分和 2500 分）
SAMPLE = (("P001", "2"), ("P002", "1"))

# 初始状态（P001 两件、P002 一件）的完整 preview 输出：
# 商品行按编号升序，字段间一个空格；其后依次是四行汇总，末行换行。
INITIAL_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
    "优惠金额 0",
    "应付金额 4900",
)

# set P002 002 后（P002 两件）：2400 + 5000 = 7400，达到满减门槛，
# 优惠 500，应付 6900
SET_TWO_P002_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 2 5000",
    "总数量 4",
    "总金额 7400",
    "优惠金额 500",
    "应付金额 6900",
)

# decrease P002 1 恰好减至零后：只剩 P001 两件，低于门槛无优惠
ONLY_P001_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "总数量 2",
    "总金额 2400",
    "优惠金额 0",
    "应付金额 2400",
)

# set P001 000 后购物车为空：没有商品行，四行汇总均为零
EMPTY_PREVIEW = (
    "总数量 0",
    "总金额 0",
    "优惠金额 0",
    "应付金额 0",
)

# 各状态下数据库中应保存的商品目录（名称与单价始终保持初始值）
EXPECTED_PRODUCTS = [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)]


class PreviewQuantityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=...):
        """以子进程运行 shop.py，每次都是全新进程（等价于重启后核对）。

        db 缺省使用临时目录中的固定文件；所有用例都显式选择同一个
        数据库，确认结果来自持久化数据。
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

    def seed_sample(self):
        """用公开的 add 入口准备固定样例购物车：两件 P001、一件 P002。"""
        db = self.tmpdir / "cart.sqlite3"
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return db

    def assert_command_ok(self, result, expected_stdout):
        """核对成功调用：退出码 0、标准错误为空、标准输出逐字一致。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, expected_stdout)

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

    def assert_db_state(self, db, expected_cart):
        """核对数据库中保存的商品目录与购物车记录（按编号升序）。"""
        conn = sqlite3.connect(str(db))
        try:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(products, EXPECTED_PRODUCTS)
        self.assertEqual(cart, expected_cart)

    def assert_state(self, db, expected_lines, expected_cart):
        """核对一个状态：连续预览两次（每次独立核对固定预期），
        且预览前后保存的名称、单价和数量不变。"""
        self.assert_db_state(db, expected_cart)
        self.assert_preview(db, expected_lines)
        self.assert_db_state(db, expected_cart)
        self.assert_preview(db, expected_lines)
        self.assert_db_state(db, expected_cart)

    def test_preview_recomputes_after_set_and_decrease(self):
        """数量调整序列：每一步预览都按已保存的购物车重新结算。"""
        db = self.seed_sample()

        # 初始：总数量 3、总金额 4900、优惠 0、应付 4900
        self.assert_state(db, INITIAL_PREVIEW, [("P001", 2), ("P002", 1)])

        # set P002 002：前导零允许，保存后 P002 恰好两件
        result = self.run_shop(["set", "P002", "002"], db=db)
        self.assert_command_ok(result, "P002 数量 2\n")
        # 总金额 7400 达到满减门槛：优惠 500、应付 6900
        self.assert_state(db, SET_TWO_P002_PREVIEW, [("P001", 2), ("P002", 2)])

        # decrease P002 01：恢复一件，预览回到初始结果，
        # 证明满减随当前数量重新判断而非记住历史状态
        result = self.run_shop(["decrease", "P002", "01"], db=db)
        self.assert_command_ok(result, "P002 数量 1\n")
        self.assert_state(db, INITIAL_PREVIEW, [("P001", 2), ("P002", 1)])

        # decrease P002 1：恰好减至零，P002 记录被移除，
        # 预览只剩 P001 两件，总金额与应付均为 2400、优惠 0
        result = self.run_shop(["decrease", "P002", "1"], db=db)
        self.assert_command_ok(result, "P002 数量 0\n")
        self.assert_state(db, ONLY_P001_PREVIEW, [("P001", 2)])

        # set P001 000：记录被移除，购物车为空，
        # 预览没有商品行且四行汇总均为零
        result = self.run_shop(["set", "P001", "000"], db=db)
        self.assert_command_ok(result, "P001 数量 0\n")
        self.assert_state(db, EMPTY_PREVIEW, [])

    def test_decrease_too_much_is_rejected_and_preview_unchanged(self):
        """减少量超过当前数量被拒绝：状态不变，后续预览仍是原预期。"""
        db = self.seed_sample()

        # 先把 P002 设为两件，进入满减状态
        result = self.run_shop(["set", "P002", "002"], db=db)
        self.assert_command_ok(result, "P002 数量 2\n")
        self.assert_state(db, SET_TWO_P002_PREVIEW, [("P001", 2), ("P002", 2)])

        # decrease P002 3：减少量超过购物车数量，固定拒绝契约
        result = self.run_shop(["decrease", "P002", "3"], db=db)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "减少数量超过购物车数量\n")
        self.assertNotIn("Traceback", result.stderr)

        # 拒绝后商品资料与购物车记录保持原样，
        # 下一次预览仍与满减状态的完整预期一致
        self.assert_state(db, SET_TWO_P002_PREVIEW, [("P001", 2), ("P002", 2)])


if __name__ == "__main__":
    unittest.main()
