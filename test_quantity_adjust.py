#!/usr/bin/env python3
"""shop.py decrease / set 共用购物车数量调整流程的回归测试。

本次重构把两个命令共同的商品检查、购物车读取、零结果删记录/正结果
更新、提交与数据库错误处理收敛到 shop.adjust_cart_quantity；命令自身
只保留数量解析与业务计算（decrease 的剩余量、set 的目标量）。

测试分两层：

- 直接调用 shop.adjust_cart_quantity：用内存 SQLite 库固定共享流程的
  骨架行为（编号检查、购物车检查、解析结果与当前数量原样传给业务
  计算、零删正改、业务失败先于写入、写入失败回滚并退出 1）；
- 子进程端到端：任务给定的验收样例，以及 decrease 与 set 在同一共享
  流程上必须一致的错误优先级、输出契约、零移除持久化、超长数字与
  数据库不可用语义。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover
"""

import contextlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SHOP = PROJECT_DIR / "shop.py"

sys.path.insert(0, str(PROJECT_DIR))
import shop  # noqa: E402

# 固定样例：两件 P001、一件 P002
SAMPLE = (("P001", "2"), ("P002", "1"))
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)
# decrease P001 01 后：两件商品各一件
ONE_EACH_SHOW = (
    "P001 虚拟笔记本 1200 1 1200",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 2",
    "总金额 3700",
)
# set P001 3 后：P001 三件、P002 一件
SET_THREE_SHOW = (
    "P001 虚拟笔记本 1200 3 3600",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 4",
    "总金额 6100",
)
# P001 记录被移除后：仅剩 P002 一件
ONLY_P002_SHOW = (
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 1",
    "总金额 2500",
)
CATALOG_OUTPUT = "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n"

LONG_NINES = "9" * 5000

# 只拒绝 P001 写入（UPDATE 正数分支、DELETE 归零分支）的触发器
REJECT_TRIGGERS = (
    "CREATE TRIGGER reject_p001_cart_update "
    "BEFORE UPDATE ON cart WHEN OLD.product_id = 'P001' "
    "BEGIN SELECT RAISE(ABORT, 'reject P001 write'); END",
    "CREATE TRIGGER reject_p001_cart_delete "
    "BEFORE DELETE ON cart WHEN OLD.product_id = 'P001' "
    "BEGIN SELECT RAISE(ABORT, 'reject P001 write'); END",
)


