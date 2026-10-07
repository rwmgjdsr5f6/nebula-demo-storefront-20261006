#!/usr/bin/env python3
"""shop.py add 命令数量上界（SQLite INTEGER 最大值）的可重复回归测试。

只使用 Python 3 标准库与本地 SQLite；在项目目录执行：

    python -m unittest discover

所有用例均在独立临时目录中运行：显式传入的数据库文件位于该目录，
默认入口也把子进程的工作目录切到该目录，因此不会接触项目或用户
已有的 shop.sqlite3。

覆盖的边界：
- 单次加入恰好到达上界（含前导零写法）；
- 分两次累计恰好到达上界；
- 单次越界与累计越界均被拒绝且持久化状态不变；
- 数量格式、范围、未知商品三类错误的优先级；
- 五千位超长数字串得到范围错误而非异常堆栈；
- 上界数量下行小计、总金额以整数分精确计算，总数量允许超过
  单个商品的数量上界（P001 达上界时另保留一件 P002）。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

MAX_QUANTITY = 9223372036854775807
MAX_TEXT = str(MAX_QUANTITY)
OVERFLOW_TEXT = str(MAX_QUANTITY + 1)
JUST_BELOW_TEXT = str(MAX_QUANTITY - 1)

# P001 达上界、P002 保留一件时 show 的完整输出。
# 行小计与总金额以整数分精确计算；总数量为两件商品之和，
# 允许超过单个商品的数量上界。
MAX_CART_SHOW = (
    f"P001 虚拟笔记本 1200 {MAX_TEXT} {1200 * MAX_QUANTITY}",
    "P002 虚拟马克杯 2500 1 2500",
    f"总数量 {MAX_QUANTITY + 1}",
    f"总金额 {1200 * MAX_QUANTITY + 2500}",
)

# P001 为上界减一、P002 保留一件时 show 的完整输出
JUST_BELOW_CART_SHOW = (
    f"P001 虚拟笔记本 1200 {JUST_BELOW_TEXT} {1200 * (MAX_QUANTITY - 1)}",
    "P002 虚拟马克杯 2500 1 2500",
    f"总数量 {MAX_QUANTITY}",
    f"总金额 {1200 * (MAX_QUANTITY - 1) + 2500}",
)

# 购物车从无 P001、仅有一件 P002 时 show 的完整输出
P002_ONLY_SHOW = (
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 1",
    "总金额 2500",
)


class ShopAddQuantityLimitTests(unittest.TestCase):
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

    def fresh_db(self):
        """每个样例使用独立的数据库文件，返回其路径。"""
        return self.tmpdir / f"case_{len(list(self.tmpdir.iterdir()))}.sqlite3"

    def add_ok(self, db, product_id, quantity_text, expected_text):
        """断言一次 add 成功：退出码 0、标准错误为空、输出仅一行数量。"""
        result = self.run_shop(["add", product_id, quantity_text], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, f"{product_id} 数量 {expected_text}\n")
        return result

    def add_p002_one(self, db):
        """令 P002 始终保留一件。"""
        self.add_ok(db, "P002", "1", "1")

    def assert_show(self, db, expected_lines, cwd=None):
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_catalog_unchanged(self, db):
        """目录名称和单价不因数量上界相关操作而改变。"""
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n",
        )

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def assert_cart_rows(self, db, expected):
        """直接读取 SQLite 核对购物车落盘数量。"""
        conn = sqlite3.connect(db)
        try:
            rows = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
            # 数量必须以 INTEGER 类型精确保存，而不是被提升为文本/浮点
            self.assertTrue(
                all(isinstance(qty, int) for _, qty in rows),
                f"购物车数量不是 INTEGER 类型: {rows!r}",
            )
        finally:
            conn.close()
        self.assertEqual(rows, expected)

    def assert_p001_absent(self, db):
        """拒绝后 P001 仍不存在，P002 保持一件。"""
        self.assert_cart_rows(db, [("P002", 1)])
        self.assert_show(db, P002_ONLY_SHOW)
        self.assert_catalog_unchanged(db)

    def assert_p001_at(self, db, value, expected_lines):
        """拒绝后 P001 保持既有数量，P002 保持一件，目录不变。"""
        self.assert_cart_rows(db, [("P001", value), ("P002", 1)])
        self.assert_show(db, expected_lines)
        self.assert_catalog_unchanged(db)

    def test_add_exact_limit_from_empty_succeeds(self):
        """P001 从未加入时直接加入上界应成功并精确落盘。"""
        db = self.fresh_db()
        self.add_p002_one(db)

        self.add_ok(db, "P001", MAX_TEXT, MAX_TEXT)

        # P001=上界、P002=1：排序、整数分金额、总数量超过单品上界
        self.assert_p001_at(db, MAX_QUANTITY, MAX_CART_SHOW)

    def test_accumulate_to_limit_in_two_adds_succeeds(self):
        """先加上界减一，再加 01（前导零），累计恰好到达上界。"""
        db = self.fresh_db()
        self.add_p002_one(db)

        self.add_ok(db, "P001", JUST_BELOW_TEXT, JUST_BELOW_TEXT)
        self.assert_p001_at(db, MAX_QUANTITY - 1, JUST_BELOW_CART_SHOW)

        self.add_ok(db, "P001", "01", MAX_TEXT)
        self.assert_p001_at(db, MAX_QUANTITY, MAX_CART_SHOW)

    def test_add_one_more_at_limit_is_rejected_and_persists(self):
        """达到上界后再加一件应拒绝，退出码 2，购物车保持上界状态。"""
        db = self.fresh_db()
        self.add_p002_one(db)
        self.add_ok(db, "P001", MAX_TEXT, MAX_TEXT)

        result = self.run_shop(["add", "P001", "1"], db=db)
        self.assert_failure(result, 2, "数量超出范围")

        # 拒绝后重新查看：P001 保持上界数量，P002 保持一件，目录不变
        self.assert_p001_at(db, MAX_QUANTITY, MAX_CART_SHOW)

    def test_add_above_limit_without_p001_is_rejected_and_persists(self):
        """从没有 P001 的购物车加入上界加一（20 位）应拒绝，P001 不落库。"""
        db = self.fresh_db()
        self.add_p002_one(db)

        result = self.run_shop(["add", "P001", OVERFLOW_TEXT], db=db)
        self.assert_failure(result, 2, "数量超出范围")

        # 拒绝后重新查看：P001 仍不存在，P002 保持一件，目录不变
        self.assert_p001_absent(db)

    def test_accumulation_overflow_is_rejected_and_persists(self):
        """已有上界减一后加 2 件会越过上界，拒绝且数量保持上界减一。"""
        db = self.fresh_db()
        self.add_p002_one(db)
        self.add_ok(db, "P001", JUST_BELOW_TEXT, JUST_BELOW_TEXT)

        result = self.run_shop(["add", "P001", "2"], db=db)
        self.assert_failure(result, 2, "数量超出范围")

        self.assert_p001_at(db, MAX_QUANTITY - 1, JUST_BELOW_CART_SHOW)

    def test_limit_with_leading_zeros_succeeds(self):
        """独立样例：给上界添加前导零，首次加入仍按上界成功。"""
        db = self.fresh_db()
        self.add_p002_one(db)

        self.add_ok(db, "P001", "000" + MAX_TEXT, MAX_TEXT)

        self.assert_p001_at(db, MAX_QUANTITY, MAX_CART_SHOW)

    def test_above_limit_with_leading_zeros_is_rejected(self):
        """越界值携带前导零不改变范围判定，拒绝后 P001 仍不存在。"""
        db = self.fresh_db()
        self.add_p002_one(db)

        result = self.run_shop(["add", "P001", "000" + OVERFLOW_TEXT], db=db)
        self.assert_failure(result, 2, "数量超出范围")

        self.assert_p001_absent(db)

    def test_unknown_product_error_priority_at_boundary(self):
        """P999 搭配上界加一应报范围错误，搭配上界应报未知商品。"""
        # 范围错误优先于未知商品：数量本身越界时先报范围
        db = self.fresh_db()
        self.add_p002_one(db)
        result = self.run_shop(["add", "P999", OVERFLOW_TEXT], db=db)
        self.assert_failure(result, 2, "数量超出范围")
        self.assert_cart_rows(db, [("P002", 1)])

        # 数量恰为上界（范围合法）时才报未知商品
        db = self.fresh_db()
        self.add_p002_one(db)
        result = self.run_shop(["add", "P999", MAX_TEXT], db=db)
        self.assert_failure(result, 2, "未知商品")
        self.assert_cart_rows(db, [("P002", 1)])

        # 上界数字后追加 x：格式错误优先级最高
        db = self.fresh_db()
        self.add_p002_one(db)
        result = self.run_shop(["add", "P999", MAX_TEXT + "x"], db=db)
        self.assert_failure(result, 2, "数量必须为正整数")
        self.assert_cart_rows(db, [("P002", 1)])

    def test_known_product_format_error_priority_at_boundary(self):
        """P001 搭配上界数字后追加 x：报格式错误而非范围错误，状态不变。"""
        # 购物车中 P001 已达上界，格式错误必须先于累计范围判定
        db = self.fresh_db()
        self.add_p002_one(db)
        self.add_ok(db, "P001", MAX_TEXT, MAX_TEXT)

        result = self.run_shop(["add", "P001", MAX_TEXT + "x"], db=db)
        self.assert_failure(result, 2, "数量必须为正整数")
        self.assert_p001_at(db, MAX_QUANTITY, MAX_CART_SHOW)

        # 从无 P001 的购物车出发同样先报格式错误
        db = self.fresh_db()
        self.add_p002_one(db)
        result = self.run_shop(["add", "P001", OVERFLOW_TEXT + "x"], db=db)
        self.assert_failure(result, 2, "数量必须为正整数")
        self.assert_p001_absent(db)

    def test_five_thousand_nines_is_range_error_without_traceback(self):
        """五千个 9 组成的数量得到范围错误，不出现异常堆栈，购物车不变。"""
        db = self.fresh_db()
        self.add_p002_one(db)

        huge = "9" * 5000
        result = self.run_shop(["add", "P001", huge], db=db)
        self.assert_failure(result, 2, "数量超出范围")

        self.assert_p001_absent(db)

        # P999 搭配同样的超长数字：范围错误仍先于未知商品
        result = self.run_shop(["add", "P999", huge], db=db)
        self.assert_failure(result, 2, "数量超出范围")
        self.assert_cart_rows(db, [("P002", 1)])

    def test_repeated_failures_all_leave_state_unchanged(self):
        """连续多种越界/格式失败后，已有购物车始终保持不变。"""
        db = self.fresh_db()
        self.add_p002_one(db)
        self.add_ok(db, "P001", JUST_BELOW_TEXT, JUST_BELOW_TEXT)

        for quantity in (
            "2",  # 累计越界（上界减一 + 2）
            MAX_TEXT,  # 单次上界，累计同样越界
            OVERFLOW_TEXT,  # 单次越界
            "9" * 5000,  # 超长数字串
            "0",  # 格式错误
            "000" + OVERFLOW_TEXT,  # 带前导零的越界值
        ):
            with self.subTest(quantity=quantity[:20]):
                result = self.run_shop(["add", "P001", quantity], db=db)
                if quantity == "0":
                    self.assert_failure(result, 2, "数量必须为正整数")
                else:
                    self.assert_failure(result, 2, "数量超出范围")
                # 每次失败后 P001 都仍是上界减一，P002 仍是一件
                self.assert_cart_rows(
                    db, [("P001", MAX_QUANTITY - 1), ("P002", 1)]
                )

        self.assert_p001_at(db, MAX_QUANTITY - 1, JUST_BELOW_CART_SHOW)

    def test_default_db_accumulation_overflow_leaves_state_unchanged(self):
        """默认 shop.sqlite3 入口：累计越界被拒绝后状态不变。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        result = self.run_shop(["add", "P002", "1"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(
            ["add", "P001", JUST_BELOW_TEXT], db=None, cwd=workdir
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(default_db.is_file())

        # 再添 2 件会越过累计上界：拒绝
        result = self.run_shop(["add", "P001", "2"], db=None, cwd=workdir)
        self.assert_failure(result, 2, "数量超出范围")

        # 默认入口查看：P001 保持上界减一，P002 保持一件，目录不变
        self.assert_show(None, JUST_BELOW_CART_SHOW, cwd=workdir)
        self.assert_cart_rows(
            default_db, [("P001", MAX_QUANTITY - 1), ("P002", 1)]
        )


if __name__ == "__main__":
    unittest.main()
