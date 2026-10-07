#!/usr/bin/env python3
"""shop.py budget --keyword 形式的可重复回归测试。

budget 保留原有 ``budget 最高单价`` 调用，并新增末尾带完整关键词参数的
``budget 最高单价 --keyword 关键词`` 形式：只输出当前单价不超过上限、
且编号或名称包含整个关键词的商品。本文件覆盖：

- 验收固定样例：budget 2500 --keyword 笔记 只输出 P001 一行（含末行
  换行）；budget 1200 --keyword 马克 成功但标准输出为空。
- 关键词规则与 catalog 完全一致：区分大小写、不拆词、不转换全角字符、
  百分号和下划线是普通字符，含非空白文字时首尾空格也参与匹配；
  编号或名称任一字段命中即可，每件只显示一次，始终按编号升序。
- 名称与单价取自所选库保存值，改价改名后重查立即按新值筛选；
  查询纯只读，不保存预算与关键词条件。
- 只接受两种调用形式：缺少上限或关键词、关键词为空或全为空白、
  重复条件片段、排序片段等一律“参数错误”（退出码 2）；校验顺序为
  调用形式与关键词有效性、上限格式、上限范围，且都在打开数据库前
  拒绝，不创建数据库文件。
- 两种 --db 形式与默认库都可用；数据库无法打开/读取时报
  “数据库不可用”（退出码 1）。

只使用 Python 标准库；在项目目录执行：

    python -m unittest test_budget_keyword
    python -m unittest discover

所有用例均在独立临时目录、显式指定的数据库文件上运行，
结束即清理，不会接触项目或用户已有的 shop.sqlite3。
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

# 首次创建数据库时写入的固定目录
INITIAL_PRODUCTS = (
    ("P001", "虚拟笔记本", 1200),
    ("P002", "虚拟马克杯", 2500),
)

# SQLite INTEGER 上界
MAX_PRICE = 9223372036854775807


class SplitBudgetArgsDirectTests(unittest.TestCase):
    """直接调用拆分入口：不经过子进程，不触碰任何数据库文件。"""

    def test_two_valid_forms_split_as_expected(self):
        """无关键词返回 None；带关键词时关键词完整保留（含首尾空格）。"""
        self.assertEqual(shop.split_budget_args(["2500"]), ("2500", None))
        self.assertEqual(
            shop.split_budget_args(["2500", "--keyword", "笔记"]),
            ("2500", "笔记"),
        )
        for keyword in (" 笔记 ", "P001", "--sort", "%_", "虚拟 笔记本"):
            with self.subTest(keyword=keyword):
                self.assertEqual(
                    shop.split_budget_args(["2500", "--keyword", keyword]),
                    ("2500", keyword),
                )

    def assert_split_error(self, args):
        """结构或关键词无效：SystemExit(2)，标准输出为空，标准错误一行。"""
        original = list(args)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                shop.split_budget_args(args)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "参数错误\n")
        self.assertEqual(args, original)

    def test_blank_or_missing_keyword_rejected(self):
        """关键词空串或纯空白、标记大小写不符、标记位置不对都按参数错误拒绝。

        只列三参数形式：两参数等非法个数由 parse_args 的 arity 先拒绝，
        端到端用例另作覆盖；split_budget_args 本身只拆分三参数片段。
        """
        bad_args = [
            ["2500", "--keyword", ""],
            ["2500", "--keyword", " "],
            ["2500", "--keyword", "\t"],
            ["2500", "--keyword", "  \t "],
            ["2500", "--Keyword", "笔记"],
            ["2500", "--KEYWORD", "笔记"],
            ["2500", "笔记", "--keyword"],   # 标记不在上限之后的固定位置
            ["--keyword", "笔记", "2500"],   # 标记出现在上限位
            ["2500", "笔记", "马克"],         # 中间位置不是标记
        ]
        for args in bad_args:
            with self.subTest(args=args):
                self.assert_split_error(args)

    def test_parse_args_accepts_keyword_form_before_any_db_access(self):
        """parse_args 接受三参形式并原样保留参数，且不触碰数据库。"""
        argv = ["--db=demo.sqlite3", "budget", "2500", "--keyword", "笔记"]
        self.assertEqual(
            shop.parse_args(argv),
            ("demo.sqlite3", "budget", ["2500", "--keyword", "笔记"]),
        )
        self.assertEqual(
            shop.parse_args(["budget", "0"]),
            (shop.DEFAULT_DB, "budget", ["0"]),
        )


class BudgetKeywordShopTests(unittest.TestCase):
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

    def run_raw(self, raw_args, cwd=None):
        """直接以完整参数列表运行，用于 --db=路径 等需要原样拼接的场景。"""
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, str(SHOP)] + raw_args,
            cwd=str(cwd if cwd is not None else self.tmpdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
            timeout=30,
        )

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

    def assert_budget_keyword(self, db, limit, keyword, expected_lines):
        """调用 budget --keyword 并逐字节核对：末行带换行；空匹配为零字节。"""
        result = self.run_shop(
            ["budget", str(limit), "--keyword", keyword], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        expected = "\n".join(expected_lines) + "\n" if expected_lines else ""
        self.assertEqual(result.stdout, expected)
        return result

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

    # ---- 验收固定样例 ---------------------------------------------------

    def test_acceptance_budget_with_keyword_filters_within_limit(self):
        """budget 2500 --keyword 笔记 完整输出只有 P001 一行及换行。"""
        db = self.tmpdir / "demo.sqlite3"
        self.assert_budget_keyword(
            db, 2500, "笔记", ("P001 虚拟笔记本 1200",)
        )
        # 首次使用按原规则初始化了两件演示商品
        self.assertEqual(self.snapshot(db)[0], list(INITIAL_PRODUCTS))

    def test_acceptance_keyword_match_but_over_limit_is_empty(self):
        """budget 1200 --keyword 马克：马克杯 2500 超预算，成功且输出为空。"""
        db = self.tmpdir / "demo.sqlite3"
        result = self.run_shop(["budget", "1200", "--keyword", "马克"], db=db)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")

    # ---- 预算与关键词同时生效 -------------------------------------------

    def test_keyword_matches_id_or_name_within_budget(self):
        """编号或名称包含关键词且单价不超过上限才入选，按编号升序。"""
        db = self.tmpdir / "fresh.sqlite3"
        # 名称命中：笔记在 1200、马克杯在 2500
        self.assert_budget_keyword(
            db, 2500, "笔记", ("P001 虚拟笔记本 1200",)
        )
        # 编号命中：P002 单价 2500，上限 2500（含等于）入选
        self.assert_budget_keyword(
            db, 2500, "P002", ("P002 虚拟马克杯 2500",)
        )
        # 两件都在预算内且关键词同时命中两件：按编号升序、各显示一次
        self.assert_budget_keyword(
            db, 2500, "P",
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 2500"),
        )
        self.assert_budget_keyword(
            db, 2500, "虚拟",
            ("P001 虚拟笔记本 1200", "P002 虚拟马克杯 2500"),
        )
        # 预算把编号/名称命中的商品排除：结果为空但仍成功
        self.assert_budget_keyword(db, 1199, "P", ())
        self.assert_budget_keyword(db, 2500, "不存在", ())

    def test_keyword_form_and_plain_form_agree_on_price_rule(self):
        """关键词为空匹配概念不存在；不带关键词的原形式结果不受影响。"""
        db = self.tmpdir / "fresh.sqlite3"
        plain = self.run_shop(["budget", "1200"], db=db)
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(plain.stdout, "P001 虚拟笔记本 1200\n")
        # 命中全部在预算内商品的关键词（P）与无关键词结果一致
        self.assert_budget_keyword(
            db, 1200, "P", ("P001 虚拟笔记本 1200",)
        )

    # ---- 字面匹配规则（与 catalog 一致） --------------------------------

    def test_keyword_is_case_sensitive_literal_substring(self):
        """区分大小写：小写 p 不命中大写 P；全角字母也不折叠为半角。"""
        db = self.tmpdir / "lit.sqlite3"
        self.assert_budget_keyword(db, MAX_PRICE, "P001", ("P001 虚拟笔记本 1200",))
        for keyword in ("p001", "Ｐ001", "p00", "P001虚拟笔记本"):
            with self.subTest(keyword=keyword):
                self.assert_budget_keyword(db, MAX_PRICE, keyword, ())

    def test_percent_and_underscore_are_plain_characters(self):
        """百分号、下划线不作为通配符：按普通字符做字面包含判断。"""
        db = self.tmpdir / "lit.sqlite3"
        for keyword in ("%", "_", "P00%", "P00_", "%P%", "_P001_"):
            with self.subTest(keyword=keyword):
                self.assert_budget_keyword(db, MAX_PRICE, keyword, ())

    def test_no_word_split_or_width_conversion(self):
        """不按空格拆词、不转换全角：只有完整连续关键词才命中。"""
        db = self.tmpdir / "lit.sqlite3"
        # 含空格的完整串在编号/名称中不存在
        for keyword in ("虚拟 笔记本", "虚拟笔记本 ", " 虚拟笔记本", "P001 "):
            with self.subTest(keyword=keyword):
                self.assert_budget_keyword(db, MAX_PRICE, keyword, ())
        # 全角数字不按半角匹配
        self.assert_budget_keyword(db, MAX_PRICE, "１２００", ())

    def test_surrounding_spaces_participate_in_match(self):
        """关键词含非空白文字时首尾空格也参与匹配，不做裁剪。"""
        db = self.tmpdir / "spaces.sqlite3"
        # 去掉首尾空格能命中，带上后商品行没有该串，故不命中
        self.assert_budget_keyword(db, MAX_PRICE, "笔记", ("P001 虚拟笔记本 1200",))
        self.assert_budget_keyword(db, MAX_PRICE, " 笔记", ())
        self.assert_budget_keyword(db, MAX_PRICE, "笔记 ", ())
        self.assert_budget_keyword(db, MAX_PRICE, " 笔记 ", ())
        self.assert_budget_keyword(db, MAX_PRICE, " P001", ())

    def test_keyword_position_text_is_never_an_option(self):
        """关键词位置的文字不解释为选项：--sort 是普通关键词（无匹配成功）。"""
        db = self.tmpdir / "lit.sqlite3"
        result = self.run_shop(
            ["budget", str(MAX_PRICE), "--keyword", "--sort"], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "")

    # ---- 名称与单价取自库内保存值 ---------------------------------------

    def test_repriced_products_are_refiltered_immediately(self):
        """改价后重查立即按新价筛选，关键词条件与新单价同时生效。"""
        db = self.tmpdir / "reprice.sqlite3"
        self.assertEqual(
            self.run_shop(["price", "P002", "1200"], db=db).returncode, 0
        )
        # P002 现价 1200：上限 1200 + 马克 命中 P002
        self.assert_budget_keyword(
            db, 1200, "马克", ("P002 虚拟马克杯 1200",)
        )
        # 上限 1199：关键词命中但超预算，输出为空
        self.assert_budget_keyword(db, 1199, "马克", ())
        # 前导零上限等价：01200 即 1200
        result = self.run_shop(
            ["budget", "01200", "--keyword", "马克"], db=db
        )
        self.assertEqual(result.stdout, "P002 虚拟马克杯 1200\n")

    def test_renamed_products_match_saved_name(self):
        """直接改库改名后，关键词按保存的新名称匹配，旧名称不再命中。"""
        db = self.tmpdir / "renamed.sqlite3"
        self.run_shop(["catalog"], db=db)
        with sqlite3.connect(str(db)) as conn:
            conn.execute(
                "UPDATE products SET name = ?, price = ? WHERE id = ?",
                ("演示笔记本", 800, "P001"),
            )
            conn.commit()
        self.assert_budget_keyword(
            db, 2500, "演示", ("P001 演示笔记本 800",)
        )
        self.assert_budget_keyword(db, 2500, "虚拟笔记本", ())

    # ---- 输出格式与空结果 -----------------------------------------------

    def test_output_uses_single_spaces_and_trailing_newline_only(self):
        """字段间一个空格、末行一个换行、无表头与汇总。"""
        db = self.tmpdir / "fmt.sqlite3"
        result = self.run_shop(
            ["budget", "2500", "--keyword", "虚拟"], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n",
        )
        for line in result.stdout.splitlines():
            self.assertEqual(len(line.split(" ")), 3)
            self.assertNotIn("  ", line)
        self.assertTrue(result.stdout.endswith("\n"))
        self.assertNotIn("\n\n", result.stdout)

    def test_no_match_is_zero_byte_success(self):
        """无匹配时标准输出为空（零字节）、退出码 0、标准错误为空。"""
        db = self.tmpdir / "empty.sqlite3"
        for args in (
            ["budget", "0", "--keyword", "笔记"],
            ["budget", "1200", "--keyword", "马克"],
            ["budget", str(MAX_PRICE), "--keyword", "P999"],
        ):
            with self.subTest(args=args):
                result = self.run_shop(args, db=db)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(result.stdout, "")

    # ---- 只读性与条件不保存 ---------------------------------------------

    def test_budget_keyword_is_read_only(self):
        """budget --keyword 不修改商品资料和购物车，重复运行结果一致。"""
        db = self.tmpdir / "ro.sqlite3"
        self.run_shop(["add", "P002", "2"], db=db)
        before = self.snapshot(db)
        for args in (
            ["budget", "0", "--keyword", "马克"],
            ["budget", "2500", "--keyword", "P"],
            ["budget", str(MAX_PRICE), "--keyword", "不存在"],
        ):
            self.run_shop(args, db=db)
        self.assertEqual(self.snapshot(db), before)

    def test_budget_keyword_condition_is_not_persisted(self):
        """关键词不落库：带词查询不影响随后的普通 budget 与 catalog。"""
        db = self.tmpdir / "cond.sqlite3"
        self.assert_budget_keyword(db, 1200, "马克", ())
        # 不带关键词的 budget 仍按预算给出 P001，未残留“马克”条件
        result = self.run_shop(["budget", "1200"], db=db)
        self.assertEqual(result.stdout, "P001 虚拟笔记本 1200\n")
        result = self.run_shop(["catalog"], db=db)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200\nP002 虚拟马克杯 2500\n",
        )

    # ---- --db 两种形式与默认库 ------------------------------------------

    def test_equals_form_db_option_works(self):
        """--db=路径 形式对 budget --keyword 同样有效。"""
        db = self.tmpdir / "eq.sqlite3"
        result = self.run_raw(
            ["--db=" + str(db), "budget", "2500", "--keyword", "笔记"]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 虚拟笔记本 1200\n")

    def test_default_db_in_working_directory(self):
        """不传 --db 时使用工作目录下的 shop.sqlite3。"""
        result = self.run_shop(
            ["budget", "2500", "--keyword", "笔记"], db=None
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 虚拟笔记本 1200\n")
        self.assertTrue((self.tmpdir / "shop.sqlite3").exists())

    # ---- 调用形式错误（参数错误） ---------------------------------------

    def test_only_two_forms_are_accepted(self):
        """缺少上限/关键词、空白关键词、重复片段、排序片段等都报参数错误。"""
        bad_argv = [
            ["budget"],                              # 缺少上限
            ["budget", "--keyword", "笔记"],         # 缺少上限（标记占了上限位）
            ["budget", "2500", "--keyword"],          # 缺少关键词
            ["budget", "2500", "--keyword", ""],     # 空关键词
            ["budget", "2500", "--keyword", " "],    # 纯空白关键词
            ["budget", "2500", "--keyword", "\t"],
            ["budget", "2500", "--Keyword", "笔记"],  # 标记区分大小写
            ["budget", "2500", "--keyword", "笔记", "extra"],
            ["budget", "2500", "笔记"],               # 两个位置参数
            ["budget", "2500", "马克", "笔记"],
            ["budget", "2500", "--sort", "price"],   # budget 不接受排序片段
            ["budget", "2500", "--keyword", "a", "--sort", "price"],
            ["budget", "2500", "--keyword", "笔记", "--keyword", "马克"],
            ["budget", "2500", "--keyword", "笔记", "--keyword"],
            ["budget", "--keyword", "笔记", "2500"],  # 标记位置不对
        ]
        for argv in bad_argv:
            with self.subTest(argv=argv):
                self.assert_rejected_before_open(argv, 2, "参数错误")

    def test_structure_and_keyword_validated_before_limit(self):
        """形式与关键词错误优先于上限格式与范围错误。"""
        # 上限既非数字、关键词又为空白：先报参数错误
        self.assert_rejected_before_open(
            ["budget", "abc", "--keyword", " "], 2, "参数错误"
        )
        # 超长超范围上限 + 空白关键词：先报参数错误而非范围错误
        self.assert_rejected_before_open(
            ["budget", "9" * 5000, "--keyword", ""], 2, "参数错误"
        )
        # 标记位置不对时，即使上限是数字也报参数错误
        self.assert_rejected_before_open(
            ["budget", "--keyword", "笔记", "2500"], 2, "参数错误"
        )

    def test_limit_format_and_range_still_validated_after_form(self):
        """形式合法后继续校验上限：格式错误先于范围错误，且不打开数据库。"""
        # 关键词合法、上限格式错误
        for bad in ("", " ", "-1", "+1", "1.5", "１２３", "12分"):
            with self.subTest(bad=bad):
                self.assert_rejected_before_open(
                    ["budget", bad, "--keyword", "笔记"],
                    2, "价格上限必须为非负整数",
                )
        # 关键词合法、上限超范围（含超长纯数字串）
        for bad in (str(MAX_PRICE + 1), "9" * 20, "9" * 5000):
            with self.subTest(bad=bad[:12]):
                self.assert_rejected_before_open(
                    ["budget", bad, "--keyword", "笔记"],
                    2, "价格上限超出范围",
                )
        # 恰好等于上界仍合法（无匹配时成功空输出）
        db = self.tmpdir / "max.sqlite3"
        result = self.run_shop(
            ["budget", str(MAX_PRICE), "--keyword", "P999"], db=db
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    # ---- 数据库不可用 ---------------------------------------------------

    def test_unopenable_db_with_keyword_is_db_unavailable(self):
        """形式合法但数据库路径是目录：报数据库不可用，退出码 1。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()
        result = self.run_shop(
            ["budget", "2500", "--keyword", "笔记"], db=directory
        )
        self.assert_failure(result, 1, "数据库不可用")

    def test_unreadable_products_with_keyword_is_db_unavailable(self):
        """能打开但商品行无法读取：带关键词同样只报数据库不可用退出 1。"""
        db = self.tmpdir / "broken_view.sqlite3"
        conn = sqlite3.connect(str(db))
        try:
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

        result = self.run_shop(
            ["budget", "2500", "--keyword", "笔记"], db=db
        )
        self.assert_failure(result, 1, "数据库不可用")


if __name__ == "__main__":
    unittest.main()
