#!/usr/bin/env python3
"""shop.py budget 命令可选排序的可重复回归测试。

覆盖 ``budget 最高单价 [--keyword 关键词] --sort price|price-desc``
新形式的行为：

- 排序只影响本次展示：按库中保存的整数分单价升序（price）或降序
  （price-desc），同价按编号升序；升序时零单价排在正价格前，降序时
  排在正价格后，大整数单价按数值精确比较；不带排序时仍按编号升序。
- 预算与关键词条件照常生效：商品仍须单价不超过上限并满足原有的
  区分大小写字面关键词条件，排序片段位于末尾、有关键词时放在
  关键词片段之后；--keyword 后的参数即使是 --sort 也作为关键词。
- 缺少排序值、排序值不是两个小写写法、重复排序片段、位置错误或
  多余参数都报“参数错误”（退出码 2），在打开数据库之前拒绝；
  排序不修改商品、购物车，也不保存排序偏好。

只使用 Python 标准库；在项目目录执行：

    python -m unittest test_budget_sort
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

# 首次创建数据库时写入的固定目录（编号升序）
INITIAL_CATALOG = (
    "P001 虚拟笔记本 1200",
    "P002 虚拟马克杯 2500",
)

# SQLite INTEGER 上界，单价允许取到该值
MAX_PRICE = 9223372036854775807


class ShopBudgetSortTests(unittest.TestCase):
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

    def set_price(self, db, product_id, price):
        """用公开的 price 语义改价，返回该次调用结果。"""
        result = self.run_shop(["price", product_id, str(price)], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        return result

    def assert_budget(self, db, limit, expected_lines, extra_args=(),
                      keyword=None, cwd=None):
        """另起进程调用 budget，逐字节核对输出与干净的错误流。

        extra_args 原样追加在最后（用于排序片段）；keyword 为 None 时
        不带关键词片段。expected_lines 为空元组时标准输出必须是空串。
        """
        args = ["budget", str(limit)]
        if keyword is not None:
            args += ["--keyword", keyword]
        args += list(extra_args)
        result = self.run_shop(args, db=db, cwd=cwd)
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

    # ---- 验收样例与基本排序 ---------------------------------------------

    def test_acceptance_sort_desc_then_plain_query_restores_id_order(self):
        """验收：新库 budget 2500 --sort price-desc 先 P002 后 P001；
        同库不带排序的查询恢复 P001、P002 的编号升序。"""
        db = self.tmpdir / "demo.sqlite3"
        self.assert_budget(
            db, 2500,
            ("P002 虚拟马克杯 2500", "P001 虚拟笔记本 1200"),
            extra_args=["--sort", "price-desc"],
        )
        # 排序偏好不保存：同库不带排序的查询恢复编号升序
        self.assert_budget(db, 2500, INITIAL_CATALOG)

    def test_sort_price_orders_by_saved_price_then_id(self):
        """--sort price 按库中整数分单价升序，同价按编号升序。"""
        db = self.tmpdir / "cart.sqlite3"
        self.set_price(db, "P002", 900)

        # 单价升序：900 的 P002 排在 1200 的 P001 前面
        self.assert_budget(
            db, 2500,
            ("P002 虚拟马克杯 900", "P001 虚拟笔记本 1200"),
            extra_args=["--sort", "price"],
        )
        # 同一状态下不带排序仍按编号升序
        self.assert_budget(
            db, 2500,
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 900"),
        )

        # 两件商品同价时按编号升序：P001 在前（升序与降序都一样）
        self.set_price(db, "P001", 900)
        self.assert_budget(
            db, 2500,
            ("P001 虚拟笔记本 900", "P002 虚拟马克杯 900"),
            extra_args=["--sort", "price"],
        )
        self.assert_budget(
            db, 2500,
            ("P001 虚拟笔记本 900", "P002 虚拟马克杯 900"),
            extra_args=["--sort", "price-desc"],
        )

    def test_sort_still_filters_by_budget_limit(self):
        """排序不改变预算筛选：超过上限的商品仍不入选。"""
        db = self.tmpdir / "limit.sqlite3"
        # 上限 1200：马克杯超出预算，两种排序都只剩笔记本
        self.assert_budget(
            db, 1200, ("P001 虚拟笔记本 1200",),
            extra_args=["--sort", "price"],
        )
        self.assert_budget(
            db, 1200, ("P001 虚拟笔记本 1200",),
            extra_args=["--sort", "price-desc"],
        )
        # 上限取等：2500 的马克杯恰好入选，降序时排在前面
        self.assert_budget(
            db, 2500,
            ("P002 虚拟马克杯 2500", "P001 虚拟笔记本 1200"),
            extra_args=["--sort", "price-desc"],
        )

    def test_sort_with_keyword_filters_then_sorts(self):
        """关键词与排序组合：先按原有规则筛选，再按单价排序输出。"""
        db = self.tmpdir / "kw.sqlite3"
        self.set_price(db, "P002", 900)

        # 关键词命中一件：只输出该件
        self.assert_budget(
            db, 2500, ("P001 虚拟笔记本 1200",),
            keyword="笔记", extra_args=["--sort", "price"],
        )
        # 关键词命中两件：按单价升序
        self.assert_budget(
            db, 2500,
            ("P002 虚拟马克杯 900", "P001 虚拟笔记本 1200"),
            keyword="虚拟", extra_args=["--sort", "price"],
        )
        # 关键词命中两件：按单价降序
        self.assert_budget(
            db, 2500,
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 900"),
            keyword="虚拟", extra_args=["--sort", "price-desc"],
        )
        # 关键词命中编号子串
        self.assert_budget(
            db, 2500, ("P002 虚拟马克杯 900",),
            keyword="P002", extra_args=["--sort", "price"],
        )

    def test_keyword_matching_rules_unchanged_with_sort(self):
        """带排序时关键词仍区分大小写、不拆词，% 与 _ 保持字面含义。"""
        db = self.tmpdir / "literal.sqlite3"
        sort = ["--sort", "price"]
        # 区分大小写：小写片段不命中大写编号
        self.assert_budget(db, 2500, (), keyword="p001", extra_args=sort)
        # 不拆词：带空格的整串不命中
        self.assert_budget(
            db, 2500, (), keyword="虚拟 笔记本", extra_args=sort,
        )
        # 百分号与下划线是普通字符
        for keyword in ("%", "_", "P%", "P_01"):
            with self.subTest(keyword=keyword):
                self.assert_budget(
                    db, 2500, (), keyword=keyword, extra_args=sort,
                )
        # 含非空白文字时首尾空格参与匹配
        self.assert_budget(db, 2500, (), keyword=" 笔记", extra_args=sort)

    def test_sort_no_match_is_quiet_success(self):
        """排序形式下无匹配：标准输出与标准错误都为空，退出码 0。"""
        db = self.tmpdir / "empty.sqlite3"
        for args in (
            ["budget", "0", "--sort", "price"],
            ["budget", "2500", "--keyword", "P999", "--sort", "price-desc"],
        ):
            with self.subTest(args=args):
                result = self.run_shop(args, db=db)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "")

    def test_sort_numeric_not_text_and_handles_bounds(self):
        """按数值而非文本比较单价；零与大整数上界都准确排序。"""
        db = self.tmpdir / "bounds.sqlite3"
        self.set_price(db, "P002", 0)
        self.set_price(db, "P001", MAX_PRICE)
        # 升序：零单价排在正价格前，INTEGER 上界准确比较与显示
        self.assert_budget(
            db, MAX_PRICE,
            ("P002 虚拟马克杯 0", f"P001 虚拟笔记本 {MAX_PRICE}"),
            extra_args=["--sort", "price"],
        )
        # 降序：零单价排在正价格后
        self.assert_budget(
            db, MAX_PRICE,
            (f"P001 虚拟笔记本 {MAX_PRICE}", "P002 虚拟马克杯 0"),
            extra_args=["--sort", "price-desc"],
        )

        # 上界与零互换后顺序随之颠倒，比较不丢精度
        self.set_price(db, "P001", 0)
        self.set_price(db, "P002", MAX_PRICE)
        self.assert_budget(
            db, MAX_PRICE,
            ("P001 虚拟笔记本 0", f"P002 虚拟马克杯 {MAX_PRICE}"),
            extra_args=["--sort", "price"],
        )
        self.assert_budget(
            db, MAX_PRICE,
            (f"P002 虚拟马克杯 {MAX_PRICE}", "P001 虚拟笔记本 0"),
            extra_args=["--sort", "price-desc"],
        )

    # ---- 只读性与库选择 ---------------------------------------------------

    def test_sort_does_not_touch_products_or_cart(self):
        """排序只影响本次展示：商品资料与购物车内容不变，也不保存偏好。"""
        db = self.tmpdir / "ro.sqlite3"
        self.set_price(db, "P002", 900)
        result = self.run_shop(["add", "P001", "2"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assert_budget(
            db, 2500,
            ("P002 虚拟马克杯 900", "P001 虚拟笔记本 1200"),
            extra_args=["--sort", "price"],
        )

        # 库中商品资料保持改价后的内容，排序查询不重置任何字段
        with sqlite3.connect(str(db)) as conn:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        self.assertEqual(
            products,
            [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 900)],
        )
        self.assertEqual(cart, [("P001", 2)])
        # 排序偏好不保存：下一次不带排序的 budget 仍按编号升序
        self.assert_budget(
            db, 2500,
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 900"),
        )

    def test_sort_on_fresh_db_initializes_then_sorts(self):
        """对首次使用的库先按现有规则初始化目录，再按排序输出。"""
        db = self.tmpdir / "fresh.sqlite3"
        self.assertFalse(db.exists())

        self.assert_budget(
            db, 2500,
            ("P002 虚拟马克杯 2500", "P001 虚拟笔记本 1200"),
            extra_args=["--sort", "price-desc"],
        )
        self.assertTrue(db.is_file())

    def test_sort_accepts_default_db_and_db_equals_form(self):
        """省略 --db 与 --db=路径 两种写法下排序同样生效。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        self.assert_budget(
            None, 2500,
            ("P002 虚拟马克杯 2500", "P001 虚拟笔记本 1200"),
            extra_args=["--sort", "price-desc"], cwd=workdir,
        )
        self.assertTrue((workdir / "shop.sqlite3").is_file())

        db = self.tmpdir / "eq.sqlite3"
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        result = subprocess.run(
            [
                sys.executable, str(SHOP), "--db=" + str(db),
                "budget", "2500", "--sort", "price-desc",
            ],
            cwd=str(self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout,
            "P002 虚拟马克杯 2500\nP001 虚拟笔记本 1200\n",
        )

    # ---- 调用形式错误 -----------------------------------------------------

    def test_invalid_sort_forms_are_argument_error_before_db_open(self):
        """非法排序组合：统一参数错误退出 2，标准输出为空，且不创建数据库。"""
        cases = (
            ["budget", "2500", "--sort"],                   # 缺少排序值
            ["budget", "2500", "--sort", "name"],           # 不支持的排序值
            ["budget", "2500", "--sort", "PRICE"],          # 只接受小写写法
            ["budget", "2500", "--sort", "Price"],
            ["budget", "2500", "--sort", "price_desc"],
            ["budget", "2500", "--sort", "price", "extra"],  # 多余参数
            ["budget", "2500", "--sort", "price", "--sort", "price"],  # 重复片段
            ["budget", "2500", "--sort", "price", "--keyword", "笔记"],  # 位置错误
            ["budget", "2500", "--keyword", "笔记", "--sort"],           # 缺少排序值
            ["budget", "2500", "--keyword", "笔记", "--sort", "PRICE"],  # 值非法
            ["budget", "2500", "--keyword", "笔记", "--sort", "price", "x"],
            ["budget", "2500", "--keyword", "", "--sort", "price"],      # 空关键词
            ["budget", "2500", "--keyword", "  \t ", "--sort", "price"],  # 全空白关键词
        )
        db = self.tmpdir / "explicit.sqlite3"
        for args in cases:
            with self.subTest(args=args):
                self.assert_rejected_before_open(args, 2, "参数错误", db=db)

    def test_keyword_position_sort_text_is_a_keyword(self):
        """--keyword 后的参数即使是 --sort 也作为关键词，可再追加排序片段。"""
        db = self.tmpdir / "kwsort.sqlite3"
        # 关键词就是字面 “--sort”：无匹配，安静成功
        result = self.run_shop(
            ["budget", "2500", "--keyword", "--sort"], db=db
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")
        # 字面 “--sort” 关键词之后还能再追加排序片段
        result = self.run_shop(
            ["budget", "2500", "--keyword", "--sort", "--sort", "price"],
            db=db,
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_form_validation_precedes_limit_validation_with_sort(self):
        """先校验调用结构与关键词，再校验上限格式，最后判断范围。"""
        # 排序形式错误：即使上限格式非法，也只报参数错误
        self.assert_rejected_before_open(
            ["budget", "abc", "--sort", "PRICE"], 2, "参数错误"
        )
        # 形式与关键词有效后，才轮到上限格式
        self.assert_rejected_before_open(
            ["budget", "abc", "--sort", "price"],
            2, "价格上限必须为非负整数",
        )
        self.assert_rejected_before_open(
            ["budget", "abc", "--keyword", "笔记", "--sort", "price-desc"],
            2, "价格上限必须为非负整数",
        )
        # 再轮到上限范围：超过上界与超长纯数字输入
        self.assert_rejected_before_open(
            ["budget", str(MAX_PRICE + 1), "--sort", "price"],
            2, "价格上限超出范围",
        )
        self.assert_rejected_before_open(
            ["budget", "9" * 5000, "--keyword", "笔记", "--sort", "price"],
            2, "价格上限超出范围",
        )

    def test_limit_format_rules_unchanged_with_sort(self):
        """带排序时上限仍接受 ASCII 数字、前导零和零，其余格式拒绝。"""
        db = self.tmpdir / "fmt.sqlite3"
        # 前导零与零在上限位置照旧合法
        self.assert_budget(
            db, "02500", INITIAL_CATALOG, extra_args=["--sort", "price"],
        )
        self.assert_budget(db, "0", (), extra_args=["--sort", "price"])
        for bad in ("", " ", "-1", "+1", "1.5", "１２３", "12a"):
            with self.subTest(bad=bad):
                self.assert_rejected_before_open(
                    ["budget", bad, "--sort", "price"],
                    2, "价格上限必须为非负整数",
                )

    # ---- 数据库错误 -------------------------------------------------------

    def test_sort_db_unavailable(self):
        """数据库无法打开时排序形式同样只报数据库不可用退出 1，无商品行。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()

        for args in (
            ["budget", "2500", "--sort", "price"],
            ["budget", "2500", "--keyword", "笔记", "--sort", "price-desc"],
        ):
            with self.subTest(args=args):
                result = self.run_shop(args, db=directory)
                self.assert_failure(result, 1, "数据库不可用")


if __name__ == "__main__":
    unittest.main()
