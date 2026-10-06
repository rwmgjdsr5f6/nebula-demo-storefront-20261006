#!/usr/bin/env python3
"""shop.py clear 命令删除被数据库拒绝时的可重复回归测试。

复现的问题：数据库能正常打开、show 与 catalog 仍能读取原有内容，
但 clear 的 DELETE 变更被数据库拒绝。按 README 约定
“任何失败操作都不会改变已有购物车内容”，此时应只输出一行
“数据库不可用”并以退出码 1 结束，且不改变表结构、商品目录与
原有购物车记录；解除删除拒绝后，同一数据库上的 clear 恢复正常。

异常样例通过给 cart 表添加只针对 P002 的 RAISE(ABORT) 删除触发器构造：
读取不受影响；DELETE FROM cart 按行顺序先删除 P001 记录，
处理到 P002 时才被拒绝，覆盖“已删除部分记录后才失败”的情形，
且不能用打不开文件的方式代替。DROP TRIGGER 即恢复正常。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

所有用例均在独立临时目录中运行：显式传入的数据库文件位于该目录，
未传 --db 时也把子进程的工作目录切到该目录，因此不会接触
项目或用户已有的 shop.sqlite3。
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

# 只拒绝 P002 删除的触发器：DELETE FROM cart 先删掉 P001 记录，
# 处理到 P002 时才被 RAISE(ABORT) 拒绝，即“删了部分记录后才失败”
REJECT_TRIGGER = (
    "CREATE TRIGGER reject_cart_delete_p002 "
    "BEFORE DELETE ON cart WHEN OLD.product_id = 'P002' "
    "BEGIN SELECT RAISE(ABORT, 'reject clear'); END"
)
TRIGGER_NAME = "reject_cart_delete_p002"


class ClearDatabaseErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py（db=None 表示不传 --db）。

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

    def seed_sample(self, db=...):
        """用公开的 add 入口准备固定样例购物车（P001 两件、P002 一件）。"""
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return self.tmpdir / "cart.sqlite3" if db is ... else db

    def break_db(self, db):
        """把健康样例库变成异常样例：P002 的删除被数据库拒绝。"""
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
        """删除被拒绝：退出码 1、空标准输出、仅一行错误、无堆栈与内部详情。"""
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("sqlite3", result.stderr.lower())
        self.assertNotIn("reject", result.stderr.lower())
        self.assertNotIn("trigger", result.stderr.lower())

    def assert_show(self, db, expected_lines, cwd=None):
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_catalog(self, db, cwd=None):
        """商品目录保持原样：编号、名称、单价均不变。"""
        result = self.run_shop(["catalog"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, CATALOG_OUTPUT)

    def check_delete_rejected_keeps_cart(self, db, cwd=None, snapshot_db=None):
        """在异常样例上核对失败保护：clear 被拒且购物车、目录、结构不变。

        db 为 run_shop 使用的 --db 取值（None 表示走默认 shop.sqlite3）；
        snapshot_db 为快照读取的实际文件路径，缺省与 db 相同。
        """
        if snapshot_db is None:
            snapshot_db = db
        before = self.snapshot(snapshot_db)

        # 异常样例能正常打开：show 与 catalog 仍读取原有内容
        self.assert_show(db, SAMPLE_SHOW, cwd=cwd)
        self.assert_catalog(db, cwd=cwd)

        # clear 的删除变更被数据库拒绝
        result = self.run_shop(["clear"], db=db, cwd=cwd)
        self.assert_db_unavailable(result)

        # 失败后重新打开同一数据库：表结构、商品资料、两条购物车记录
        # （含数量）与失败前完全一致
        self.assertEqual(self.snapshot(snapshot_db), before)
        self.assertEqual(self.snapshot(snapshot_db)[2], SAMPLE_CART)

        # 另起进程 show：明细与汇总均未变
        self.assert_show(db, SAMPLE_SHOW, cwd=cwd)

        # 重复调用 clear 仍是同样错误，购物车依旧完整：
        # 不能只凭第一次的错误文字认定保护有效
        result = self.run_shop(["clear"], db=db, cwd=cwd)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(snapshot_db), before)
        self.assert_show(db, SAMPLE_SHOW, cwd=cwd)
        self.assert_catalog(db, cwd=cwd)
        return before

    def test_clear_delete_rejected_keeps_cart_and_recovers(self):
        """显式 --db：删除被拒时购物车完整保留，解除拒绝后清空成功。"""
        db = self.seed_sample()
        # 准备阶段核对：小计 2400、2500，总数量 3，总金额 4900
        self.assert_show(db, SAMPLE_SHOW)

        self.break_db(db)
        before = self.check_delete_rejected_keeps_cart(db)

        # 解除删除拒绝后，同一数据库上的 clear 正常完成
        self.restore_db(db)
        result = self.run_shop(["clear"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "购物车已清空\n")
        self.assertEqual(result.stderr, "")

        # 清空结果已持久化：show 只剩空车汇总两行，目录不变，库文件保留
        self.assert_show(db, EMPTY_CART_SHOW)
        self.assert_catalog(db)
        self.assertTrue(db.is_file())

        # 恢复后的清空不依赖失败前的异常状态：表结构快照中两表定义未变
        self.assertEqual(self.snapshot(db)[0], before[0])

    def test_clear_delete_rejected_default_db_in_temp_cwd(self):
        """不传 --db：临时工作目录下的 shop.sqlite3 具有同样的失败保护。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        for product_id, quantity in SAMPLE:
            result = self.run_shop(
                ["add", product_id, quantity], db=None, cwd=workdir
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
        self.assertTrue(default_db.is_file())
        self.assert_show(None, SAMPLE_SHOW, cwd=workdir)

        self.break_db(default_db)
        self.check_delete_rejected_keeps_cart(
            None, cwd=workdir, snapshot_db=default_db
        )


if __name__ == "__main__":
    unittest.main()