class AdjustCartQuantityDirectTests(unittest.TestCase):
    """直接驱动共享流程本身：不经子进程、不经命令专属解析。"""

    def setUp(self):
        # 内存库：open_db 同样负责建表与固定目录写入
        self.conn = shop.open_db(":memory:")
        self.conn.execute(
            "INSERT INTO cart (product_id, quantity) VALUES ('P001', 2)"
        )
        self.conn.execute(
            "INSERT INTO cart (product_id, quantity) VALUES ('P002', 1)"
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def run_adjust(self, product_id, parsed, resolver):
        """运行共享流程并返回 (SystemExit 码或 None, stdout, stderr)。"""
        stdout, stderr = io.StringIO(), io.StringIO()
        code = None
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                shop.adjust_cart_quantity(
                    self.conn, product_id, parsed, resolver
                )
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def cart_quantity(self, product_id):
        row = self.conn.execute(
            "SELECT quantity FROM cart WHERE product_id = ?", (product_id,)
        ).fetchone()
        return None if row is None else row[0]

    def test_positive_result_updates_and_prints_one_line(self):
        """正结果走 UPDATE：输出独占一行，落库数量即业务计算结果。"""
        code, stdout, stderr = self.run_adjust(
            "P001", 5, lambda current, parsed: current + parsed
        )
        self.assertIsNone(code)
        self.assertEqual(stdout, "P001 数量 7\n")
        self.assertEqual(stderr, "")
        self.assertEqual(self.cart_quantity("P001"), 7)

    def test_zero_result_deletes_row_but_keeps_product(self):
        """零结果走 DELETE：购物车记录移除，商品目录保留，输出数量 0。"""
        code, stdout, stderr = self.run_adjust(
            "P001", "ignored", lambda current, parsed: 0
        )
        self.assertIsNone(code)
        self.assertEqual(stdout, "P001 数量 0\n")
        self.assertIsNone(self.cart_quantity("P001"))
        product = self.conn.execute(
            "SELECT name, price FROM products WHERE id = 'P001'"
        ).fetchone()
        self.assertEqual(product, ("虚拟笔记本", 1200))

    def test_resolver_receives_current_and_parsed_verbatim(self):
        """业务计算拿到的两个参数就是购物车当前数量与调用方解析结果。"""
        seen = {}

        def resolver(current, parsed):
            seen["args"] = (current, parsed)
            return current

        self.run_adjust("P002", ("digits",), resolver)
        self.assertEqual(seen["args"], (1, ("digits",)))

    def test_unknown_product_exits_2_without_calling_resolver(self):
        """编号按原样匹配：未知编号报未知商品，业务计算不执行、库不变。"""
        def resolver(current, parsed):
            raise AssertionError("商品未知时不应执行业务计算")

        code, stdout, stderr = self.run_adjust("P999", 1, resolver)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "未知商品\n")
        self.assertEqual(self.cart_quantity("P001"), 2)

    def test_product_not_in_cart_exits_2_without_calling_resolver(self):
        """目录存在但购物车无记录：报商品不在购物车，业务计算不执行。"""
        def resolver(current, parsed):
            raise AssertionError("不在购物车时不应执行业务计算")

        # 先把 P001 移出购物车但保留目录
        self.conn.execute("DELETE FROM cart WHERE product_id = 'P001'")
        self.conn.commit()
        code, stdout, stderr = self.run_adjust("P001", 0, resolver)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "商品不在购物车\n")

    def test_business_failure_happens_before_write(self):
        """业务计算内 fail：退出 2 且无输出，购物车保持调整前状态。"""
        def resolver(current, parsed):
            shop.fail(shop.ERR_DECREASE_TOO_MUCH, 2)

        code, stdout, stderr = self.run_adjust("P001", "9", resolver)
        self.assertEqual((code, stdout, stderr), (2, "", "减少数量超过购物车数量\n"))
        self.assertEqual(self.cart_quantity("P001"), 2)

    def test_write_failure_rolls_back_and_reports_db_unavailable(self):
        """UPDATE 被数据库拒绝：回滚、退出 1、无成功输出、数量不变。"""
        for statement in REJECT_TRIGGERS:
            self.conn.execute(statement)
        self.conn.commit()

        code, stdout, stderr = self.run_adjust(
            "P001", 9, lambda current, parsed: 9
        )
        self.assertEqual(code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "数据库不可用\n")
        self.assertEqual(self.cart_quantity("P001"), 2)
        self.assertEqual(self.cart_quantity("P002"), 1)


