#!/usr/bin/env python3
"""shop.py budget 命令可选关键词条件的可重复回归测试。

覆盖 ``budget 最高单价 --keyword 关键词`` 新形式的行为：

- 同时满足预算与文字条件才入选：单价不超过上限，且编号或名称包含
  整个关键词；每件只显示一次，按编号升序，行格式与 budget 原形式、
  catalog 完全一致；无匹配时零字节成功。
- 匹配区分大小写，不拆词、不转换全角字符，百分号与下划线是普通
  字符，关键词含非空白文字时首尾空格也参与匹配。
- 名称与单价取自所选库保存值，改价后重查立即按新价筛选；纯只读、
  不保存筛选条件，默认库与两种 --db 形式照旧。
- 只接受 ``budget 上限``、``budget 上限 --keyword 关键词`` 以及末尾
  追加 ``--sort price|price-desc`` 排序片段的形式；调用形式与关键词
  有效性先于上限格式与范围校验，全部在打开数据库之前拒绝，不创建
  数据库文件。

只使用 Python 标准库；在项目目录执行：

    python -m unittest test_budget_keyword
    python -m unittest discover

所有用例均在独立临时目录、显式指定的数据库文件上运行，
结束即清理，不会接触项目或用户已有的 shop.sqlite3。
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 首次创建数据库时写入的固定目录
INITIAL_PRODUCTS = (
    ("P001", "虚拟笔记本", 1200),
    ("P002", "虚拟马克杯", 2500),
)

# SQLite INTEGER 上界
MAX_PRICE = 9223372036854775807


class ShopBudgetKeywordTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=..., cwd=None):
        """以子进程运行 shop.py，每次都是全新进程（等价于重启后核对）。

        db=None 表示不传 --db；db 其余取值（含 Path）展开为 --db 参数。
        cwd 为 None 时使用临时目录，避免在项目目录生成 shop.sqlite3。
        """
        cmd = [sys.executable, str(SHOP)]
        if db is not None:
            if db is ...:
                db = self.tmpdir / "shop.sqlite3"
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

    def assert_budget_kw(self, db, limit, keyword, expected_lines):
        """调用 budget --keyword 并逐字节核对：末行带换行；
        expected_lines 为空元组时标准输出必须是空串（零字节）。
        """
        result = self.run_shop(
            ["budget", str(limit), "--keyword", keyword], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        expected = "\n".join(expected_lines) + "\n" if expected_lines else ""
        self.assertEqual(result.stdout, expected)
        return result

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def assert_rejected_before_open(self, args, code, message, db=None):
        """参数类错误必须在打开数据库前拒绝：目标数据库文件不得被创建。"""
        if db is None:
            db = self.tmpdir / "never_created.sqlite3"
        self.assertFalse(db.exists())
        result = self.run_shop(args, db=db)
        self.assert_failure(result, code, message)
        self.assertFalse(db.exists(), "参数错误不应创建数据库文件")

    def snapshot(self, db):
        """读取商品与购物车全部内容，供只读性核对。"""
        with sqlite3.connect(str(db)) as conn:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        return products, cart

    # ---- 验收样例与基本筛选 ---------------------------------------------

    def test_acceptance_budget_with_keyword_matches_notebook_only(self):
        """验收：budget 2500 --keyword 笔记 只输出 P001 一行及换行。"""
        db = self.tmpdir / "demo.sqlite3"
        self.assert_budget_kw(
            db, 2500, "笔记", ("P001 虚拟笔记本 1200",)
        )

    def test_acceptance_budget_keyword_no_match_is_empty_success(self):
        """验收：budget 1200 --keyword 马克 成功且标准输出为空。

        马克杯单价 2500 超过上限 1200：文字命中但预算不满足，仍不入选。
        """
        db = self.tmpdir / "demo.sqlite3"
        result = self.run_shop(
            ["budget", "1200", "--keyword", "马克"], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")

    def test_keyword_matches_id_or_name(self):
        """编号或名称包含整个关键词都入选；按编号升序，每件只出现一次。"""
        db = self.tmpdir / "fresh.sqlite3"
        # 编号包含 P00：两件都命中，各自只出现一次
        self.assert_budget_kw(
            db, 2500, "P00",
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 2500"),
        )
        # 名称包含 虚拟：两件都命中
        self.assert_budget_kw(
            db, 2500, "虚拟",
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 2500"),
        )
        # 只命中编号 P002
        self.assert_budget_kw(
            db, 2500, "P002", ("P002 虚拟马克杯 2500",)
        )

    def test_budget_and_keyword_are_both_required(self):
        """预算与文字条件取交集：任一不满足都不入选。"""
        db = self.tmpdir / "fresh.sqlite3"
        # 文字命中但超预算
        self.assert_budget_kw(db, 2499, "马克", ())
        # 预算内但文字不命中
        self.assert_budget_kw(db, 2500, "不存在的文字", ())
        # 上限取等：2500 的马克杯恰好入选
        self.assert_budget_kw(
            db, 2500, "马克", ("P002 虚拟马克杯 2500",)
        )

    def test_output_uses_single_spaces_and_trailing_newline(self):
        """字段之间恰好一个空格，末行一个换行，无表头与汇总。"""
        db = self.tmpdir / "fresh.sqlite3"
        result = self.run_shop(
            ["budget", "2500", "--keyword", "虚拟"], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n",
        )
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertNotIn("\n\n", result.stdout)

    # ---- 字面匹配规则 ---------------------------------------------------

    def test_matching_is_case_sensitive(self):
        """匹配区分大小写：小写编号片段不命中大写编号。"""
        db = self.tmpdir / "fresh.sqlite3"
        self.assert_budget_kw(db, 2500, "p001", ())
        self.assert_budget_kw(
            db, 2500, "P001", ("P001 虚拟笔记本 1200",)
        )

    def test_keyword_is_not_split_on_spaces(self):
        """含空格的关键词按整串匹配，不按空格拆词。"""
        db = self.tmpdir / "fresh.sqlite3"
        # “虚拟 笔记本”带空格，任何字段都不含这整串
        self.assert_budget_kw(db, 2500, "虚拟 笔记本", ())
        # 名称中实际存在的连续子串才命中
        self.assert_budget_kw(
            db, 2500, "虚拟笔记本", ("P001 虚拟笔记本 1200",)
        )

    def test_percent_and_underscore_are_literal(self):
        """百分号与下划线是普通字符，不作为 SQL 通配符。"""
        db = self.tmpdir / "fresh.sqlite3"
        for keyword in ("%", "_", "P%", "%P%", "P_01", "P00_", "%%"):
            with self.subTest(keyword=keyword):
                self.assert_budget_kw(db, 2500, keyword, ())
        # 字面下划线确实出现在字段中时可以命中：先改库制造含 _ 的名称
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE products SET name = ? WHERE id = ?",
                ("促销_笔记本", "P001"),
            )
            conn.commit()
        self.assert_budget_kw(
            db, 2500, "销_笔", ("P001 促销_笔记本 1200",)
        )
        self.assert_budget_kw(db, 2500, "销%笔", ())

    def test_no_fullwidth_conversion(self):
        """不转换全角字符：全角字母/数字与半角不视为相同。"""
        db = self.tmpdir / "fresh.sqlite3"
        self.assert_budget_kw(db, 2500, "Ｐ001", ())
        # 编号是半角 P001/P002：全角Ｐ不命中，半角 P 命中两件
        self.assert_budget_kw(
            db, 2500, "P",
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 2500"),
        )

    def test_surrounding_spaces_participate_in_match(self):
        """关键词含非空白文字时首尾空格也参与匹配。"""
        db = self.tmpdir / "fresh.sqlite3"
        # 名称首尾没有空格：带首尾空格的整串不命中
        self.assert_budget_kw(db, 2500, " 笔记", ())
        self.assert_budget_kw(db, 2500, "笔记 ", ())
        self.assert_budget_kw(db, 2500, " 笔记 ", ())
        # 给名称加上首尾空格后，带同样空格的关键词才命中
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE products SET name = ? WHERE id = ?",
                (" 虚拟笔记本 ", "P001"),
            )
            conn.commit()
        self.assert_budget_kw(
            db, 2500, " 虚拟笔记本", ("P001  虚拟笔记本  1200",)
        )

    # ---- 使用数据库保存值、只读与库选择 ---------------------------------

    def test_uses_saved_prices_after_price_change(self):
        """改价后重查立即按新价筛选：调高后预算内文字命中也消失。"""
        db = self.tmpdir / "priced.sqlite3"
        self.assert_budget_kw(
            db, 1200, "笔记", ("P001 虚拟笔记本 1200",)
        )
        self.assertEqual(
            self.run_shop(["price", "P001", "1300"], db=db).returncode, 0
        )
        self.assert_budget_kw(db, 1200, "笔记", ())
        self.assert_budget_kw(
            db, 1300, "笔记", ("P001 虚拟笔记本 1300",)
        )

    def test_uses_saved_names_from_existing_db(self):
        """名称取库中保存值：直接改库改名后按新名称匹配。"""
        db = self.tmpdir / "renamed.sqlite3"
        self.assertEqual(self.run_shop(["catalog"], db=db).returncode, 0)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE products SET name = ? WHERE id = ?",
                ("临时记事本", "P001"),
            )
            conn.commit()
        self.assert_budget_kw(
            db, 2500, "记事", ("P001 临时记事本 1200",)
        )
        # 旧名称不再匹配
        self.assert_budget_kw(db, 2500, "笔记本", ())

    def test_query_is_read_only(self):
        """带关键词的 budget 不修改商品资料与购物车。"""
        db = self.tmpdir / "ro.sqlite3"
        self.run_shop(["add", "P002", "2"], db=db)
        before = self.snapshot(db)
        for args in (
            ["budget", "2500", "--keyword", "虚拟"],
            ["budget", "0", "--keyword", "P001"],
            ["budget", "1200", "--keyword", "无匹配"],
        ):
            self.assertEqual(self.run_shop(args, db=db).returncode, 0)
        self.assertEqual(self.snapshot(db), before)

    def test_filter_condition_is_not_persisted(self):
        """筛选条件不落库：关键词与预算都只影响本次展示。"""
        db = self.tmpdir / "cond.sqlite3"
        self.assert_budget_kw(db, 1200, "马克", ())
        # 不带关键词的原形式仍能看到预算内全部商品
        result = self.run_shop(["budget", "2500"], db=db)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n",
        )

    def test_default_db_and_both_db_forms(self):
        """默认库与 --db 两种形式对新调用同样有效。"""
        # 不传 --db：使用工作目录下的 shop.sqlite3
        result = self.run_shop(
            ["budget", "2500", "--keyword", "笔记"], db=None
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 虚拟笔记本 1200\n")
        self.assertTrue((self.tmpdir / "shop.sqlite3").exists())

        db = self.tmpdir / "eq.sqlite3"
        # --db=路径 形式
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            [
                sys.executable, str(SHOP), "--db=" + str(db),
                "budget", "2500", "--keyword", "笔记",
            ],
            cwd=str(self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 虚拟笔记本 1200\n")

    # ---- 调用形式错误 ---------------------------------------------------

    def test_invalid_forms_report_argument_error_before_open(self):
        """非法调用形式：缺上限/缺关键词、错大小写、重复片段等全部拒绝。"""
        bad_args = [
            # 缺少上限：--keyword 不能顶替上限位置
            ["budget", "--keyword", "笔记"],
            # 缺少关键词文本
            ["budget", "2500", "--keyword"],
            # 关键词为空或全为空白
            ["budget", "2500", "--keyword", ""],
            ["budget", "2500", "--keyword", " "],
            ["budget", "2500", "--keyword", "\t"],
            ["budget", "2500", "--keyword", "   "],
            # 标记大小写敏感
            ["budget", "2500", "--Keyword", "笔记"],
            ["budget", "2500", "--KEYWORD", "笔记"],
            ["budget", "2500", "--keyword=笔记"],
            # 两个位置参数不是合法形式
            ["budget", "100", "笔记"],
            ["budget", "2500", "笔记", "马克"],
            # 重复条件片段
            ["budget", "100", "--keyword", "笔记", "--keyword", "本"],
            # 多余参数
            ["budget", "100", "--keyword", "笔记", "额外"],
            # 完全缺少参数
            ["budget"],
        ]
        for argv in bad_args:
            with self.subTest(argv=argv):
                self.assert_rejected_before_open(argv, 2, "参数错误")

    def test_keyword_position_text_is_not_an_option(self):
        """关键词位置的文字不解释为选项：以连字符开头也按字面关键词处理。"""
        db = self.tmpdir / "literal.sqlite3"
        # “-x”不命中任何商品：零字节成功，而不是参数错误
        result = self.run_shop(
            ["budget", "2500", "--keyword", "-x"], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        # 字面 “--keyword” 作为关键词同样不解释为标记
        result = self.run_shop(
            ["budget", "2500", "--keyword", "--keyword"], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_form_validation_precedes_limit_validation(self):
        """先校验调用形式与关键词，再校验上限格式与范围。"""
        # 形式错误：即使上限是格式错误的文本，也只报参数错误
        self.assert_rejected_before_open(
            ["budget", "abc", "笔记"], 2, "参数错误"
        )
        # 关键词为空：即使上限格式非法，也先报参数错误
        self.assert_rejected_before_open(
            ["budget", "abc", "--keyword", "  "], 2, "参数错误"
        )
        # 形式与关键词有效后，才轮到上限格式
        self.assert_rejected_before_open(
            ["budget", "abc", "--keyword", "笔记"],
            2, "价格上限必须为非负整数",
        )
        # 形式与关键词有效后，才轮到上限范围
        self.assert_rejected_before_open(
            ["budget", str(MAX_PRICE + 1), "--keyword", "笔记"],
            2, "价格上限超出范围",
        )
        self.assert_rejected_before_open(
            ["budget", "9" * 5000, "--keyword", "笔记"],
            2, "价格上限超出范围",
        )

    def test_limit_format_rules_unchanged_with_keyword(self):
        """带关键词时上限的格式规则与原形式完全一致。"""
        for bad in ("", " ", "-1", "+1", "1.5", "１２３", "12a"):
            with self.subTest(bad=bad):
                self.assert_rejected_before_open(
                    ["budget", bad, "--keyword", "笔记"],
                    2, "价格上限必须为非负整数",
                )

    def test_plain_form_remains_valid(self):
        """原 budget 上限 调用形式保持不变。"""
        db = self.tmpdir / "plain.sqlite3"
        result = self.run_shop(["budget", "1200"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 虚拟笔记本 1200\n")
        # 前导零与零在上限位置照旧合法
        self.assertEqual(
            self.run_shop(["budget", "01200"], db=db).stdout,
            "P001 虚拟笔记本 1200\n",
        )

    # ---- 数据库错误 -----------------------------------------------------

    def test_database_unavailable_reports_error_after_valid_args(self):
        """参数合法但数据库不可用：报“数据库不可用”退出 1，输出为空。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()
        result = self.run_shop(
            ["budget", "2500", "--keyword", "笔记"],
            db=directory,  # 目录不能作为数据库文件打开
        )
        self.assert_failure(result, 1, "数据库不可用")


if __name__ == "__main__":
    unittest.main()
