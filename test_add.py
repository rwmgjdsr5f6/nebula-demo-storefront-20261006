#!/usr/bin/env python3
"""shop.py add 命令数量语义的可重复回归测试。

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

# 从空库依次执行 add P002 1 / add P001 007 / add P001 2 后的完整购物车
CART_SHOW = (
    "P001 虚拟笔记本 1200 9 10800",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 10",
    "总金额 13300",
)

# 各类不被接受的数量：零、全零、负数、小数、空字符串、字母、
# 显式正号、带空格的数字、全角数字
INVALID_QUANTITIES = (
    "0",
    "000",
    "-1",
    "1.5",
    "",
    "abc",
    "+1",
    " 1",
    "１",
)


class ShopAddTests(unittest.TestCase):
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

    def seed_accumulated_cart(self, db=..., cwd=None):
        """从空库按公开命令构造已知购物车：P002=1、P001=007→7、P001 再 +2=9。"""
        result = self.run_shop(["add", "P002", "1"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "P002 数量 1\n")

        result = self.run_shop(["add", "P001", "007"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 前导零按数值解释：007 与 7 等价
        self.assertEqual(result.stdout, "P001 数量 7\n")

        result = self.run_shop(["add", "P001", "2"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        # 重复加入是累计（7+2）而非覆盖
        self.assertEqual(result.stdout, "P001 数量 9\n")

        return self.tmpdir / "cart.sqlite3" if db is ... else db

    def assert_show(self, db, expected_lines, cwd=None):
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_add_success_leading_zero_and_accumulation_persist(self):
        """成功场景：前导零按数值解释、重复加入累计、排序与金额正确落盘。"""
        db = self.seed_accumulated_cart()

        # 另起进程查看：按编号升序，P001 累计为 9，P002 数量与单价不受影响
        self.assert_show(db, CART_SHOW)

    def test_explicit_db_rejects_invalid_quantities_and_preserves_cart(self):
        """显式 --db 入口：各类非正整数数量均被拒绝，每次失败后购物车不变。"""
        db = self.seed_accumulated_cart()

        for quantity in INVALID_QUANTITIES:
            with self.subTest(quantity=quantity):
                result = self.run_shop(["add", "P001", quantity], db=db)
                self.assert_failure(result, 2, "数量必须为正整数")
                # 每次失败后重新查看：完整购物车内容与调用前相同
                self.assert_show(db, CART_SHOW)

    def test_add_unknown_product_quantity_error_has_priority(self):
        """P999：数量无效时优先报数量错误；数量有效时仅报未知商品。"""
        db = self.seed_accumulated_cart()

        for quantity in ("0", "abc", "-1"):
            with self.subTest(quantity=quantity):
                result = self.run_shop(["add", "P999", quantity], db=db)
                self.assert_failure(result, 2, "数量必须为正整数")

        result = self.run_shop(["add", "P999", "1"], db=db)
        self.assert_failure(result, 2, "未知商品")

        self.assert_show(db, CART_SHOW)

    def test_add_without_quantity_is_argument_error(self):
        """add 缺少数量：仅报参数错误，购物车不变。"""
        db = self.seed_accumulated_cart()

        result = self.run_shop(["add", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, CART_SHOW)

    def test_add_with_extra_arg_is_argument_error(self):
        """add 多出一个参数：仅报参数错误，购物车不变。"""
        db = self.seed_accumulated_cart()

        result = self.run_shop(["add", "P001", "1", "2"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_show(db, CART_SHOW)

    def test_default_db_in_temp_cwd_rejects_bad_quantity_and_preserves(self):
        """默认数据库入口：工作目录置于临时目录，数量错误后状态仍保留。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        self.seed_accumulated_cart(db=None, cwd=workdir)
        self.assertTrue(default_db.is_file())

        result = self.run_shop(["add", "P002", "0"], db=None, cwd=workdir)
        self.assert_failure(result, 2, "数量必须为正整数")

        # 另一次 show 调用确认失败结果没有落盘，完整购物车保持不变
        self.assert_show(None, CART_SHOW, cwd=workdir)


