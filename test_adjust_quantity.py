#!/usr/bin/env python3
"""shop.py decrease/set 共用购物车数量调整流程的回归测试。

本次重构把两个命令共同维护的规则收拢到 read_cart_product（商品
检查 + 购物车读取）与 write_cart_quantity（零即删除、非零更新），
本测试针对这条共用流程做端到端回归，确保：

- 两个命令各自语义不变（decrease 是本次减少量，set 是最终件数）；
- 商品检查、购物车读取、调整后保存的共用规则对两条命令一致；
- 数量格式/范围错误优先于编号错误，decrease 超长纯数字仍按
  “减少数量超过购物车数量”处理，不套用 set 的数量上界；
- 减至零或设为零都只删购物车记录、保留商品目录并跨进程持久化；
- 数据库拒绝本次变更时退出 1、状态无部分修改，恢复后命令仍成功。

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

# 固定验收样例：两件 P001、一件 P002，单价均未改动
SAMPLE = (("P001", "2"), ("P002", "1"))
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)
# decrease P001 01 后
AFTER_DECREASE_SHOW = (
    "P001 虚拟笔记本 1200 1 1200",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 2",
    "总金额 3700",
)
# 随后 set P001 3 后
AFTER_SET_SHOW = (
    "P001 虚拟笔记本 1200 3 3600",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 4",
    "总金额 6100",
)
ONLY_P002_SHOW = (
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 1",
    "总金额 2500",
)
CATALOG_OUTPUT = "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n"

# 超过 SQLite INTEGER 上界的纯数字：set 报范围错误，decrease 报超过购物车数量
OVER_MAX = "9223372036854775808"
# 五千位纯数字：超过 Python 3.11 默认数字转换位数限制，decrease 也必须有确定结果
LONG_NINES = "9" * 5000

# 仅拒绝 P001 购物车变更的触发器：UPDATE 对应非零调整，DELETE 对应归零
REJECT_TRIGGERS = (
    "CREATE TRIGGER reject_p001_cart_update "
    "BEFORE UPDATE ON cart WHEN OLD.product_id = 'P001' "
    "BEGIN SELECT RAISE(ABORT, 'reject P001 change'); END",
    "CREATE TRIGGER reject_p001_cart_delete "
    "BEFORE DELETE ON cart WHEN OLD.product_id = 'P001' "
    "BEGIN SELECT RAISE(ABORT, 'reject P001 change'); END",
)
TRIGGER_NAMES = ("reject_p001_cart_update", "reject_p001_cart_delete")


class AdjustQuantityFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=...):
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
            if db is ...:
                db = self.tmpdir / "cart.sqlite3"
            cmd += ["--db", str(db)]
        cmd += list(args)
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            cmd,
            cwd=str(self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    def seed_sample(self, db=...):
        """用公开的 add 语义准备 P001 两件、P002 一件。"""
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return self.tmpdir / "cart.sqlite3" if db is ... else db

    def assert_show(self, db, expected_lines):
        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_failure(self, result, code, message):
        """失败调用：退出码、空 stdout、独占一行的错误、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def assert_success_line(self, result, line):
        """成功调用：退出码 0、成功输出独占一行并以换行结束、stderr 为空。"""
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, line + "\n")
        self.assertEqual(result.stderr, "")

    # ---- 验收顺序：decrease P001 01，再 set P001 3 ----

    def test_acceptance_decrease_then_set_on_fixed_sample(self):
        db = self.seed_sample()

        result = self.run_shop(["decrease", "P001", "01"], db=db)
        self.assert_success_line(result, "P001 数量 1")
        self.assert_show(db, AFTER_DECREASE_SHOW)

        result = self.run_shop(["set", "P001", "3"], db=db)
        self.assert_success_line(result, "P001 数量 3")
        self.assert_show(db, AFTER_SET_SHOW)

    # ---- 共用的“读取 + 检查”规则：编号、未知、不在购物车 ----

    def test_shared_product_lookup_rules_for_both_commands(self):
        db = self.seed_sample()

        for command in ("decrease", "set"):
            with self.subTest(command=command):
                # 未知编号：编号按原样匹配，报未知商品
                self.assert_failure(
                    self.run_shop([command, "p001", "1"], db=db),
                    2,
                    "未知商品",
                )
                self.assert_failure(
                    self.run_shop([command, "P999", "1"], db=db),
                    2,
                    "未知商品",
                )
                # 目录存在但未加入购物车：报商品不在购物车（set 零目标也一样）
                self.seed_only_p001(db)
                self.assert_failure(
                    self.run_shop([command, "P002", "1"], db=db),
                    2,
                    "商品不在购物车",
                )
                self.assert_failure(
                    self.run_shop([command, "P002", "0"], db=db),
                    2,
                    "商品不在购物车" if command == "set" else "数量必须为正整数",
                )
        # 全部失败后购物车仍为固定样例之外的 P001 两件，未被改动
        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 2 2400", "总数量 2", "总金额 2400"),
        )

    def seed_only_p001(self, db):
        """把样例库恢复/改造成仅有 P001 两件（P002 目录保留、购物车无记录）。"""
        result = self.run_shop(["clear"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(["add", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_quantity_errors_take_priority_over_product_errors(self):
        db = self.seed_sample()

        # decrease：格式错误优先于编号错误
        for quantity in ("0", "000", "abc", "-1", " 1", ""):
            with self.subTest(command="decrease", quantity=quantity):
                self.assert_failure(
                    self.run_shop(["decrease", "P999", quantity], db=db),
                    2,
                    "数量必须为正整数",
                )

        # set：格式错误与范围错误都优先于编号错误
        for quantity, message in (
            ("abc", "数量必须为非负整数"),
            ("-1", "数量必须为非负整数"),
            ("", "数量必须为非负整数"),
            (OVER_MAX, "数量超出范围"),
            (LONG_NINES, "数量超出范围"),
        ):
            with self.subTest(command="set", quantity=quantity[:12]):
                self.assert_failure(
                    self.run_shop(["set", "P999", quantity], db=db),
                    2,
                    message,
                )

        self.assert_show(db, SAMPLE_SHOW)

    def test_decrease_overlong_pure_digits_uses_business_rule_not_range(self):
        """decrease 的超长纯数字按“超过购物车数量”判断，不套用 set 上界。"""
        db = self.seed_sample()

        # 超过 SQLite 上界但只有 19 位：decrease 仍是业务比较
        self.assert_failure(
            self.run_shop(["decrease", "P001", OVER_MAX], db=db),
            2,
            "减少数量超过购物车数量",
        )
        # 五千位纯数字：同样得到确定的业务结果，无异常堆栈
        self.assert_failure(
            self.run_shop(["decrease", "P001", LONG_NINES], db=db),
            2,
            "减少数量超过购物车数量",
        )
        # 恰好相等允许；超过一件则拒绝
        self.assert_success_line(
            self.run_shop(["decrease", "P002", "1"], db=db), "P002 数量 0"
        )
        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 2 2400", "总数量 2", "总金额 2400"),
        )

    def test_distinct_format_messages_and_zero_semantics(self):
        """两个命令各自的格式文案与零语义保持不同。"""
        db = self.seed_sample()

        self.assert_failure(
            self.run_shop(["decrease", "P001", "0"], db=db),
            2,
            "数量必须为正整数",
        )
        self.assert_failure(
            self.run_shop(["set", "P001", "x"], db=db),
            2,
            "数量必须为非负整数",
        )
        self.assert_show(db, SAMPLE_SHOW)

        # set 同值设定成功
        self.assert_success_line(
            self.run_shop(["set", "P001", "2"], db=db), "P001 数量 2"
        )

    # ---- 共用的保存流程：归零删除记录、保留目录、跨进程持久化 ----

    def test_zero_removes_row_keeps_catalog_and_persists_for_both_commands(self):
        # decrease 恰好减至零
        db = self.seed_sample()
        self.assert_success_line(
            self.run_shop(["decrease", "P001", "2"], db=db), "P001 数量 0"
        )
        self.assert_show(db, ONLY_P002_SHOW)
        self.assertEqual(
            self.run_shop(["catalog"], db=db).stdout, CATALOG_OUTPUT
        )
        # 归零后再对该商品做调整：报不在购物车
        self.assert_failure(
            self.run_shop(["decrease", "P001", "1"], db=db),
            2,
            "商品不在购物车",
        )
        self.assert_failure(
            self.run_shop(["set", "P001", "0"], db=db),
            2,
            "商品不在购物车",
        )

        # set 把剩余 P002 设为零：目录仍保留，空购物车跨进程可见
        self.assert_success_line(
            self.run_shop(["set", "P002", "000"], db=db), "P002 数量 0"
        )
        self.assert_show(db, ("总数量 0", "总金额 0"))
        self.assertEqual(
            self.run_shop(["catalog"], db=db).stdout, CATALOG_OUTPUT
        )
        # 重新 add 后目录与购物车照常工作，证明仅删除了购物车记录
        self.assert_success_line(
            self.run_shop(["add", "P001", "1"], db=db), "P001 数量 1"
        )
        self.assert_show(
            db,
            ("P001 虚拟笔记本 1200 1 1200", "总数量 1", "总金额 1200"),
        )

    # ---- 参数个数仍由统一入口处理 ----

    def test_missing_or_extra_args_are_argument_errors(self):
        db = self.seed_sample()

        for command in ("decrease", "set"):
            with self.subTest(command=command):
                self.assert_failure(
                    self.run_shop([command], db=db), 2, "参数错误"
                )
                self.assert_failure(
                    self.run_shop([command, "P001"], db=db), 2, "参数错误"
                )
                self.assert_failure(
                    self.run_shop([command, "P001", "1", "2"], db=db),
                    2,
                    "参数错误",
                )
        self.assert_show(db, SAMPLE_SHOW)

    # ---- 数据库失败：回滚不留部分修改，恢复后共用流程仍成功 ----

    def cart_snapshot(self, db):
        conn = sqlite3.connect(str(db))
        try:
            return conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        finally:
            conn.close()

    def install_reject_triggers(self, db):
        conn = sqlite3.connect(str(db))
        try:
            for statement in REJECT_TRIGGERS:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()

    def drop_reject_triggers(self, db):
        conn = sqlite3.connect(str(db))
        try:
            for name in TRIGGER_NAMES:
                conn.execute(f"DROP TRIGGER IF EXISTS {name}")
            conn.commit()
        finally:
            conn.close()

    def assert_db_unavailable(self, result):
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("sqlite3", result.stderr.lower())
        self.assertNotIn("reject", result.stderr.lower())

    def test_db_write_failure_is_atomic_and_recovers_for_both_commands(self):
        for command, quantity, ok_line, recovered_show in (
            ("decrease", "1", "P001 数量 1", AFTER_DECREASE_SHOW),
            ("set", "3", "P001 数量 3", AFTER_SET_SHOW),
        ):
            with self.subTest(command=command):
                # 每个命令使用独立库文件，避免上一轮的落盘结果累积到本轮
                db = self.tmpdir / f"cart-{command}.sqlite3"
                self.seed_sample(db)
                self.install_reject_triggers(db)
                before = self.cart_snapshot(db)

                # 非零调整（UPDATE）被拒：状态不变
                result = self.run_shop([command, "P001", quantity], db=db)
                self.assert_db_unavailable(result)
                self.assertEqual(self.cart_snapshot(db), before)
                self.assert_show(db, SAMPLE_SHOW)

                # 归零调整（DELETE）同样被拒：记录仍在、数量不变
                result = self.run_shop([command, "P001", "2" if command == "decrease" else "0"], db=db)
                self.assert_db_unavailable(result)
                self.assertEqual(self.cart_snapshot(db), before)
                self.assert_show(db, SAMPLE_SHOW)

                # 解除拒绝后同一数据库上调整成功并持久化
                self.drop_reject_triggers(db)
                result = self.run_shop([command, "P001", quantity], db=db)
                self.assert_success_line(result, ok_line)
                self.assert_show(db, recovered_show)

    def test_unopenable_database_reports_unavailable(self):
        """--db 指向现有目录：打开阶段即报数据库不可用，退出 1。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()
        for command in ("decrease", "set"):
            with self.subTest(command=command):
                self.assert_db_unavailable(
                    self.run_shop([command, "P001", "1"], db=directory)
                )


if __name__ == "__main__":
    unittest.main()