class QuantityAdjustCommandTests(unittest.TestCase):
    """端到端：decrease 与 set 经过同一共享流程时的对外契约。"""

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
        """用公开的 add 语义准备固定样例购物车（P001 两件、P002 一件）。"""
        db = self.tmpdir / "cart.sqlite3"
        for product_id, quantity in SAMPLE:
            result = self.run_shop(["add", product_id, quantity], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
        return db

    def assert_show(self, db, expected_lines):
        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_failure(self, result, code, message):
        """失败契约：退出码、空 stdout、stderr 仅一行错误、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_acceptance_decrease_then_set_on_fixed_sample(self):
        """任务验收序列：decrease P001 01 后 2 件 3700；set P001 3 后 4 件 6100。"""
        db = self.seed_sample()

        result = self.run_shop(["decrease", "P001", "01"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        self.assertEqual(result.stderr, "")
        self.assert_show(db, ONE_EACH_SHOW)

        result = self.run_shop(["set", "P001", "3"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 3\n")
        self.assertEqual(result.stderr, "")
        self.assert_show(db, SET_THREE_SHOW)

    def test_unknown_and_not_in_cart_are_shared_between_commands(self):
        """两个命令的商品检查规则一致：未知商品、目录有但购物车无。"""
        db = self.seed_sample()

        for command, valid_quantity in (
            ("decrease", "1"),
            ("set", "0"),
            ("set", "3"),
        ):
            with self.subTest(command=command, quantity=valid_quantity):
                result = self.run_shop(
                    [command, "P999", valid_quantity], db=db
                )
                self.assert_failure(result, 2, "未知商品")

        # P002 之外再准备一个只在目录里的商品视角：样例中两商品均在购物车，
        # 这里用全新库验证“目录存在但购物车为空”
        fresh = self.tmpdir / "fresh.sqlite3"
        for command, valid_quantity in (
            ("decrease", "1"),
            ("set", "0"),
            ("set", "3"),
        ):
            with self.subTest(fresh=command, quantity=valid_quantity):
                result = self.run_shop(
                    [command, "P001", valid_quantity], db=fresh
                )
                self.assert_failure(result, 2, "商品不在购物车")

        self.assert_show(db, SAMPLE_SHOW)

    def test_quantity_errors_take_priority_over_product_errors(self):
        """数量格式错误（及 set 的范围错误）优先于编号错误，两命令一致。"""
        db = self.seed_sample()

        for quantity in ("0", "000", "-1", "abc", "1.5", " 1"):
            with self.subTest(command="decrease", quantity=quantity):
                result = self.run_shop(
                    ["decrease", "P999", quantity], db=db
                )
                self.assert_failure(result, 2, "数量必须为正整数")

        for quantity, message in (
            ("-1", "数量必须为非负整数"),
            ("", "数量必须为非负整数"),
            ("9223372036854775808", "数量超出范围"),
            (LONG_NINES, "数量超出范围"),
        ):
            with self.subTest(command="set", quantity=quantity[:12]):
                result = self.run_shop(["set", "P999", quantity], db=db)
                self.assert_failure(result, 2, message)

        self.assert_show(db, SAMPLE_SHOW)

    def test_decrease_overlong_digits_follow_business_condition(self):
        """超长纯数字对 decrease 不套 set 上界：只按“超过购物车数量”判定。"""
        db = self.seed_sample()

        result = self.run_shop(["decrease", "P001", LONG_NINES], db=db)
        self.assert_failure(result, 2, "减少数量超过购物车数量")
        self.assert_show(db, SAMPLE_SHOW)

        # 超过 SQLite 整数上界但仍是纯数字：同样是业务条件而非范围错误
        result = self.run_shop(
            ["decrease", "P001", "9223372036854775808"], db=db
        )
        self.assert_failure(result, 2, "减少数量超过购物车数量")
        self.assert_show(db, SAMPLE_SHOW)

    def test_zero_results_remove_row_keep_catalog_and_persist(self):
        """减至零与设为零：记录移除、目录保留，跨进程可见，之后可重新加入。"""
        db = self.seed_sample()

        result = self.run_shop(["set", "P001", "000"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 0\n")
        self.assert_show(db, ONLY_P002_SHOW)

        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, CATALOG_OUTPUT)

        # set 不负责首次加入；重新加入走 add，随后 decrease 归零同理
        result = self.run_shop(["add", "P001", "1"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(["decrease", "P001", "1"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 0\n")
        self.assert_show(db, ONLY_P002_SHOW)

        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(result.stdout, CATALOG_OUTPUT)

    def test_write_failure_is_shared_and_leaves_no_partial_change(self):
        """写入被触发器拒绝时两命令都报数据库不可用（退出 1），状态不变。

        正数与归零两种写入各覆盖一次；解除拒绝后同一命令可正常完成。
        """
        db = self.seed_sample()
        conn = sqlite3.connect(str(db))
        try:
            for statement in REJECT_TRIGGERS:
                conn.execute(statement)
            conn.commit()
        finally:
            conn.close()

        blocked_cases = (
            (["decrease", "P001", "1"], "positive decrease"),
            (["decrease", "P001", "2"], "decrease to zero"),
            (["set", "P001", "3"], "positive set"),
            (["set", "P001", "0"], "set to zero"),
        )
        for args, label in blocked_cases:
            with self.subTest(case=label):
                result = self.run_shop(args, db=db)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "数据库不可用\n")
                self.assertNotIn("Traceback", result.stderr)
                self.assert_show(db, SAMPLE_SHOW)

        # 解除写入拒绝：验收序列在同一库上恢复正常
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("DROP TRIGGER reject_p001_cart_update")
            conn.execute("DROP TRIGGER reject_p001_cart_delete")
            conn.commit()
        finally:
            conn.close()

        result = self.run_shop(["decrease", "P001", "01"], db=db)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        self.assert_show(db, ONE_EACH_SHOW)

    def test_unopenable_db_reports_unavailable_for_both_commands(self):
        """--db 指向目录：两个命令都只报数据库不可用并退出 1。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        for command, valid_quantity in (("decrease", "1"), ("set", "1")):
            with self.subTest(command=command):
                result = self.run_shop(
                    [command, "P001", valid_quantity], db=directory
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "数据库不可用\n")


if __name__ == "__main__":
    unittest.main()
