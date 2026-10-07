#!/usr/bin/env python3
"""shop.py catalog --sort price 按单价升序浏览的可重复回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

覆盖三个层面：

- 直接调用 shop.parse_catalog_args：四种合法形式的返回值与非法形式的
  SystemExit(2)；以及 parse_args 对 catalog 返回原样参数列表的契约；
- 子进程端到端：按数据库整数分单价数值排序（零在前、同价按编号）、
  关键词与排序组合、不恢复演示价格、不修改商品资料与购物车、大整数；
- 非法参数在打开数据库之前拒绝（不创建库文件），数据库不可用退出 1。

所有用例均在独立临时目录中运行，不会接触项目或用户已有的
shop.sqlite3。
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

# 首次创建数据库时写入的固定目录（默认顺序恰好就是单价升序）
INITIAL_CATALOG = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)

MAX_PRICE = "9223372036854775807"


class ParseCatalogArgsDirectTests(unittest.TestCase):
    """直接调用 catalog 参数解析：不经过子进程，不触碰任何数据库文件。"""

    def assert_parsed(self, args, expected):
        result = shop.parse_catalog_args(args)
        self.assertEqual(result, expected)
        # 解析不修改传入的参数序列
        self.assertEqual(args, list(args))

    def assert_parse_error(self, args):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                shop.parse_catalog_args(args)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "参数错误\n")

    def test_four_valid_forms(self):
        """四种合法形式解析出对应的关键词与排序标志。"""
        self.assert_parsed([], (None, False))
        self.assert_parsed(["笔记"], ("笔记", False))
        self.assert_parsed(["--sort", "price"], (None, True))
        self.assert_parsed(["笔记", "--sort", "price"], ("笔记", True))

    def test_keyword_kept_verbatim(self):
        """含首尾空格或内部空格的关键词原样返回，不裁剪、不拆词。"""
        for keyword in (" 笔记 ", "P001", "虚拟 笔记本", "%", "_", "--sort=price"):
            with self.subTest(keyword=keyword):
                self.assert_parsed([keyword], (keyword, False))
                self.assert_parsed(
                    [keyword, "--sort", "price"], (keyword, True)
                )

    def test_unsupported_sort_value_or_case_rejected(self):
        """排序片段只接受末尾相邻的字面量 --sort price。"""
        for args in (
            ["--sort", "Price"],
            ["--sort", "PRICE"],
            ["--sort", "price2"],
            ["--SORT", "price"],
            ["--sort", ""],
            ["--sort", "price", "id"],
        ):
            with self.subTest(args=args):
                self.assert_parse_error(args)

    def test_single_token_that_merely_resembles_option_is_a_keyword(self):
        """未构成两词排序片段的单个参数一律按关键词返回，不报错。"""
        for token in ("--sort", "--sort=price", "price", "--SORT"):
            with self.subTest(token=token):
                self.assert_parsed([token], (token, False))

    def test_sort_fragment_must_be_at_the_end(self):
        """排序片段不在末尾、重复出现或与多个关键词组合都按参数错误拒绝。"""
        for args in (
            ["--sort", "price", "笔记"],
            ["--sort", "price", "--sort", "price"],
            ["a", "b", "--sort", "price"],
            ["a", "b"],
            ["笔记", "--sort"],
            ["price", "--sort"],
            ["--sortx", "price"],
        ):
            with self.subTest(args=args):
                self.assert_parse_error(args)

    def test_blank_keyword_rejected_even_with_sort(self):
        """空或全空白关键词即使带排序片段也按参数错误拒绝。"""
        for keyword in ("", " ", "\t", "  \t "):
            with self.subTest(keyword=keyword):
                self.assert_parse_error([keyword])
                self.assert_parse_error([keyword, "--sort", "price"])

    def test_single_sort_token_is_a_keyword_not_a_fragment(self):
        """保留旧语义：只有 --sort 一个参数时它是关键词，不构成排序片段。"""
        self.assert_parsed(["--sort"], ("--sort", False))

    def test_parse_args_keeps_raw_catalog_args(self):
        """parse_args 校验 catalog 形式但仍返回原样的参数列表。"""
        self.assertEqual(
            shop.parse_args(["catalog", "--sort", "price"]),
            (shop.DEFAULT_DB, "catalog", ["--sort", "price"]),
        )
        self.assertEqual(
            shop.parse_args(["catalog", "笔记", "--sort", "price"]),
            (shop.DEFAULT_DB, "catalog", ["笔记", "--sort", "price"]),
        )
        self.assertEqual(
            shop.parse_args(["catalog", "--sort"]),
            (shop.DEFAULT_DB, "catalog", ["--sort"]),
        )


class CatalogSortSubprocessTests(unittest.TestCase):
    """端到端：按单价升序的展示行为、参数边界与数据库错误。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py。

        db=None 表示不传 --db；db 其余取值（含 Path）展开为 --db 参数；
        cwd 为 None 时使用临时目录。
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

    def set_price(self, product_id, price, db=...):
        result = self.run_shop(["price", product_id, str(price)], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def assert_products_in_db(self, db, expected):
        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
        self.assertEqual(rows, expected)

    def test_sort_price_on_fresh_db_lists_initial_products(self):
        """全新库执行 catalog --sort price：建库并按单价升序输出固定商品。"""
        db = self.tmpdir / "fresh.sqlite3"
        self.assertFalse(db.exists())

        result = self.run_shop(["catalog", "--sort", "price"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n",
        )
        self.assertTrue(db.is_file())

    def test_price_change_then_sort_price_uses_saved_numeric_price(self):
        """验收场景：price P002 900 后按单价升序，P002(900) 在 P001(1200) 前。

        文本排序会把 "1200" 排在 "900" 之前，因此该顺序同时证明按整数
        数值比较而非按金额文本比较。
        """
        db = self.tmpdir / "demo.sqlite3"
        result = self.run_shop(["price", "P002", "900"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["catalog", "--sort", "price"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            "P002 虚拟马克杯 900\nP001 虚拟笔记本 1200\n",
        )

        # 同一状态下普通 catalog 仍按编号升序
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 900\n",
        )

        # 带关键词“笔记”的新查询只显示 P001
        result = self.run_shop(
            ["catalog", "笔记", "--sort", "price"], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 虚拟笔记本 1200\n")

    def test_equal_prices_fall_back_to_id_order(self):
        """两件商品同价时按编号升序：P001 在 P002 之前。"""
        db = self.tmpdir / "tie.sqlite3"
        self.set_price("P001", 900, db=db)
        self.set_price("P002", 900, db=db)

        result = self.run_shop(["catalog", "--sort", "price"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 900\nP002 虚拟马克杯 900\n",
        )

    def test_zero_price_sorts_before_positive(self):
        """零单价排在正数单价前面。"""
        db = self.tmpdir / "zero.sqlite3"
        # P002 改为 0，P001 保持 1200：编号逆序但单价升序应输出 P002 在前
        self.set_price("P002", 0, db=db)

        result = self.run_shop(["catalog", "--sort", "price"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P002 虚拟马克杯 0\nP001 虚拟笔记本 1200\n",
        )

    def test_max_integer_price_compares_and_displays_exactly(self):
        """单价达到 SQLite 整数上界时仍准确参与比较并完整显示。"""
        db = self.tmpdir / "maxint.sqlite3"
        self.set_price("P001", 0, db=db)
        self.set_price("P002", MAX_PRICE, db=db)

        result = self.run_shop(["catalog", "--sort", "price"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            f"P001 虚拟笔记本 0\nP002 虚拟马克杯 {MAX_PRICE}\n",
        )

    def test_keyword_filter_applied_on_top_of_price_order(self):
        """关键词命中两件商品时仍按单价升序展示，每件只出现一次。"""
        db = self.tmpdir / "kw.sqlite3"
        self.set_price("P002", 900, db=db)

        result = self.run_shop(
            ["catalog", "虚拟", "--sort", "price"], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P002 虚拟马克杯 900\nP001 虚拟笔记本 1200\n",
        )

    def test_keyword_remains_case_sensitive_literal_with_sort(self):
        """排序不改变关键词语义：区分大小写，百分号下划线是普通字符。"""
        db = self.tmpdir / "lit.sqlite3"

        for keyword in ("p001", "%", "_", "P00_", "P00%"):
            with self.subTest(keyword=keyword):
                result = self.run_shop(
                    ["catalog", keyword, "--sort", "price"], db=db
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def test_no_match_with_sort_is_quiet_success(self):
        """带排序但无匹配：标准输出、标准错误均空，退出码 0。"""
        db = self.tmpdir / "none.sqlite3"

        result = self.run_shop(
            ["catalog", "不存在的商品", "--sort", "price"], db=db
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_sort_display_does_not_modify_products_or_cart(self):
        """排序仅影响本次展示：商品资料、价格与购物车均保持不变。"""
        db = self.tmpdir / "keep.sqlite3"
        self.set_price("P002", 900, db=db)
        self.assertEqual(
            self.run_shop(["add", "P001", "2"], db=db).returncode, 0
        )

        for _ in range(3):
            result = self.run_shop(["catalog", "--sort", "price"], db=db)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                result.stdout,
                "P002 虚拟马克杯 900\nP001 虚拟笔记本 1200\n",
            )

        # 价格没有被重置为演示价格，购物车数量也未变化
        self.assert_products_in_db(
            db,
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 900)],
        )
        with sqlite3.connect(str(db)) as conn:
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart"
            ).fetchall()
        self.assertEqual(cart, [("P001", 2)])

    def test_sort_price_accepts_default_db(self):
        """省略 --db 时排序浏览同样作用于当前工作目录下的默认库。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        result = self.run_shop(["catalog", "--sort", "price"], db=None, cwd=workdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n",
        )
        self.assertTrue((workdir / "shop.sqlite3").is_file())

    def test_sort_price_accepts_equals_form_db_option(self):
        """--db=路径 写法与排序片段可以组合使用。"""
        db = self.tmpdir / "eq.sqlite3"
        cmd = [
            sys.executable,
            str(SHOP),
            "--db=" + str(db),
            "catalog",
            "--sort",
            "price",
        ]
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n",
        )

    def test_illegal_forms_are_argument_error_before_db_open(self):
        """所有不合法形式报参数错误退出 2，且不创建数据库文件。"""
        for args in (
            ["catalog", "--sort", "Price"],
            ["catalog", "--SORT", "price"],
            ["catalog", "--sort", "price2"],
            ["catalog", "--sort", "price", "x"],
            ["catalog", "a", "b", "--sort", "price"],
            ["catalog", "a", "b"],
            ["catalog", "--sort", "price", "--sort", "price"],
            ["catalog", "--sort", "price", "笔记"],
            ["catalog", "笔记", "--sort"],
            ["catalog", "price", "--sort"],
            ["catalog", "--sortx", "price"],
            ["catalog", "", "--sort", "price"],
            ["catalog", "  ", "--sort", "price"],
        ):
            with self.subTest(args=args):
                db = self.tmpdir / ("bad_" + str(abs(hash(tuple(args)))) + ".sqlite3")
                result = self.run_shop(args, db=db)
                self.assert_failure(result, 2, "参数错误")
                self.assertFalse(db.exists())

    def test_single_sort_token_stays_a_keyword(self):
        """保留原调用语义：catalog --sort 把 --sort 当作单个关键词。"""
        db = self.tmpdir / "kwonly.sqlite3"
        result = self.run_shop(["catalog", "--sort"], db=db)
        self.assertEqual(result.returncode, 0)
        # 编号与名称都不含字面量 "--sort"：无输出但成功
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_db_unavailable_with_sort_is_database_error(self):
        """参数合法但数据库无法打开：报数据库不可用退出 1，无商品行堆栈。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        result = self.run_shop(["catalog", "--sort", "price"], db=directory)
        self.assert_failure(result, 1, "数据库不可用")

        result = self.run_shop(
            ["catalog", "笔记", "--sort", "price"], db=directory
        )
        self.assert_failure(result, 1, "数据库不可用")


if __name__ == "__main__":
    unittest.main()
