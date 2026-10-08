#!/usr/bin/env python3
"""shop.py rename 命令“修改目录商品名称”语义的可重复回归测试。

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

# 未改过名称时的样例目录与购物车
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

# rename P001 演示笔记本 后的目录与购物车：单价、编号、数量均不变
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

# rename 不接受的新名称：空字符串、纯空白（含纯回车换行）、含回车或换行
INVALID_NAMES = (
    "",
    " ",
    "   ",
    "\t",
    " \t ",
    "\n",
    "\r",
    " \n ",
    "a\nb",
    "a\rb",
    "\n名称",
    "名称\n",
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
        """另起进程调用 show，核对持久化后的完整购物车输出。"""
        result = self.run_shop(["show"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

    def assert_catalog(self, db, expected_lines, cwd=None):
        """另起进程调用 catalog，核对持久化后的完整目录输出。"""
        result = self.run_shop(["catalog"], db=db, cwd=cwd)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, "\n".join(expected_lines) + "\n")

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
        finally:
            conn.close()
        return products, cart

    def assert_cart_product_ids(self, db, expected_ids):
        """直接读库核对购物车条目编号（含“不产生条目”的空列表情形）。"""
        conn = sqlite3.connect(str(db))
        try:
            cart = conn.execute(
                "SELECT product_id FROM cart ORDER BY product_id"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([row[0] for row in cart], expected_ids)

    def test_rename_fixed_sample_then_show(self):
        """固定验收样例：rename P001 演示笔记本 后 show 显示新名称与原有金额。"""
        db = self.seed_sample()

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 名称 演示笔记本\n")
        self.assertEqual(result.stderr, "")

        # 全新进程（重启）查看：名称已更新，单价、编号、数量与汇总不变
        self.assert_catalog(db, RENAMED_CATALOG)
        self.assert_show(db, RENAMED_SHOW)

    def test_rename_persists_across_restarts(self):
        """名称在重启后保留：连续多个全新进程读到的都是保存后的名称。"""
        db = self.seed_sample()

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_catalog(db, RENAMED_CATALOG)
        self.assert_show(db, RENAMED_SHOW)
        # 再重启一次仍然保留，且不会自行回到初始名称 虚拟笔记本
        self.assert_catalog(db, RENAMED_CATALOG)
        self.assert_show(db, RENAMED_SHOW)

    def test_rename_same_name_succeeds(self):
        """改成当前名称同样成功：输出一致，状态不变。"""
        db = self.seed_sample()

        result = self.run_shop(["rename", "P001", "虚拟笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 名称 虚拟笔记本\n")
        self.assertEqual(result.stderr, "")
        self.assert_catalog(db, INITIAL_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

        # 改名后再改成同一名称也成功
        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 名称 演示笔记本\n")
        self.assert_catalog(db, RENAMED_CATALOG)
        self.assert_show(db, RENAMED_SHOW)

    def test_rename_allows_duplicate_names_across_products(self):
        """不同商品可以同名：P002 改成与 P001 相同的名称也成功。"""
        db = self.seed_sample()

        result = self.run_shop(["rename", "P002", "虚拟笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 名称 虚拟笔记本\n")

        self.assert_catalog(
            db,
            ("P001 虚拟笔记本 1200", "P002 虚拟笔记本 2500"),
        )
        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 2 2400",
                "P002 虚拟笔记本 2500 1 2500",
                "总数量 3",
                "总金额 4900",
            ),
        )

    def test_rename_name_kept_verbatim_with_spaces_and_punctuation(self):
        """含中文、标点与首尾空格的名称按原文保存，不裁剪。"""
        db = self.seed_sample()
        name = "  演示笔记本（Pro），2026 版！ "

        result = self.run_shop(["rename", "P001", name], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"P001 名称 {name}\n")
        self.assertEqual(result.stderr, "")

        # 数据库中保存的与输出展示的均为原文，首尾空格不裁剪
        products, _ = self.snapshot(db)
        self.assertEqual(products[0], ("P001", name, 1200))
        self.assert_catalog(db, (f"P001 {name} 1200", "P002 虚拟马克杯 2500"))

    def test_rename_without_cart_does_not_create_cart_entry(self):
        """未入车商品改名成功但不产生购物车条目，show 仍为空车。"""
        db = self.tmpdir / "fresh.sqlite3"

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 名称 演示笔记本\n")
        self.assertEqual(result.stderr, "")

        # 目录名称已更新，购物车没有任何记录
        self.assert_catalog(
            db,
            ("P001 演示笔记本 1200", "P002 虚拟马克杯 2500"),
        )
        self.assert_show(db, ("总数量 0", "总金额 0"))
        self.assert_cart_product_ids(db, [])

    def test_rename_only_changes_target_name(self):
        """改名只影响指定商品名称：另一商品名称、单价与两车数量均不变。"""
        db = self.seed_sample()

        result = self.run_shop(["rename", "P002", "马克杯二代"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P002 名称 马克杯二代\n")

        self.assert_catalog(
            db,
            ("P001 虚拟笔记本 1200", "P002 马克杯二代 2500"),
        )
        self.assert_show(
            db,
            (
                "P001 虚拟笔记本 1200 2 2400",
                "P002 马克杯二代 2500 1 2500",
                "总数量 3",
                "总金额 4900",
            ),
        )

    def test_rename_visible_in_budget_and_preview(self):
        """改名后 budget 与 preview 同样显示新名称，预算判断与满减不变。"""
        db = self.seed_sample()

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["budget", "1200"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 演示笔记本 1200\n")
        self.assertEqual(result.stderr, "")

        result = self.run_shop(["preview"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 演示笔记本 1200 2 2400\n"
            "P002 虚拟马克杯 2500 1 2500\n"
            "总数量 3\n"
            "总金额 4900\n"
            "优惠金额 0\n"
            "应付金额 4900\n",
        )
        self.assertEqual(result.stderr, "")

    def test_catalog_keyword_matches_current_name_only(self):
        """目录关键词以当前名称为准：新名称可匹配，旧名称不再作为匹配来源。"""
        db = self.seed_sample()

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        # 新名称关键词命中 P001
        result = self.run_shop(["catalog", "演示"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 演示笔记本 1200\n")

        # 旧名称独有片段不再命中 P001（P002 名称仍含“虚拟”故仍出现）
        result = self.run_shop(["catalog", "虚拟笔记"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

        # 编号仍按原样参与匹配
        result = self.run_shop(["catalog", "P001"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 演示笔记本 1200\n")

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

        # 重启后默认库中的名称保留
        self.assert_catalog(None, RENAMED_CATALOG, cwd=workdir)
        self.assert_show(None, RENAMED_SHOW, cwd=workdir)

    def test_explicit_db_files_have_independent_names(self):
        """两个独立数据库：一个库的改名不影响另一个库的目录与购物车。"""
        db_a = self.seed_sample()
        db_b = self.tmpdir / "b.sqlite3"

        result = self.run_shop(["add", "P002", "1"], db=db_b)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db_a)
        self.assertEqual(result.returncode, 0, result.stderr)

        # 两库 P001 名称互不影响
        self.assert_catalog(db_a, RENAMED_CATALOG)
        self.assert_catalog(db_b, INITIAL_CATALOG)
        self.assert_show(db_a, RENAMED_SHOW)
        self.assert_show(
            db_b,
            ("P002 虚拟马克杯 2500 1 2500", "总数量 1", "总金额 2500"),
        )

    def test_rename_rejects_invalid_names_and_preserves_state(self):
        """空串、纯空白、含回车换行的名称均被拒绝，失败后状态不变。"""
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

    def test_rename_invalid_name_does_not_create_db(self):
        """名称无效在打开数据库之前拒绝：全新路径上不创建数据库文件。"""
        fresh = self.tmpdir / "never.sqlite3"
        self.assertFalse(fresh.exists())

        for name in INVALID_NAMES:
            with self.subTest(name=name):
                result = self.run_shop(["rename", "P001", name], db=fresh)
                self.assert_failure(result, 2, "商品名称无效")
                self.assertFalse(fresh.exists())

    def test_rename_unknown_product_with_valid_name(self):
        """有效名称配未知编号：报未知商品，不新增商品或购物车条目。"""
        db = self.seed_sample()
        before = self.snapshot(db)

        result = self.run_shop(["rename", "P999", "演示笔记本"], db=db)
        self.assert_failure(result, 2, "未知商品")
        self.assertEqual(self.snapshot(db), before)
        self.assert_cart_product_ids(db, ["P001", "P002"])
        self.assert_show(db, SAMPLE_SHOW)

        # 全新空库上同样只报未知商品，购物车保持为空
        fresh = self.tmpdir / "fresh.sqlite3"
        result = self.run_shop(["rename", "P999", "演示笔记本"], db=fresh)
        self.assert_failure(result, 2, "未知商品")
        self.assert_cart_product_ids(fresh, [])

    def test_rename_error_priority_structure_name_then_product(self):
        """调用结构、名称、编号同时有问题：按参数错误、名称无效、未知商品的顺序报告。"""
        db = self.seed_sample()
        before = self.snapshot(db)

        # 结构错误最先报告，即使名称与编号也都有问题
        result = self.run_shop(["rename", "P999"], db=db)
        self.assert_failure(result, 2, "参数错误")
        result = self.run_shop(["rename", "P999", "", "多出的参数"], db=db)
        self.assert_failure(result, 2, "参数错误")

        # 结构合法时名称无效优先于编号：P999 为未知编号仍先报名称错误
        for name in INVALID_NAMES:
            with self.subTest(name=name):
                result = self.run_shop(["rename", "P999", name], db=db)
                self.assert_failure(result, 2, "商品名称无效")
                self.assertEqual(self.snapshot(db), before)

        # 名称有效时才轮到编号校验
        result = self.run_shop(["rename", "P999", "演示笔记本"], db=db)
        self.assert_failure(result, 2, "未知商品")
        self.assertEqual(self.snapshot(db), before)
        self.assert_show(db, SAMPLE_SHOW)

    def test_rename_without_enough_args_is_argument_error(self):
        """rename 缺少编号或名称：报参数错误，商品资料与购物车不变。"""
        db = self.seed_sample()

        result = self.run_shop(["rename"], db=db)
        self.assert_failure(result, 2, "参数错误")

        result = self.run_shop(["rename", "P001"], db=db)
        self.assert_failure(result, 2, "参数错误")

        self.assert_catalog(db, INITIAL_CATALOG)
        self.assert_show(db, SAMPLE_SHOW)

    def test_rename_with_extra_arg_is_argument_error_and_no_db_created(self):
        """rename 多带参数：报参数错误；全新路径上不创建数据库文件。"""
        fresh = self.tmpdir / "never.sqlite3"
        self.assertFalse(fresh.exists())

        result = self.run_shop(["rename", "P001", "甲", "乙"], db=fresh)
        self.assert_failure(result, 2, "参数错误")
        self.assertFalse(fresh.exists())

    def test_db_path_pointing_at_directory_is_unavailable(self):
        """--db 指向现有目录：不输出成功信息，只报数据库不可用并退出 1。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["rename", "P001", "演示笔记本"], db=directory)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_state_after_all_failures_matches_before(self):
        """汇总：一连串失败后重新查看可用数据库，商品资料与购物车与操作前完全一致。"""
        db = self.seed_sample()
        result = self.run_shop(["rename", "P001", "演示笔记本"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        before = self.snapshot(db)

        for args, code, message in (
            (["rename", "P001", ""], 2, "商品名称无效"),
            (["rename", "P001", "  "], 2, "商品名称无效"),
            (["rename", "P001", "a\nb"], 2, "商品名称无效"),
            (["rename", "P001", "a\rb"], 2, "商品名称无效"),
            (["rename", "P999", "新名字"], 2, "未知商品"),
            (["rename", "P999", ""], 2, "商品名称无效"),
            (["rename", "P001"], 2, "参数错误"),
            (["rename", "P001", "甲", "乙"], 2, "参数错误"),
        ):
            with self.subTest(args=args):
                result = self.run_shop(args, db=db)
                self.assert_failure(result, code, message)
                self.assertEqual(self.snapshot(db), before)

        self.assert_catalog(db, RENAMED_CATALOG)
        self.assert_show(db, RENAMED_SHOW)


if __name__ == "__main__":
    unittest.main()