class ShopAddBrokenDbTests(unittest.TestCase):
    """add 在数据库可打开但购物车写入失败时的回归测试。

    异常样例：products 表正常且保存两件演示商品；cart 表只有
    product_id 主键、已有 P001 记录、缺少 quantity 列。此时 add 的
    查询/写入/提交会抛出 SQLite 错误，必须按 README 约定报告
    “数据库不可用”（退出码 1），不得暴露异常堆栈，也不得改变
    表结构、商品资料或原购物车记录。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """与 ShopAddTests 相同的子进程入口，默认工作目录为临时目录。"""
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
            if db is ...:
                db = self.tmpdir / "broken.sqlite3"
            cmd += ["--db", str(db)]
        cmd += list(args)
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            cmd,
            cwd=str(cwd if cwd is not None else self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

    def make_broken_db(self, path):
        """构造异常数据库：目录正常，cart 表缺 quantity 列且已有 P001。"""
        conn = sqlite3.connect(str(path))
        conn.execute(
            "CREATE TABLE products ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL)"
        )
        conn.executemany(
            "INSERT INTO products (id, name, price) VALUES (?, ?, ?)",
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
        )
        conn.execute("CREATE TABLE cart (product_id TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO cart (product_id) VALUES ('P001')")
        conn.commit()
        conn.close()

    def snapshot_db(self, path):
        """读取表结构与全部记录，用于失败前后的逐字节比对。"""
        conn = sqlite3.connect(str(path))
        schema = conn.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        products = conn.execute(
            "SELECT id, name, price FROM products ORDER BY id"
        ).fetchall()
        cart = conn.execute("SELECT product_id FROM cart ORDER BY product_id").fetchall()
        conn.close()
        return schema, products, cart

    def assert_db_unavailable(self, result):
        """退出码 1、标准输出为空、标准错误仅一行“数据库不可用”、无堆栈。"""
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("sqlite", result.stderr.lower())

    def check_broken_add_failure(self, db_arg, db_path, cwd):
        """对同一个异常数据库核对 add 失败输出与数据保持原样。

        db_arg 为传给 run_shop 的 --db 取值（None 表示走默认文件），
        db_path 为实际数据库文件路径，用于直接核对库内记录。
        """
        before = self.snapshot_db(db_path)

        # 重复调用得到同一结果
        for _ in range(2):
            result = self.run_shop(["add", "P001", "1"], db=db_arg, cwd=cwd)
            self.assert_db_unavailable(result)

        # 数量校验仍先于编号校验与购物车写入，退出码 2
        result = self.run_shop(["add", "P999", "0"], db=db_arg, cwd=cwd)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数量必须为正整数\n")

        result = self.run_shop(["add", "P999", "1"], db=db_arg, cwd=cwd)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "未知商品\n")

        # 失败前后：表结构、商品资料、原购物车记录完全一致（不补列、不重建）
        self.assertEqual(self.snapshot_db(db_path), before)

    def test_add_on_broken_db_reports_db_unavailable_and_preserves_data(self):
        """显式 --db 指向异常数据库：写入失败按数据库错误约定报告。"""
        db = self.tmpdir / "broken.sqlite3"
        self.make_broken_db(db)
        self.check_broken_add_failure(db, db, cwd=None)

    def test_add_on_broken_default_db_in_temp_cwd(self):
        """默认 shop.sqlite3 为异常数据库时结果与显式 --db 相同。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        db = workdir / "shop.sqlite3"
        self.make_broken_db(db)
        self.check_broken_add_failure(None, db, cwd=workdir)

    def test_normal_db_accumulation_leading_zero_and_totals(self):
        """正常数据库：重复加入累计、前导零被接受、金额按已保存目录计算。"""
        db = self.tmpdir / "cart.sqlite3"
        self.assertEqual(
            self.run_shop(["add", "P001", "2"], db=db).stdout, "P001 数量 2\n"
        )
        self.assertEqual(
            self.run_shop(["add", "P002", "1"], db=db).stdout, "P002 数量 1\n"
        )

        result = self.run_shop(["add", "P001", "01"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "P001 数量 3\n")

        result = self.run_shop(["show"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200 3 3600\n"
            "P002 虚拟马克杯 2500 1 2500\n"
            "总数量 4\n"
            "总金额 6100\n",
        )


if __name__ == "__main__":
    unittest.main()
