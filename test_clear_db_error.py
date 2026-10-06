#!/usr/bin/env python3
"""shop.py clear 命令数据库删除被拒绝的可重复回归测试。

复现的问题：数据库能正常打开、show 与 catalog 仍能读取原有内容，
但 clear 的 DELETE 被数据库拒绝（含已删除部分记录后才失败的情形，
不能用打不开文件代替）。按 README 约定“任何失败操作都不会改变
已有购物车内容”，此时应只输出一行“数据库不可用”并以退出码 1 结束，
且不改变表结构、商品目录与原有购物车记录；解除删除拒绝后，
同一数据库上的 clear 仍可正常完成。

异常样例通过给 cart 表添加只针对 P002 的 RAISE(ABORT) 触发器构造：
DELETE FROM cart 按插入顺序先删 P001、删到 P002 时才被拒绝，
因此覆盖“已删除部分记录后才失败”的情形；读取不受影响，
DROP TRIGGER 即恢复正常。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

所有用例均在独立临时目录中运行，不会接触项目或用户已有的店铺文件。
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
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)
EMPTY_CART_SHOW = ("总数量 0", "总金额 0")
CATALOG_OUTPUT = "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n"

# 只拒绝删到 P002 的触发器：clear 的 DELETE 先删掉 P001，
# 删到 P002 时才被 RAISE(ABORT) 拒绝，覆盖部分删除后失败的情形
REJECT_TRIGGER = (
    "CREATE TRIGGER reject_cart_clear "
    "BEFORE DELETE ON cart WHEN OLD.product_id = 'P002' "
    "BEGIN SELECT RAISE(ABORT, 'reject clear'); END"
)
TRIGGER_NAME = "reject_cart_clear"


class ClearDatabaseErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py（db=None 表示不传 --db）。"""
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

    def seed_sample(self, db=..., cwd=None):
        """用公开的 add 语义准备固定样例购物车（P001 两件、P002 一件）。"""
        for product_id, quantity in SAMPLE:
            result = self.run_shop(
                ["add", product_id, quantity], db=db, cwd=cwd
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")

    def break_db(self, db):
        """把健康样例库变成异常样例：clear 的删除被数据库拒绝，读取不受影响。"""
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(REJECT_TRIGGER)
            conn.commit()
        finally:
            conn.close()

    def restore_db(self, db):
        """解除删除拒绝：删除触发器，同一数据库恢复可清空。"""
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_NAME}")
            conn.commit()
        finally:
            conn.close()

    def snapshot(self, db):
        """重新打开数据库，读取失败后必须保持一致的状态：

        建表语句、商品名称与价格、两条购物车记录（含数量）。
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

    def assert_db_unavailable(self, result):
        """数据库删除失败：退出码 1、空标准输出、仅一行错误、无堆栈与内部详情。"""
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("sqlite3", result.stderr.lower())
        self.assertNotIn("reject", result.stderr.lower())

    def assert_show(self, expected_lines, db=..., cwd=None):
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_catalog(self, db=..., cwd=None):
        """商品目录保持原样：编号、名称、单价均不变。"""
        result = self.run_shop(["catalog"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, CATALOG_OUTPUT)

    def check_clear_rejected_keeps_state(self, db=..., cwd=None):
        """异常样例上 clear 被拒绝且状态不变；重复调用结果一致。

        返回 (真实数据库路径, 失败前快照)，供调用方继续做恢复验证。
        """
        self.seed_sample(db=db, cwd=cwd)
        if db is ...:
            real_db = self.tmpdir / "cart.sqlite3"
        elif db is None:
            real_db = cwd / "shop.sqlite3"
        else:
            real_db = db
        self.break_db(real_db)
        before = self.snapshot(real_db)

        # 异常样例能正常打开：show 与 catalog 仍读取原有内容
        self.assert_show(SAMPLE_SHOW, db=db, cwd=cwd)
        self.assert_catalog(db=db, cwd=cwd)

        # clear 的删除被数据库拒绝（删到第二条记录时才失败）
        result = self.run_shop(["clear"], db=db, cwd=cwd)
        self.assert_db_unavailable(result)

        # 失败后重新打开数据库：表结构、商品资料、购物车记录与失败前一致，
        # P001 数量仍为 2，P002 数量仍为 1（部分删除已回滚）
        self.assertEqual(self.snapshot(real_db), before)
        self.assertEqual(self.snapshot(real_db)[2], SAMPLE_CART)

        # 另起进程 show：仍按编号升序显示原有明细与汇总
        self.assert_show(SAMPLE_SHOW, db=db, cwd=cwd)

        # 重复调用 clear 仍是同样错误，购物车保持完整，不只凭错误文字判断
        result = self.run_shop(["clear"], db=db, cwd=cwd)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(real_db), before)
        self.assert_show(SAMPLE_SHOW, db=db, cwd=cwd)

        return real_db, before

    def test_clear_delete_rejected_keeps_cart_and_recovers(self):
        """显式 --db 入口：删除被拒时状态不变，解除拒绝后 clear 正常完成。"""
        db, _ = self.check_clear_rejected_keeps_state()

        # 解除删除拒绝后，同一数据库上的 clear 正常完成
        self.restore_db(db)
        result = self.run_shop(["clear"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "购物车已清空\n")
        self.assertEqual(result.stderr, "")

        # 清空结果已持久化：show 只剩空车汇总两行，目录保留，数据库文件保留
        self.assert_show(EMPTY_CART_SHOW, db=db)
        self.assert_catalog(db=db)
        self.assertTrue(db.is_file())

    def test_clear_delete_rejected_default_db_entry(self):
        """默认 shop.sqlite3 入口（不传 --db）：失败保护语义与显式入口一致。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        real_db, _ = self.check_clear_rejected_keeps_state(db=None, cwd=workdir)
        self.assertEqual(real_db, default_db)  # 全程走临时工作目录下的默认文件
        self.assertTrue(default_db.is_file())

        # 默认文件上的目录与购物车内容在失败后保持原样
        self.assert_catalog(db=None, cwd=workdir)
        self.assert_show(SAMPLE_SHOW, db=None, cwd=workdir)


if __name__ == "__main__":
    unittest.main()
