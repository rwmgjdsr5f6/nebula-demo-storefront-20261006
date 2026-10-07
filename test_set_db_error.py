#!/usr/bin/env python3
"""shop.py set 命令数据库变更失败的可重复回归测试。

复现的问题：数据库能正常打开、show 与 catalog 仍能读取原有内容，
但 set 所需的变更（目标大于零时 UPDATE，目标为零时 DELETE）被数据库
拒绝。按 README 约定“任何失败操作都不会改变已有购物车内容”，
此时应只输出一行“数据库不可用”并以退出码 1 结束，
且不改变表结构、商品目录与原有购物车记录；
解除拒绝后，同一数据库上的 set 仍可正常完成。

异常样例通过给 cart 表添加只针对 P001 的 RAISE(ABORT) 触发器构造：
读取与其他商品的写入不受影响，只有 P001 的更新或删除被拒绝；
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
# set P001 3 成功后：P001 三件、P002 一件
SET_TO_THREE_SHOW = (
    "P001 虚拟笔记本 1200 3 3600",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 4",
    "总金额 6100",
)
# set P001 0 成功后：整条记录删除，仅剩 P002 一件
ONLY_P002_SHOW = (
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 1",
    "总金额 2500",
)
CATALOG_OUTPUT = "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n"

# 只拒绝 P001 更新的触发器：set 目标大于零时的 UPDATE 被拒
REJECT_UPDATE_TRIGGER = (
    "CREATE TRIGGER reject_p001_cart_update "
    "BEFORE UPDATE ON cart WHEN OLD.product_id = 'P001' "
    "BEGIN SELECT RAISE(ABORT, 'reject P001 set update'); END"
)
UPDATE_TRIGGER_NAME = "reject_p001_cart_update"

# 只拒绝 P001 删除的触发器：set 目标为零时的 DELETE 被拒
REJECT_DELETE_TRIGGER = (
    "CREATE TRIGGER reject_p001_cart_delete "
    "BEFORE DELETE ON cart WHEN OLD.product_id = 'P001' "
    "BEGIN SELECT RAISE(ABORT, 'reject P001 set delete'); END"
)
DELETE_TRIGGER_NAME = "reject_p001_cart_delete"


class SetDatabaseErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py（db=None 表示不传 --db）。"""
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
            if db is ...:
                db = self.tmpdir / "sample.sqlite3"
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

    def seed_sample(self):
        """用公开的 add 语义准备固定样例购物车（P001 两件、P002 一件）。"""
        db = self.tmpdir / "sample.sqlite3"
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return db

    def install_trigger(self, db, trigger_sql):
        """在健康样例库上安装拒绝变更的触发器。"""
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(trigger_sql)
            conn.commit()
        finally:
            conn.close()

    def drop_trigger(self, db, trigger_name):
        """解除拒绝：删除触发器，同一数据库恢复可写。"""
        conn = sqlite3.connect(str(db))
        try:
            conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
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
        """数据库变更失败：退出码 1、空标准输出、仅一行错误、无堆栈与 SQL 详情。"""
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("sqlite3", result.stderr.lower())
        self.assertNotIn("reject", result.stderr.lower())

    def assert_show(self, db, expected_lines):
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_catalog(self, db):
        """商品目录保持原样：编号、名称、单价均不变。"""
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, CATALOG_OUTPUT)

    def test_set_positive_db_error_keeps_state_and_recovers(self):
        """UPDATE 被拒时 set P001 3 报数据库不可用且状态不变；解除后设定成功。"""
        db = self.seed_sample()
        self.install_trigger(db, REJECT_UPDATE_TRIGGER)
        before = self.snapshot(db)

        # 异常样例能正常打开：show 与 catalog 仍读取原有内容
        self.assert_show(db, SAMPLE_SHOW)
        self.assert_catalog(db)

        # 目标大于零的 UPDATE 被数据库拒绝
        result = self.run_shop(["set", "P001", "3"], db=db)
        self.assert_db_unavailable(result)

        # 失败后表结构、商品资料、购物车记录与失败前完全一致
        self.assertEqual(self.snapshot(db), before)
        self.assertEqual(self.snapshot(db)[2], SAMPLE_CART)
        self.assert_show(db, SAMPLE_SHOW)

        # 触发器只针对 P001：其他商品的 set 仍正常完成
        result = self.run_shop(["set", "P002", "3"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 数量 3\n")
        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 2 2400",
                "P002 虚拟马克杯 2500 3 7500",
                "总数量 5",
                "总金额 9900",
            ),
        )
        # 把 P002 设回一件，恢复固定样例后再验证拒绝依旧
        result = self.run_shop(["set", "P002", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.snapshot(db)[2], SAMPLE_CART)

        # 重复失败调用不改变数量
        result = self.run_shop(["set", "P001", "3"], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(self.snapshot(db), before)

        # 解除拒绝后，同一数据库上的设定正常完成并持久化
        self.drop_trigger(db, UPDATE_TRIGGER_NAME)
        result = self.run_shop(["set", "P001", "3"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 3\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db, SET_TO_THREE_SHOW)
        self.assert_catalog(db)
        self.assertEqual(self.snapshot(db)[2], [("P001", 3), ("P002", 1)])

    def test_set_zero_db_error_keeps_state_and_recovers(self):
        """DELETE 被拒时 set P001 0 报数据库不可用且记录保留；解除后置零成功。"""
        db = self.seed_sample()
        self.install_trigger(db, REJECT_DELETE_TRIGGER)
        before = self.snapshot(db)

        # 目标为零的 DELETE 被数据库拒绝：记录不得被移除
        result = self.run_shop(["set", "P001", "0"], db=db)
        self.assert_db_unavailable(result)

        # 失败后仍输出空标准输出且 P001 数量保持两件
        self.assertEqual(self.snapshot(db), before)
        self.assertEqual(self.snapshot(db)[2], SAMPLE_CART)
        self.assert_show(db, SAMPLE_SHOW)
        self.assert_catalog(db)

        # 触发器只针对 P001：P002 置零的删除仍正常完成
        result = self.run_shop(["set", "P002", "000"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 数量 0\n")
        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 2 2400", "总数量 2", "总金额 2400"),
        )

        # 重复置零 P001 仍失败，记录保留
        result = self.run_shop(["set", "P001", "0"], db=db)
        self.assert_db_unavailable(result)
        self.assertEqual(
            self.snapshot(db)[2],
            [("P001", 2)],
        )

        # 解除拒绝后，置零正常完成：仅剩目录保留，购物车中 P001 被移除
        self.drop_trigger(db, DELETE_TRIGGER_NAME)
        result = self.run_shop(["set", "P001", "0"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 0\n")
        self.assertEqual(result.stderr, "")

        self.assert_show(db, ("总数量 0", "总金额 0"))
        self.assert_catalog(db)
        self.assertEqual(self.snapshot(db)[2], [])

    def test_broken_db_validation_order_unchanged(self):
        """异常数据库上参数与数量、编号校验的既有优先级不变，且不触达写入。"""
        db = self.seed_sample()
        self.install_trigger(db, REJECT_UPDATE_TRIGGER)
        before = self.snapshot(db)

        # 缺少/多出参数仍报参数错误（退出 2）
        self.assert_failure(
            self.run_shop(["set", "P001"], db=db), 2, "参数错误"
        )
        self.assert_failure(
            self.run_shop(["set", "P001", "1", "x"], db=db), 2, "参数错误"
        )

        # 数量校验先于编号校验与购物车变更
        self.assert_failure(
            self.run_shop(["set", "P999", ""], db=db), 2, "数量必须为非负整数"
        )
        self.assert_failure(
            self.run_shop(["set", "P999", "-1"], db=db), 2, "数量必须为非负整数"
        )
        # 数量有效但编号未知：仍在校验阶段失败，不触达触发器保护的写入
        self.assert_failure(
            self.run_shop(["set", "P999", "1"], db=db), 2, "未知商品"
        )

        self.assertEqual(self.snapshot(db), before)

    def assert_failure(self, result, code, message):
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
