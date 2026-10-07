#!/usr/bin/env python3
"""shop.py add 命令数量上界的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

固定使用目录中的 P001、P002，并令 P002 始终保留一件，用来核对：
- 数量恰为上界 9223372036854775807 时加入成功（含前导零、分步累计）；
- 单次或累计越过上界时拒绝（退出码 2，仅输出“数量超出范围”）；
- 每次拒绝后购物车与商品目录保持原样；
- 编号与数量同时有问题时，数量格式/范围错误优先于“未知商品”；
- 五千位等超长数字串得到确定的范围错误而非异常堆栈；
- show 仍按编号排序、金额以整数分精确计算，总数量可以超过单件上界。

所有用例均在独立临时目录中运行：显式传入的数据库文件位于该目录，
未传 --db 时也把子进程的工作目录切到该目录，因此不会接触
项目或用户已有的 shop.sqlite3。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 数量上界：SQLite INTEGER 的最大值
MAX_QUANTITY = 9223372036854775807
OVER_MAX = MAX_QUANTITY + 1
NEAR_MAX = MAX_QUANTITY - 1

CATALOG_SHOW = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)


def cart_show(p001_quantity):
    """构造 P002 始终为一件时的完整 show 输出。

    p001_quantity 为 None 表示购物车中没有 P001。行小计、总金额按
    整数分精确计算；上界样例的总数量会达到上界加一，即允许超过
    单个商品的数量上界。
    """
    lines = []
    total_quantity = 1
    total_amount = 2500
    if p001_quantity is not None:
        subtotal = 1200 * p001_quantity
        lines.append(
            f"P001 虚拟笔记本 1200 {p001_quantity} {subtotal}"
        )
        total_quantity += p001_quantity
        total_amount += subtotal
    lines.append("P002 虚拟马克杯 2500 1 2500")
    lines.append(f"总数量 {total_quantity}")
    lines.append(f"总金额 {total_amount}")
    return tuple(lines)


# 三种固定持久化状态
NO_P001_SHOW = cart_show(None)
NEAR_MAX_SHOW = cart_show(NEAR_MAX)
AT_MAX_SHOW = cart_show(MAX_QUANTITY)


class ShopAddMaxQuantityTests(unittest.TestCase):
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

    def add_p002_one(self, db=..., cwd=None):
        """令 P002 始终保留一件：各样例的第一步固定加入一件 P002。"""
        result = self.run_shop(["add", "P002", "1"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "P002 数量 1\n")

    def seed_cart_with_p001(self, quantity_text, quantity_value,
                            db=..., cwd=None):
        """P002 一件 + 按给定文本把 P001 加到指定数值，并核对成功输出。"""
        self.add_p002_one(db=db, cwd=cwd)
        result = self.run_shop(
            ["add", "P001", quantity_text], db=db, cwd=cwd
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, f"P001 数量 {quantity_value}\n")
        return self.db_path(db) if db is ... else db

    def seed_at_max(self, db=...):
        """P002 一件、P001 恰为上界。"""
        return self.seed_cart_with_p001(
            str(MAX_QUANTITY), MAX_QUANTITY, db=db
        )

    def seed_near_max(self, db=..., cwd=None):
        """P002 一件、P001 为上界减一。"""
        return self.seed_cart_with_p001(
            str(NEAR_MAX), NEAR_MAX, db=db, cwd=cwd
        )

    def assert_show(self, db, expected_lines, cwd=None):
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_catalog_unchanged(self, db, cwd=None):
        """目录名称和单价不因数量边界处理而改变，且仍按编号排序。"""
        result = self.run_shop(["catalog"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(CATALOG_SHOW) + "\n")

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_add_max_quantity_from_empty_succeeds_and_persists(self):
        """P001 从未加入时直接加入上界：成功，落库后恰为上界。"""
        db = self.tmpdir / "max.sqlite3"
        self.add_p002_one(db=db)
        # 加入前固定前置状态：购物车只有一件 P002，P001 不存在
        self.assert_show(db, NO_P001_SHOW)

        result = self.run_shop(["add", "P001", str(MAX_QUANTITY)], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, f"P001 数量 {MAX_QUANTITY}\n")

        # 排序、整数分小计、总数量（上界+1）与总金额一并固定
        self.assert_show(db, AT_MAX_SHOW)
        self.assert_catalog_unchanged(db)

    def test_add_accumulates_to_max_in_two_steps(self):
        """独立样例：先加上界减一，再加 01，分步累计恰好到达上界。"""
        db = self.tmpdir / "steps.sqlite3"
        self.seed_near_max(db=db)
        self.assert_show(db, NEAR_MAX_SHOW)

        # 前导零按数值解释：01 与 1 等价，累计后到达上界
        result = self.run_shop(["add", "P001", "01"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, f"P001 数量 {MAX_QUANTITY}\n")

        self.assert_show(db, AT_MAX_SHOW)
        self.assert_catalog_unchanged(db)

    def test_add_one_more_at_max_is_rejected_and_persists(self):
        """已达上界后再加一件：拒绝，P001 保持上界、P002 保持一件。"""
        db = self.seed_at_max()

        result = self.run_shop(["add", "P001", "1"], db=db)
        self.assert_failure(result, 2, "数量超出范围")

        # 拒绝后重新查看：数量停留在上界，目录不变
        self.assert_show(db, AT_MAX_SHOW)
        self.assert_catalog_unchanged(db)

    def test_add_over_max_without_p001_is_rejected_and_persists(self):
        """购物车没有 P001 时直接加入上界加一：拒绝且 P001 仍不存在。"""
        db = self.tmpdir / "over.sqlite3"
        self.add_p002_one(db=db)
        self.assert_show(db, NO_P001_SHOW)

        result = self.run_shop(["add", "P001", str(OVER_MAX)], db=db)
        self.assert_failure(result, 2, "数量超出范围")

        # P001 仍不存在，仅剩一件 P002，目录不变
        self.assert_show(db, NO_P001_SHOW)
        self.assert_catalog_unchanged(db)

    def test_add_max_with_leading_zeros_succeeds(self):
        """独立样例：上界数字带前导零时首次加入仍成功。"""
        db = self.tmpdir / "zeros.sqlite3"
        self.add_p002_one(db=db)

        result = self.run_shop(
            ["add", "P001", "000" + str(MAX_QUANTITY)], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 输出按数值规范化，不带前导零
        self.assertEqual(result.stdout, f"P001 数量 {MAX_QUANTITY}\n")

        self.assert_show(db, AT_MAX_SHOW)

    def test_unknown_product_error_priority_at_boundary(self):
        """P999 在边界数量下：范围错误 > 未知商品 > 之外的格式错误顺序固定。"""
        db = self.seed_at_max()

        cases = (
            (str(OVER_MAX), "数量超出范围"),
            (str(MAX_QUANTITY), "未知商品"),
            (str(MAX_QUANTITY) + "x", "数量必须为正整数"),
        )
        for quantity, message in cases:
            with self.subTest(quantity=quantity):
                result = self.run_shop(["add", "P999", quantity], db=db)
                self.assert_failure(result, 2, message)
                # 每次失败后原购物车（P001 处于上界、P002 一件）保持不变
                self.assert_show(db, AT_MAX_SHOW)

        self.assert_catalog_unchanged(db)

    def test_five_thousand_nines_is_range_error_without_traceback(self):
        """五千个 9 组成的数量：报范围错误，不出现异常堆栈，购物车不变。"""
        db = self.tmpdir / "huge.sqlite3"
        self.add_p002_one(db=db)
        self.assert_show(db, NO_P001_SHOW)

        result = self.run_shop(["add", "P001", "9" * 5000], db=db)
        self.assert_failure(result, 2, "数量超出范围")

        # 超长数字串不能被当作格式错误，也不能让 P001 落库
        self.assert_show(db, NO_P001_SHOW)
        self.assert_catalog_unchanged(db)

    def test_default_db_accumulation_over_max_preserves_state(self):
        """默认 shop.sqlite3 入口：累计越界被拒绝，状态与越界前一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        self.seed_near_max(db=None, cwd=workdir)
        self.assertTrue(default_db.is_file())
        self.assert_show(None, NEAR_MAX_SHOW, cwd=workdir)

        # 上界减一再加两件即累计越界
        result = self.run_shop(["add", "P001", "2"], db=None, cwd=workdir)
        self.assert_failure(result, 2, "数量超出范围")

        # 默认数据库中的数量仍停在上界减一，P002 与目录不变
        self.assert_show(None, NEAR_MAX_SHOW, cwd=workdir)
        self.assert_catalog_unchanged(None, cwd=workdir)


if __name__ == "__main__":
    unittest.main()
