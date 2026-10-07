#!/usr/bin/env python3
"""shop.py preview 在数量调整（set / decrease）后的重结算回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest test_preview_quantity
    python -m unittest discover

覆盖内容：

- 固定样例（两件 P001、一件 P002，单价 1200 / 2500 分）经公开的 add
  入口准备后，初始预览为总数量 3、总金额 4900、优惠金额 0、应付 4900；
- set P002 002（前导零）后预览按新数量重算：总数量 4、总金额 7400、
  达到满减门槛，优惠金额 500、应付金额 6900；
- decrease P002 01 后预览恢复初始结果，证明满减随当前数量重新判断；
- decrease P002 1（减至零）后预览只剩 P001 两件：总数量 2、总金额与
  应付金额均为 2400、优惠金额 0；
- set P001 000 后预览没有商品行，四行汇总均为零；
- P002 为两件时 decrease P002 3 被拒绝：标准输出为空、标准错误仅一行
  “减少数量超过购物车数量”、退出码 2，商品资料与购物车记录保持原样，
  下一次预览仍与 7400 / 500 / 6900 的完整预期一致；
- 每个状态都连续预览两次并分别核对同一份固定预期，且预览前后数据库中
  保存的商品名称、单价与购物车数量不变（preview 纯只读）。

所有命令都显式传入同一个 --db 并以全新子进程执行，确认结果来自持久化
数据而非进程内状态。所有期望文本均为本文件手写的独立字面量，不导入也
不调用 shop.py 的内部计算函数。所有用例均在独立临时目录中运行：显式
传入的数据库位于该目录，结束即清理，不会接触项目或用户已有的
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

# 固定样例：两件 P001、一件 P002（初始单价 1200 / 2500 分）
SAMPLE = (("P001", "2"), ("P002", "1"))

# 初始状态（也是 decrease P002 01 之后恢复的状态）：
# 商品行沿用 show 的公开格式（编号 名称 单价 数量 小计），按编号升序；
# 其后依次是 preview 约定的四行汇总，标签与值之间一个空格。
INITIAL_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
    "优惠金额 0",
    "应付金额 4900",
)

# set P002 002 之后：P002 两件，2400 + 5000 = 7400 达到满减门槛
P002_TWO_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 2 5000",
    "总数量 4",
    "总金额 7400",
    "优惠金额 500",
    "应付金额 6900",
)

# decrease P002 1（减至零）之后：只剩 P001 两件，低于门槛无优惠
ONLY_P001_PREVIEW = (
    "P001 虚拟笔记本 1200 2 2400",
    "总数量 2",
    "总金额 2400",
    "优惠金额 0",
    "应付金额 2400",
)

# set P001 000 之后：购物车为空，没有商品行，四行汇总均为零
EMPTY_PREVIEW = (
    "总数量 0",
    "总金额 0",
    "优惠金额 0",
    "应付金额 0",
)

# 样例全程不变的商品目录（名称与单价始终保持初始值）
SAMPLE_PRODUCTS = [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)]


class PreviewQuantityTests(unittest.TestCase):
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

    def seed_sample(self):
        """用公开的 add 入口准备固定样例购物车：两件 P001、一件 P002。"""
        db = self.tmpdir / "cart.sqlite3"
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return db

    def assert_command_ok(self, args, db, expected_stdout):
        """核对一次成功调用：退出码 0、标准错误为空、输出独占一行。"""
        result = self.run_shop(args, db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, expected_stdout)
        return result

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

    def read_saved_state(self, db):
        """读取数据库中保存的商品目录与购物车数量（按编号升序）。"""
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
        return products, cart

    def assert_state(self, db, expected_lines, expected_cart):
        """核对一个状态：连续预览两次（各自核对固定预期），且预览前后
        保存的商品名称、单价与购物车数量不变。"""
        before = self.read_saved_state(db)
        self.assert_preview(db, expected_lines)
        self.assert_preview(db, expected_lines)
        after = self.read_saved_state(db)
        self.assertEqual(after, before)
        products, cart = after
        self.assertEqual(products, SAMPLE_PRODUCTS)
        self.assertEqual(cart, expected_cart)

    def test_preview_recalculates_after_set_and_decrease(self):
        """数量调整主流程：每个状态的预览都按当前购物车重新结算。"""
        db = self.seed_sample()

        # 初始：两件 P001、一件 P002，总金额 4900 低于门槛，无优惠
        self.assert_state(db, INITIAL_PREVIEW, [("P001", 2), ("P002", 1)])

        # set P002 002：前导零允许，保存后恰好两件；7400 达到门槛减 500
        self.assert_command_ok(["set", "P002", "002"], db, "P002 数量 2\n")
        self.assert_state(db, P002_TWO_PREVIEW, [("P001", 2), ("P002", 2)])

        # decrease P002 01：回到一件，预览恢复初始结果，
        # 证明满减随当前数量重新判断而非沿用旧状态
        self.assert_command_ok(["decrease", "P002", "01"], db, "P002 数量 1\n")
        self.assert_state(db, INITIAL_PREVIEW, [("P001", 2), ("P002", 1)])

        # decrease P002 1：恰好减至零，记录被移除，只剩 P001 两件
        self.assert_command_ok(["decrease", "P002", "1"], db, "P002 数量 0\n")
        self.assert_state(db, ONLY_P001_PREVIEW, [("P001", 2)])

        # set P001 000：目标为零同样移除记录，预览没有商品行、汇总全零
        self.assert_command_ok(["set", "P001", "000"], db, "P001 数量 0\n")
        self.assert_state(db, EMPTY_PREVIEW, [])

    def test_decrease_beyond_quantity_rejected_and_preview_unchanged(self):
        """P002 两件时 decrease P002 3 被拒绝，数据与后续预览均不变。"""
        db = self.seed_sample()
        self.assert_command_ok(["set", "P002", "002"], db, "P002 数量 2\n")
        before = self.read_saved_state(db)

        result = self.run_shop(["decrease", "P002", "3"], db=db)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "减少数量超过购物车数量\n")
        self.assertNotIn("Traceback", result.stderr)

        # 拒绝后商品资料与购物车记录保持原样
        self.assertEqual(self.read_saved_state(db), before)

        # 下一次预览仍与两件状态的完整预期一致
        self.assert_state(db, P002_TWO_PREVIEW, [("P001", 2), ("P002", 2)])


if __name__ == "__main__":
    unittest.main()
