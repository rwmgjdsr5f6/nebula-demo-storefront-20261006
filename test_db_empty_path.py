#!/usr/bin/env python3
"""shop.py 显式 --db 空路径拒绝差异的回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest test_db_empty_path
    python -m unittest discover

背景：--db 路径 与 --db=路径 两种数据库选择写法此前不一致——
--db= 的空值会被按参数错误拒绝，而分开传参时长度为零的独立路径参数
（["--db", "", ...]）却能通过解析，随后 sqlite3.connect("") 会创建
临时数据库。修复后两种写法统一：显式选择数据库时空路径一律报参数错误，
且发生在打开数据库之前。

覆盖两个层面：

- 直接调用 shop.parse_args：["--db", "", "show"] 与 ["--db=", "show"]
  都抛出 SystemExit(2)，标准输出为空、标准错误恰为“参数错误”加换行，
  传入的参数序列保持不变；空路径优先于数量等业务校验；非空路径
  （相对路径、含中文与空格、首尾空格）在两种写法下仍原样传递且指向
  同一文件，省略 --db 仍返回默认路径；
- 子进程端到端：空路径失败不创建默认库或任何临时文件、不读取或修改
  已有数据库；独立样例目录中先按默认价格加入两件 P001，空路径 show
  失败后正常 show 仍得到完整商品行与汇总；缺参、额外参数、未知子命令
  仍按原规则拒绝；合法但无法打开的路径仍报“数据库不可用”退出 1。

所有涉及文件的用例均在独立临时目录中运行，不会接触项目或用户已有的
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

# 参数个数合法的代表性现有子命令序列：空路径检查必须先于所有业务流程
ARITY_VALID_COMMAND_CASES = [
    ["show"],
    ["preview"],
    ["clear"],
    ["add", "P001", "1"],
    ["decrease", "P001", "1"],
    ["set", "P001", "1"],
    ["price", "P001", "1"],
    ["remove", "P001"],
    ["catalog"],
    ["budget", "1000"],
]


def empty_path_argv(command_args, separate):
    """构造空路径选择数据库的两种写法：分开传参或 --db= 形式。"""
    if separate:
        return ["--db", ""] + command_args
    return ["--db="] + command_args


class EmptyDbPathParseTests(unittest.TestCase):
    """直接调用解析入口：不经过子进程，不触碰任何数据库文件。"""

    def assert_empty_path_parse_error(self, argv):
        """空路径：SystemExit(2)、空标准输出、标准错误仅参数错误一行，入参不变。"""
        original = list(argv)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as caught:
                shop.parse_args(argv)
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "参数错误\n")
        self.assertEqual(argv, original)

    def test_empty_path_rejected_both_forms_with_show(self):
        """验收点：["--db", "", "show"] 与 ["--db=", "show"] 都抛 SystemExit(2)。"""
        self.assert_empty_path_parse_error(["--db", "", "show"])
        self.assert_empty_path_parse_error(["--db=", "show"])

    def test_empty_path_rejected_for_every_existing_subcommand(self):
        """两种空路径写法配合任意参数合法的现有子命令都在解析阶段被拒绝。"""
        for command_args in ARITY_VALID_COMMAND_CASES:
            for separate in (True, False):
                argv = empty_path_argv(command_args, separate)
                with self.subTest(argv=argv):
                    self.assert_empty_path_parse_error(argv)

    def test_empty_path_takes_precedence_over_business_validation(self):
        """空路径先于数量等业务校验：add P999 0 只报参数错误。

        数量 0 本身应报“数量必须为正整数”、P999 是未知商品，但空路径
        拒绝发生在打开数据库与业务校验之前，错误只能是参数错误。
        """
        self.assert_empty_path_parse_error(["--db", "", "add", "P999", "0"])
        self.assert_empty_path_parse_error(["--db=", "add", "P999", "0"])
        # 业务参数个数本身也不合法时，结论不变（仍是参数错误、退出 2）
        self.assert_empty_path_parse_error(["--db", "", "add", "P999"])
        self.assert_empty_path_parse_error(["--db=", "add", "P999", "0", "x"])

    def test_only_zero_length_path_rejected(self):
        """只拒绝长度为零的字符串：纯空白等非空路径按原样返回，不裁剪。"""
        for path in (" ", "\t", " 　 "):
            for separate in (True, False):
                with self.subTest(path=path, separate=separate):
                    argv = ["--db", path, "show"] if separate else ["--db=" + path, "show"]
                    original = list(argv)
                    self.assertEqual(shop.parse_args(argv), (path, "show", []))
                    self.assertEqual(argv, original)

    def test_nonempty_paths_verbatim_both_forms_agree(self):
        """非空路径在两种写法下原样返回且结果一致：相对、含中文与空格。"""
        for path in ("data.sqlite3", os.path.join("样例 目录", "我的 店铺.sqlite3")):
            with self.subTest(path=path):
                self.assertEqual(
                    shop.parse_args(["--db", path, "add", "P001", "2"]),
                    shop.parse_args(["--db=" + path, "add", "P001", "2"]),
                )
                self.assertEqual(
                    shop.parse_args(["--db", path, "show"]), (path, "show", [])
                )

    def test_legal_input_return_convention_unchanged(self):
        """合法输入保留现有三元组返回约定，业务参数保持原文。"""
        self.assertEqual(
            shop.parse_args(["--db", "demo.sqlite3", "add", "P001", "007"]),
            ("demo.sqlite3", "add", ["P001", "007"]),
        )
        self.assertEqual(
            shop.parse_args(["--db=demo.sqlite3", "add", "P001", "007"]),
            ("demo.sqlite3", "add", ["P001", "007"]),
        )
        # 省略 --db 仍使用默认路径 shop.sqlite3
        self.assertEqual(shop.parse_args(["show"]), (shop.DEFAULT_DB, "show", []))

    def test_input_sequence_not_mutated_on_empty_path_error(self):
        """空路径失败路径同样不修改传入的参数序列。"""
        for argv in (["--db", "", "show"], ["--db=", "show"], ["--db", ""]):
            original = list(argv)
            with self.subTest(argv=argv):
                with contextlib.redirect_stdout(io.StringIO()), (
                    contextlib.redirect_stderr(io.StringIO())
                ):
                    with self.assertRaises(SystemExit):
                        shop.parse_args(argv)
                self.assertEqual(argv, original)


class EmptyDbPathSubprocessTests(unittest.TestCase):
    """端到端：空路径在打开数据库前被拒绝，且不影响既有校验与数据。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, cwd=None):
        cmd = [sys.executable, str(SHOP)] + list(args)
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

    def assert_arg_error(self, result):
        """核对参数错误：退出码 2、空标准输出、标准错误仅参数错误一行、无堆栈。"""
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "参数错误\n")
        self.assertNotIn("Traceback", result.stderr)

    def snapshot_dir(self):
        """递归记录目录内全部文件及内容指纹，用于证明失败调用不留任何文件。"""
        entries = {}
        for root, dirs, files in os.walk(self.tmpdir):
            for name in files:
                path = Path(root) / name
                entries[str(path.relative_to(self.tmpdir))] = path.read_bytes()
        return entries

    def test_empty_path_fails_before_opening_any_database(self):
        """空路径配合各现有子命令：报参数错误，目录内不新增/改动任何文件。

        sqlite3.connect("") 本会创建临时数据库；解析阶段拒绝后默认库、
        临时库都不应出现。
        """
        cases = []
        for command_args in ARITY_VALID_COMMAND_CASES:
            cases.append(["--db", ""] + command_args)
            cases.append(["--db="] + command_args)
        # 空路径优先于业务校验的命令行确认
        cases += [
            ["--db", "", "add", "P999", "0"],
            ["--db=", "add", "P999", "0"],
        ]
        for args in cases:
            with self.subTest(args=args):
                before = self.snapshot_dir()
                result = self.run_shop(args)
                self.assert_arg_error(result)
                self.assertFalse((self.tmpdir / "shop.sqlite3").exists())
                self.assertEqual(self.snapshot_dir(), before)

    def test_no_default_db_when_starting_empty(self):
        """没有默认库时，空路径失败调用不产生 shop.sqlite3。"""
        self.assertEqual(os.listdir(self.tmpdir), [])
        for args in (["--db", "", "show"], ["--db=", "show"]):
            with self.subTest(args=args):
                result = self.run_shop(args)
                self.assert_arg_error(result)
                self.assertFalse((self.tmpdir / "shop.sqlite3").exists())
                self.assertEqual(os.listdir(self.tmpdir), [])

    def test_sample_dir_add_then_empty_show_fails_then_show_intact(self):
        """独立样例目录验收：默认库加两件 P001，空路径 show 失败后数据不变。"""
        result = self.run_shop(["add", "P001", "2"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 2\n")
        default_db = self.tmpdir / "shop.sqlite3"
        self.assertTrue(default_db.is_file())

        for args in (["--db", "", "show"], ["--db=", "show"]):
            with self.subTest(args=args):
                self.assert_arg_error(self.run_shop(args))
                # 失败后默认库仍然存在且未被替换
                self.assertTrue(default_db.is_file())

        result = self.run_shop(["show"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200 2 2400\n总数量 2\n总金额 2400\n",
        )

        # 直接核对落库内容：表结构与数据均保持修复前的兼容形态
        conn = sqlite3.connect(str(default_db))
        try:
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(products, [("P001", "虚拟笔记本", 1200), ("P002", "虚拟马克杯", 2500)])
        self.assertEqual(cart, [("P001", 2)])

    def test_empty_path_does_not_touch_existing_database(self):
        """已有数据库在空路径失败后内容与结构不变（不被读取或修改）。"""
        db = self.tmpdir / "kept.sqlite3"
        result = self.run_shop(["--db", str(db), "add", "P001", "2"])
        self.assertEqual(result.returncode, 0, result.stderr)
        before = db.read_bytes()

        for args in (
            ["--db", "", "show"],
            ["--db=", "show"],
            ["--db", "", "add", "P002", "3"],
            ["--db=", "clear"],
        ):
            with self.subTest(args=args):
                self.assert_arg_error(self.run_shop(args))
                self.assertEqual(db.read_bytes(), before)

        # 正常选择该库时购物车仍是样例内容
        result = self.run_shop(["--db", str(db), "show"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200 2 2400\n总数量 2\n总金额 2400\n",
        )

    def test_business_validation_still_active_without_empty_path(self):
        """对照：路径合法时数量等业务错误仍由原有流程报告。"""
        biz_dir = self.tmpdir / "biz"
        biz_dir.mkdir()
        # add P999 0：数量格式校验在业务流程中报数量错误（退出 2）
        result = self.run_shop(["add", "P999", "0"], cwd=biz_dir)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数量必须为正整数\n")
        # 业务校验发生在建库之后，与空路径场景的“不创建任何文件”互不干扰
        self.assertTrue((biz_dir / "shop.sqlite3").is_file())

    def test_relative_non_ascii_spaced_path_forms_select_same_file(self):
        """相对路径、含中文和空格：两种写法选择同一文件，路径不被重写。"""
        rel_dir = Path("样例 目录")
        (self.tmpdir / rel_dir).mkdir()
        rel_db = rel_dir / "我的 店铺.sqlite3"

        result = self.run_shop(["--db", str(rel_db), "add", "P001", "2"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.tmpdir / rel_db).is_file())

        # 用另一种写法读到同一文件的同一份数据
        result = self.run_shop(["--db=" + str(rel_db), "show"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200 2 2400\n总数量 2\n总金额 2400\n",
        )

        # 反过来再写一次：等号形式改价、分开形式读回
        result = self.run_shop(["--db=" + str(rel_db), "price", "P001", "999"])
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_shop(["--db", str(rel_db), "show"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 999 2 1998\n总数量 2\n总金额 1998\n",
        )

    def test_other_parse_rules_unchanged(self):
        """缺参、额外参数、未知子命令仍按原规则报参数错误且不建库。"""
        for args in (
            ["add", "P001"],            # 缺少参数
            ["add", "P001", "1", "x"],  # 额外参数
            ["frobnicate"],             # 未知子命令
            ["--db", "x.sqlite3"],      # 有 --db 但无子命令
            ["--db"],                   # --db 缺少路径
            ["--db="],                  # 等号形式且无子命令
        ):
            with self.subTest(args=args):
                result = self.run_shop(args)
                self.assert_arg_error(result)
                self.assertFalse((self.tmpdir / "shop.sqlite3").exists())

    def test_legal_unopenable_path_still_reports_db_unavailable(self):
        """合法非空路径无法打开时仍只报数据库不可用，退出码 1。"""
        directory = self.tmpdir / "一个目录"
        directory.mkdir()
        for args in (
            ["--db", str(directory), "show"],
            ["--db=" + str(directory), "show"],
        ):
            with self.subTest(args=args):
                result = self.run_shop(args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr, "数据库不可用\n")
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
