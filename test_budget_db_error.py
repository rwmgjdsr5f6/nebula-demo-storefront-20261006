#!/usr/bin/env python3
"""shop.py budget 命令数据库不可用场景的可重复回归测试。

参数校验通过后，数据库无法创建、打开或读取商品时，budget 必须只输出
一行“数据库不可用”并以退出码 1 结束：标准输出为空、无异常堆栈、
不泄露底层 SQLite 错误文字。budget 是只读命令，失败前后商品资料与
购物车内容保持一致。

覆盖三类失败：

- 数据库无法创建：路径位于不存在的目录、目标路径本身是目录、
  所在目录只读（root 下只读位会被绕过，该子用例自动跳过）。
- 数据库无法打开：目标文件不是合法的 SQLite 数据库文件。
- 能打开但无法读取商品：products 被构造成底层表已删除的视图，
  建表与初始化语句照常通过，只有 SELECT 商品行时数据库报错。

只使用 Python 标准库与本地 SQLite；在项目目录执行：

    python -m unittest test_budget_db_error
    python -m unittest discover

所有用例均在独立临时目录中运行，结束即清理，不会接触项目或用户
已有的 shop.sqlite3。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"


class BudgetDatabaseErrorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db):
        """以子进程运行 shop.py，每次都是全新进程。"""
        cmd = [sys.executable, str(SHOP), "--db", str(db)] + list(args)
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

    def assert_db_unavailable(self, result):
        """数据库失败：退出码 1、空标准输出、仅一行错误、无堆栈与底层信息。"""
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("sqlite3", result.stderr.lower())

    def create_healthy_db(self, path):
        """由 shop.py 正常建库并初始化两件演示商品。"""
        result = self.run_shop(["catalog"], path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(path.exists())
        return path

    def snapshot(self, path):
        """读取商品与购物车全部内容，供失败前后一致性核对。"""
        with sqlite3.connect(str(path)) as conn:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        return products, cart

    # ---- 无法创建 -------------------------------------------------------

    def test_missing_parent_directory_is_db_unavailable(self):
        """数据库路径位于不存在的目录：无法创建文件，退出码 1。"""
        db = self.tmpdir / "no_such_dir" / "shop.sqlite3"
        self.assertFalse(db.parent.exists())
        result = self.run_shop(["budget", "100"], db)
        self.assert_db_unavailable(result)
        # 失败没有顺手把缺失的目录建出来
        self.assertFalse(db.parent.exists())

    def test_db_path_is_an_existing_directory(self):
        """目标路径本身是目录：无法作为数据库打开，退出码 1。"""
        db = self.tmpdir / "a_directory"
        db.mkdir()
        result = self.run_shop(["budget", "100"], db)
        self.assert_db_unavailable(result)
        self.assertTrue(db.is_dir())

    @unittest.skipIf(os.geteuid() == 0, "root 会绕过目录只读权限位")
    def test_read_only_parent_directory_is_db_unavailable(self):
        """所在目录只读时无法创建数据库文件，退出码 1。"""
        ro_dir = self.tmpdir / "readonly_dir"
        ro_dir.mkdir()
        os.chmod(ro_dir, 0o555)
        self.addCleanup(lambda: os.chmod(ro_dir, 0o755))
        try:
            result = self.run_shop(["budget", "100"], ro_dir / "shop.sqlite3")
            self.assert_db_unavailable(result)
            self.assertFalse((ro_dir / "shop.sqlite3").exists())
        finally:
            os.chmod(ro_dir, 0o755)

    # ---- 无法打开 -------------------------------------------------------

    def test_non_sqlite_file_is_db_unavailable(self):
        """目标文件不是 SQLite 数据库：打开/初始化失败，退出码 1。"""
        db = self.tmpdir / "garbage.sqlite3"
        db.write_text("this is definitely not a sqlite database\n", encoding="utf-8")
        before_bytes = db.read_bytes()

        result = self.run_shop(["budget", "100"], db)
        self.assert_db_unavailable(result)
        # 失败不得改写已有文件
        self.assertEqual(db.read_bytes(), before_bytes)

    # ---- 能打开但无法读取商品 -------------------------------------------

    def make_products_unreadable_view(self, path):
        """构造“能打开、建表语句通过、但读不到商品”的异常数据库。

        products 被替换为引用已删除底层表的视图：open_db 中的
        CREATE TABLE IF NOT EXISTS 因同名对象存在而跳过，
        INSERT OR IGNORE 由 INSTEAD OF INSERT 触发器吞掉，
        只有 SELECT 商品行时数据库才报错。
        """
        conn = sqlite3.connect(str(path))
        try:
            # 正常初始化一份健康库后再改造
            conn.execute(
                "CREATE TABLE products ("
                "id TEXT PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE cart ("
                "product_id TEXT PRIMARY KEY, quantity INTEGER NOT NULL)"
            )
            conn.executemany(
                "INSERT INTO products (id, name, price) VALUES (?, ?, ?)",
                (("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)),
            )
            conn.execute("INSERT INTO cart (product_id, quantity) VALUES ('P001', 2)")
            # 重建为底层表缺失的视图
            conn.execute("DROP TABLE products")
            conn.execute("CREATE TABLE ghost(id TEXT, name TEXT, price INTEGER)")
            conn.execute(
                "CREATE VIEW products AS SELECT id, name, price FROM ghost"
            )
            conn.execute(
                "CREATE TRIGGER products_ins INSTEAD OF INSERT ON products "
                "BEGIN SELECT 1; END"
            )
            conn.execute("DROP TABLE ghost")
            conn.commit()
        finally:
            conn.close()

    def test_unreadable_products_table_is_db_unavailable(self):
        """数据库可打开但商品行无法读取：退出码 1，状态不变、不重试破坏数据。"""
        db = self.tmpdir / "broken_view.sqlite3"
        self.make_products_unreadable_view(db)
        # 购物车记录在失败前后保持不变
        with sqlite3.connect(str(db)) as conn:
            cart_before = conn.execute(
                "SELECT product_id, quantity FROM cart"
            ).fetchall()
        self.assertEqual(cart_before, [("P001", 2)])

        result = self.run_shop(["budget", "2500"], db)
        self.assert_db_unavailable(result)

        with sqlite3.connect(str(db)) as conn:
            cart_after = conn.execute(
                "SELECT product_id, quantity FROM cart"
            ).fetchall()
            # 视图对象与触发器仍在，失败没有改动 schema
            kinds = conn.execute(
                "SELECT type, name FROM sqlite_master "
                "WHERE name IN ('products', 'products_ins', 'cart') "
                "ORDER BY name"
            ).fetchall()
        self.assertEqual(cart_after, cart_before)
        self.assertEqual(
            kinds,
            [("table", "cart"), ("view", "products"), ("trigger", "products_ins")],
        )

        # 重复调用：同样的失败结果，不产生部分输出，也不出现异常堆栈
        result_again = self.run_shop(["budget", "0"], db)
        self.assert_db_unavailable(result_again)

    def test_database_error_message_is_single_line(self):
        """任何数据库失败的标准错误都只有一行固定文字。"""
        db = self.tmpdir / "dir_as_db"
        db.mkdir()
        result = self.run_shop(["budget", str(9223372036854775807)], db)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr.count("\n"), 1)
        self.assertTrue(result.stderr.endswith("\n"))

    def test_healthy_db_still_works_after_other_db_failure(self):
        """一个数据库上的失败不影响另一个健康数据库。"""
        bad_db = self.tmpdir / "bad.sqlite3"
        bad_db.write_text("not a database", encoding="utf-8")
        good_db = self.create_healthy_db(self.tmpdir / "good.sqlite3")

        bad_result = self.run_shop(["budget", "1200"], bad_db)
        self.assert_db_unavailable(bad_result)

        good_result = self.run_shop(["budget", "1200"], good_db)
        self.assertEqual(good_result.returncode, 0, good_result.stderr)
        self.assertEqual(good_result.stderr, "")
        self.assertEqual(good_result.stdout, "P001 虚拟笔记本 1200\n")


if __name__ == "__main__":
    unittest.main()
