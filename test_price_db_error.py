#!/usr/bin/env python3
"""shop.py price 命令数据库写入被拒的可重复回归测试。

复现的问题：数据库能正常打开、catalog 与 show 仍能读取原有内容，
但针对 P001 的本次改价（UPDATE products）被数据库拒绝。
按 README 约定“任何失败操作都不会改变已有商品资料与购物车内容”，
此时应只输出一行“数据库不可用”并以退出码 1 结束，标准输出为空，
不出现成功提示、异常堆栈或底层数据库详情；重新打开数据库后，
商品编号、名称、单价与购物车记录均与失败前一致。
解除写入拒绝后，同一数据库上重试原来的改价输入应正常成功。

固定样例：P001 已保存单价 1500、购物车数量 2；P002 单价 2500、
数量 1。show 的总数量为 3、总金额为 5500。

异常样例通过给 products 表添加只针对 P001 的 RAISE(ABORT) 触发器
构造：打开数据库与任何读取都不受影响，P002 的改价也照常成功，
只有 P001 的 UPDATE 被拒绝；DROP TRIGGER 即稳定恢复可写。

只使用 Python 3 标准库与本地 SQLite；在项目目录执行：

    python -m unittest discover
    python test_price_db_error.py

所有用例均在独立临时目录中使用显式指定的数据库文件，结束即清理，
不会接触项目或用户工作目录中已有的 shop.sqlite3 等店铺文件。
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
SAMPLE_CART = [("P001", 2), ("P002", 1)]

# 样例准备完成后：P001 单价已通过 price 命令保存为 1500
PRICED_PRODUCTS = [
    ("P001", "虚拟笔记本", 1500),
    ("P002", "虚拟马克杯", 2500),
]
PRICED_CATALOG = (
    "P001 虚拟笔记本 1500",
    "P002 虚拟马克杯 2500",
)
PRICED_SHOW = (
    "P001 虚拟笔记本 1500 2 3000",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 5500",
)

# 恢复后 price P001 001800：两件 P001 小计 3600，总额 6100
PRICE_1800 = 1800
RECOVERED_1800_CATALOG = (
    "P001 虚拟笔记本 1800",
    "P002 虚拟马克杯 2500",
)
RECOVERED_1800_SHOW = (
    "P001 虚拟笔记本 1800 2 3600",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 6100",
)

# 恢复后 price P001 000：P001 仍在目录和购物车中，小计为 0，总额 2500
PRICE_ZERO = 0
RECOVERED_ZERO_CATALOG = (
    "P001 虚拟笔记本 0",
    "P002 虚拟马克杯 2500",
)
RECOVERED_ZERO_SHOW = (
    "P001 虚拟笔记本 0 2 0",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 2500",
)

# 只拒绝 P001 改价的触发器：BEFORE UPDATE 仅在旧编号为 P001 时中止。
# 数据库打开、catalog/show 读取以及 P002 的 UPDATE 都不会触发它。
REJECT_TRIGGER = (
    "CREATE TRIGGER reject_p001_product_update "
    "BEFORE UPDATE ON products WHEN OLD.id = 'P001' "
    "BEGIN SELECT RAISE(ABORT, 'reject P001 price'); END"
)
TRIGGER_NAME = "reject_p001_product_update"


class PriceDatabaseErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=...):
        """以子进程运行 shop.py，显式传入临时目录中的数据库文件。"""
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
        """用公开命令准备固定样例：P001 两件、P002 一件，P001 单价保存为 1500。"""
        db = self.tmpdir / "cart.sqlite3"
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        result = self.run_shop(["price", "P001", "001500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 单价 1500\n")
        self.assertEqual(result.stderr, "")
        return db

    def break_db(self, db):
        """把健康样例库变成异常样例：仅 P001 的改价写入被数据库拒绝。"""
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(REJECT_TRIGGER)
            conn.commit()
        finally:
            conn.close()

    def restore_db(self, db):
        """稳定解除写入拒绝：删除触发器，同一数据库恢复可写。"""
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")
            conn.commit()
        finally:
            conn.close()

    def snapshot(self, db):
        """重新打开数据库，读取失败前后必须保持一致的状态：
        表结构、商品编号/名称/单价、两条购物车记录（含数量）。
        """
        conn = sqlite3.connect(str(db))
        try:
            schema = conn.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type='table' AND name IN ('products', 'cart') "
                "ORDER BY name"
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

    def assert_db_unavailable(self, result, price_text):
        """改价被数据库拒绝：退出码 1、空标准输出、标准错误严格只有
        “数据库不可用”及末尾换行；无成功提示、异常堆栈或底层数据库详情。
        """
        label = f"price P001 {price_text}"
        self.assertEqual(
            result.returncode,
            1,
            f"{label} 被数据库拒绝时退出码应为 1，实际为 {result.returncode}；"
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        self.assertEqual(
            result.stdout,
            "",
            f"{label} 被拒时标准输出应为空，实际为 {result.stdout!r}",
        )
        self.assertEqual(
            result.stderr,
            "数据库不可用\n",
            f"{label} 被拒时标准错误应严格只有“数据库不可用”加换行，"
            f"实际为 {result.stderr!r}",
        )
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("sqlite3", result.stderr.lower())
        self.assertNotIn("reject", result.stderr.lower())
        self.assertNotIn("RAISE", result.stderr)
        self.assertNotIn("TRIGGER", result.stderr)

    def assert_show(self, db, expected_lines):
        """另起进程调用 show，核对持久化后的完整购物车明细与汇总。"""
        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(
            result.stdout,
            "\n".join(expected_lines) + "\n",
            f"show 输出与预期不符：{result.stdout!r}",
        )

    def assert_catalog(self, db, expected_lines):
        """另起进程调用 catalog，核对持久化后的完整目录输出。"""
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(
            result.stdout,
            "\n".join(expected_lines) + "\n",
            f"catalog 输出与预期不符：{result.stdout!r}",
        )

    def check_failure_then_recovery(
        self,
        price_text,
        expected_price,
        success_stdout,
        recovered_catalog,
        recovered_show,
    ):
        """异常样例上 price P001 被拒且全部状态不变；解除拒绝后重试成功。"""
        db = self.seed_sample()
        self.break_db(db)
        before = self.snapshot(db)

        # 异常样例仍能正常打开：catalog 与 show 读取到原有内容
        self.assert_catalog(db, PRICED_CATALOG)
        self.assert_show(db, PRICED_SHOW)

        # 写入拒绝只针对 P001：P002 同值改价照常成功，且不改变任何保存数据
        result = self.run_shop(["price", "P002", "2500"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 单价 2500\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            self.snapshot(db),
            before,
            "P002 改价成功后样例状态不应发生变化",
        )

        # 针对 P001 的本次改价被数据库拒绝（第一次）
        result = self.run_shop(["price", "P001", price_text], db=db)
        self.assert_db_unavailable(result, price_text)

        # 失败后重新打开数据库：表结构、商品编号/名称/单价、购物车记录
        # 均与失败前一致；P001 单价仍为 1500，数量仍为 2
        self.assertEqual(
            self.snapshot(db),
            before,
            f"price P001 {price_text} 失败后保存数据发生了变化",
        )
        _, products, cart = self.snapshot(db)
        self.assertEqual(products, PRICED_PRODUCTS)
        self.assertEqual(cart, SAMPLE_CART)

        # 另起进程通过公开入口核对：P001 仍为 1500，两条明细与汇总不变
        self.assert_catalog(db, PRICED_CATALOG)
        self.assert_show(db, PRICED_SHOW)

        # 重复同一次失败操作：结果完全相同，保存数据依旧不变
        result = self.run_shop(["price", "P001", price_text], db=db)
        self.assert_db_unavailable(result, price_text)
        self.assertEqual(
            self.snapshot(db),
            before,
            f"重复 price P001 {price_text} 失败后保存数据发生了变化",
        )
        self.assert_show(db, PRICED_SHOW)

        # 在同一个样例库上解除写入拒绝，重试原来的改价输入：正常成功
        self.restore_db(db)
        result = self.run_shop(["price", "P001", price_text], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            success_stdout,
            f"恢复后 price P001 {price_text} 的成功输出与预期不符",
        )
        self.assertTrue(
            result.stdout.endswith("\n"),
            f"成功输出应以换行结束，实际为 {result.stdout!r}",
        )

        # catalog 与 show 确认新单价已持久化，数量与汇总符合预期
        self.assert_catalog(db, recovered_catalog)
        self.assert_show(db, recovered_show)

        # 直接重新开库核对：仅 P001 单价改变，P002 资料与两条购物车数量始终不变
        _, products, cart = self.snapshot(db)
        self.assertEqual(
            products,
            [
                ("P001", "虚拟笔记本", expected_price),
                ("P002", "虚拟马克杯", 2500),
            ],
        )
        self.assertEqual(cart, SAMPLE_CART)

    def test_price_nonzero_db_error_keeps_state_and_recovers(self):
        """非零单价 001800 写入被数据库拒绝：退出码 1 且状态不变，恢复后单价 1800。"""
        self.check_failure_then_recovery(
            "001800",
            PRICE_1800,
            "P001 单价 1800\n",
            RECOVERED_1800_CATALOG,
            RECOVERED_1800_SHOW,
        )

    def test_price_zero_db_error_keeps_state_and_recovers(self):
        """零单价 000 写入被数据库拒绝：退出码 1 且状态不变，恢复后单价为 0。"""
        self.check_failure_then_recovery(
            "000",
            PRICE_ZERO,
            "P001 单价 0\n",
            RECOVERED_ZERO_CATALOG,
            RECOVERED_ZERO_SHOW,
        )


if __name__ == "__main__":
    unittest.main()
