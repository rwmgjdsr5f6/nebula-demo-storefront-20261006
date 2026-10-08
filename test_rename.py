#!/usr/bin/env python3
"""shop.py rename 命令“修改目录商品展示名称”语义的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

所有用例均在独立临时目录中运行：显式传入的数据库文件位于该目录，
未传 --db 时也把子进程的工作目录切到该目录，因此不会接触
项目或用户已有的 shop.sqlite3。每次 run_shop 都是全新进程，
随后的 catalog/show 读取即等价于“重启后”核对持久化结果。
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

# 未改名时的样例目录
INITIAL_CATALOG = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)
SAMPLE_SHOW = (
    "P001 虚拟笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)

# rename P001 演示笔记本 后的目录与购物车：只有名称变化，
# 单价、编号、数量、小计、汇总全部不变
RENAMED_CATALOG = (
    "P001 演示笔记本 1200",
    "P002 虚拟马克杯 2500",
)
RENAMED_SHOW = (
    "P001 演示笔记本 1200 2 2400",
    "P002 虚拟马克杯 2500 1 2500",
    "总数量 3",
    "总金额 4900",
)
RENAMED_PREVIEW = RENAMED_SHOW + ("优惠金额 0", "应付金额 4900")

# 无效名称：空串、纯空白，以及含回车/换行的名称
INVALID_NAMES = ("", " ", "   ", "\t", " \t ", "\n", "\r", "a\nb", "a\rb", " \n ")

# 含非空白字符的有效名称：中文、标点、空格、内部制表符与首尾空格都按原文保存
VALID_NAMES = (
    "演示笔记本",
    "a",
    "A/B%C_笔记本?",
    "演示（本）★ 1",
    "  演示 笔记本 ! ",
    "演\t示",
    "0",
    "虚拟笔记本",  # 改成初始名称同样成功
)


class ShopRenameTests(unittest.TestCase):
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

    def seed_sample(self, db=..., cwd=None):
        """用公开的 add 语义准备固定样例购物车（P001 两件、P002 一件）。"""
        for product_id, quantity in SAMPLE:
            result = self.run_shop(
                ["add", product_id, quantity], db=db, cwd=cwd
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)
            self.assertEqual(result.stdout, f"{product_id} 数量 {quantity}\n")
        return self.tmpdir / "cart.sqlite3" if db is ... else db

    def assert_show(self, db, expected_lines, cwd=None):
        result = self.run_shop(["show"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_preview(self, db, expected_lines, cwd=None):
        result = self.run_shop(["preview"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_catalog(self, db, expected_lines, keyword=None, cwd=None):
        args = ["catalog"] + ([keyword] if keyword is not None else [])
        result = self.run_shop(args, db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        expected = ("\n".join(expected_lines) + "\n") if expected_lines else ""
        self.assertEqual(result.stdout, expected)

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def snapshot(self, db):
        """重新打开数据库读取商品资料与购物车，用于失败前后逐字节对比。"""
        conn = sqlite3.connect(str(db))
        try:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
            name_types = conn.execute(
                "SELECT id, typeof(name) FROM products ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        return products, cart, name_types

    def test_rename_fixed_sample(self):
        """固定验收样例：rename P001 演示笔记本 后 show 行与汇总正确。"""
        db = self.seed_sample()

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 名称 演示笔记本\n")
        self.assertEqual(result.stderr, "")

        self.assert_catalog(db, RENAMED_CATALOG)
        self.assert_show(db, RENAMED_SHOW)
        self.assert_preview(db, RENAMED_PREVIEW)

        # 库中只有名称被改写，单价与购物车数量原样保留且名称仍为 TEXT
        products, cart, name_types = self.snapshot(db)
        self.assertEqual(
            products,
            [("P001", "演示笔记本", 1200), ("P002", "虚拟马克杯", 2500)],
        )
        self.assertEqual(cart, [("P001", 2), ("P002", 1)])
        self.assertEqual(name_types, [("P001", "text"), ("P002", "text")])

    def test_rename_persists_across_restarts(self):
        """名称在重启后保留：连续多个全新进程读到的都是保存后的名称。"""
        db = self.seed_sample()

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_catalog(db, RENAMED_CATALOG)
        self.assert_show(db, RENAMED_SHOW)
        # 再重启一次仍然保留，不会自行回到初始名称
        self.assert_catalog(db, RENAMED_CATALOG)
        self.assert_show(db, RENAMED_SHOW)

    def test_rename_same_name_succeeds(self):
        """改成当前名称（包括初始名称）同样成功，状态不变。"""
        db = self.seed_sample()

        for name in ("演示笔记本", "演示笔记本"):
            result = self.run_shop(["rename", "P001", name], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f"P001 名称 {name}\n")
        self.assert_show(db, RENAMED_SHOW)

        # 改回初始名称也成功
        result = self.run_shop(["rename", "P001", "虚拟笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 名称 虚拟笔记本\n")
        self.assert_catalog(db, INITIAL_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

    def test_duplicate_names_allowed(self):
        """不同商品允许同名：两件商品都可改成同一名称，按编号分别成行。"""
        db = self.seed_sample()

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(["rename", "P002", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 名称 演示笔记本\n")

        self.assert_catalog(
            db,
            ("P001 演示笔记本 1200", "P002 演示笔记本 2500"),
        )
        self.assert_show(
            db,
            (
                "P001 演示笔记本 1200 2 2400",
                "P002 演示笔记本 2500 1 2500",
                "总数量 3",
                "总金额 4900",
            ),
        )

    def test_rename_without_cart_does_not_create_cart_entry(self):
        """未入车商品改名成功但不产生购物车条目，show 仍为空车。"""
        db = self.tmpdir / "fresh.sqlite3"

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 名称 演示笔记本\n")
        self.assertEqual(result.stderr, "")

        self.assert_catalog(
            db,
            ("P001 演示笔记本 1200", "P002 虚拟马克杯 2500"),
        )
        self.assert_show(db, ("总数量 0", "总金额 0"))

        conn = sqlite3.connect(str(db))
        try:
            cart = conn.execute("SELECT product_id FROM cart").fetchall()
        finally:
            conn.close()
        self.assertEqual(cart, [])

    def test_rename_only_changes_target_name(self):
        """改名只影响指定商品：另一商品名称、单价与两车数量均不变。"""
        db = self.seed_sample()

        result = self.run_shop(["rename", "P002", "新马克杯"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 名称 新马克杯\n")

        self.assert_catalog(
            db,
            ("P001 虚拟笔记本 1200", "P002 新马克杯 2500"),
        )
        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 2 2400",
                "P002 新马克杯 2500 1 2500",
                "总数量 3",
                "总金额 4900",
            ),
        )

    def test_names_saved_verbatim(self):
        """含非空白字符的名称按原文保存：首尾空格、内部制表符、标点都不裁剪。"""
        db = self.seed_sample()
        for name in VALID_NAMES:
            with self.subTest(name=name):
                result = self.run_shop(["rename", "P001", name], db=db)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, f"P001 名称 {name}\n")
                products, _, _ = self.snapshot(db)
                self.assertEqual(products[0], ("P001", name, 1200))

    def test_keyword_filter_uses_current_name_only(self):
        """关键词只按当前名称与编号匹配，旧名称不再作为额外匹配来源。"""
        db = self.seed_sample()

        # P001 改名为与旧名没有共同片段的“演示电脑”；P002 名称保持不变
        result = self.run_shop(["rename", "P001", "演示电脑"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        # 当前名称中的片段可以匹配，编号照常匹配
        self.assert_catalog(db, ("P001 演示电脑 1200",), keyword="演示")
        self.assert_catalog(db, ("P001 演示电脑 1200",), keyword="P001")
        # 旧名称片段不再命中 P001（“虚拟”仍命中名称未改的 P002）
        self.assert_catalog(db, ("P002 虚拟马克杯 2500",), keyword="虚拟")
        self.assert_catalog(db, (), keyword="笔记本")
        self.assert_catalog(db, (), keyword="虚拟笔记本")
        # budget --keyword 同样只按当前名称匹配，预算判断不变
        result = self.run_shop(
            ["budget", "2000", "--keyword", "演示"], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 演示电脑 1200\n")
        result = self.run_shop(
            ["budget", "2000", "--keyword", "虚拟笔记本"], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_default_db_in_temp_cwd_supports_rename(self):
        """不传 --db：使用临时工作目录下的 shop.sqlite3，改名同样生效且持久。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        default_db = workdir / "shop.sqlite3"

        self.seed_sample(db=None, cwd=workdir)
        self.assertTrue(default_db.is_file())

        result = self.run_shop(
            ["rename", "P001", "演示笔记本"], db=None, cwd=workdir
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "P001 名称 演示笔记本\n")
        self.assertEqual(result.stderr, "")

        self.assert_catalog(None, RENAMED_CATALOG, cwd=workdir)
        self.assert_show(None, RENAMED_SHOW, cwd=workdir)

    def test_db_equals_form_renames(self):
        """--db=路径 写法与 --db 路径 写法等价。"""
        db = self.tmpdir / "eq.sqlite3"
        cmd = [
            sys.executable,
            str(SHOP),
            f"--db={db}",
            "rename",
            "P001",
            "演示笔记本",
        ]
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            cmd,
            cwd=str(self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 名称 演示笔记本\n")
        self.assert_catalog(
            db,
            ("P001 演示笔记本 1200", "P002 虚拟马克杯 2500"),
        )

    def test_explicit_db_files_have_independent_names(self):
        """两个独立数据库：一个库的改名不影响另一个库的目录与购物车。"""
        db_a = self.seed_sample()
        db_b = self.tmpdir / "b.sqlite3"

        result = self.run_shop(["add", "P002", "1"], db=db_b)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["rename", "P001", "甲库名称"], db=db_a)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(["rename", "P001", "乙库名称"], db=db_b)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_catalog(
            db_a,
            ("P001 甲库名称 1200", "P002 虚拟马克杯 2500"),
        )
        self.assert_catalog(
            db_b,
            ("P001 乙库名称 1200", "P002 虚拟马克杯 2500"),
        )

    def test_invalid_names_rejected_before_db_opened(self):
        """空串、纯空白、含回车/换行：商品名称无效，退出 2，不创建文件。"""
        for name in INVALID_NAMES:
            with self.subTest(name=name):
                fresh = self.tmpdir / f"invalid_{len(name)}_{ord(name[0]) if name else 0}.sqlite3"
                self.assertFalse(fresh.exists())
                result = self.run_shop(["rename", "P001", name], db=fresh)
                self.assert_failure(result, 2, "商品名称无效")
                self.assertFalse(fresh.exists())

    def test_invalid_name_preserves_state(self):
        """名称无效时已保存的名称、价格与购物车记录保持不变。"""
        db = self.seed_sample()
        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.snapshot(db)

        for name in INVALID_NAMES:
            with self.subTest(name=name):
                result = self.run_shop(["rename", "P001", name], db=db)
                self.assert_failure(result, 2, "商品名称无效")
                self.assertEqual(self.snapshot(db), before)

        self.assert_catalog(db, RENAMED_CATALOG)
        self.assert_show(db, RENAMED_SHOW)

    def test_unknown_product_with_valid_name(self):
        """有效名称配未知编号：报未知商品，不新增商品或购物车条目。"""
        db = self.seed_sample()
        before = self.snapshot(db)

        result = self.run_shop(["rename", "P999", "新名称"], db=db)
        self.assert_failure(result, 2, "未知商品")
        self.assertEqual(self.snapshot(db), before)

        # 编号按原样精确匹配：大小写或首尾空白不同都算未知
        for product_id in ("p001", " P001", "P001 ", "P01"):
            with self.subTest(product_id=product_id):
                result = self.run_shop(
                    ["rename", product_id, "新名称"], db=db
                )
                self.assert_failure(result, 2, "未知商品")
        self.assertEqual(self.snapshot(db), before)

        # 全新空库上同样只报未知商品，购物车保持为空
        fresh = self.tmpdir / "fresh.sqlite3"
        result = self.run_shop(["rename", "P999", "新名称"], db=fresh)
        self.assert_failure(result, 2, "未知商品")
        conn = sqlite3.connect(str(fresh))
        try:
            cart = conn.execute("SELECT product_id FROM cart").fetchall()
        finally:
            conn.close()
        self.assertEqual(cart, [])

    def test_arity_errors_and_no_db_created(self):
        """缺少或多余业务参数：参数错误，退出 2，全新路径上不创建文件。"""
        fresh = self.tmpdir / "never.sqlite3"

        result = self.run_shop(["rename"], db=fresh)
        self.assert_failure(result, 2, "参数错误")
        result = self.run_shop(["rename", "P001"], db=fresh)
        self.assert_failure(result, 2, "参数错误")
        result = self.run_shop(["rename", "P001", "a", "b"], db=fresh)
        self.assert_failure(result, 2, "参数错误")
        self.assertFalse(fresh.exists())

    def test_structure_error_precedes_invalid_name(self):
        """结构错误先于名称校验：缺参数时即使名称位空缺也报参数错误。"""
        fresh = self.tmpdir / "never.sqlite3"
        result = self.run_shop(["rename", "P001", "a", ""], db=fresh)
        self.assert_failure(result, 2, "参数错误")
        result = self.run_shop(["rename"], db=fresh)
        self.assert_failure(result, 2, "参数错误")
        self.assertFalse(fresh.exists())

        # 空数据库路径同样属于结构错误，先于名称校验
        result = self.run_shop(["rename", "P001", ""], db="")
        self.assert_failure(result, 2, "参数错误")

    def test_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：名称有效且编号存在，仍只报数据库不可用退出 1。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=directory)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_repeated_restarts_keep_saved_name(self):
        """首次初始化不覆盖已保存名称：重启后目录仍是改名后的内容。"""
        db = self.tmpdir / "init.sqlite3"

        result = self.run_shop(["rename", "P001", "自定义名称"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        # 两次全新进程触发初始化逻辑，已保存名称都不被内置目录覆盖
        self.assert_catalog(
            db,
            ("P001 自定义名称 1200", "P002 虚拟马克杯 2500"),
        )
        self.assert_catalog(
            db,
            ("P001 自定义名称 1200", "P002 虚拟马克杯 2500"),
        )


if __name__ == "__main__":
    unittest.main()
