#!/usr/bin/env python3
"""shop.py 参数解析流程（parse_args）的回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

覆盖两个层面：

- 直接调用 shop.parse_args：返回值、SystemExit(2)、错误输出、
  传入的参数序列不被修改；
- 子进程端到端：解析失败发生在打开数据库之前（不创建数据库文件），
  参数齐全后的数量/商品/数据库错误仍由原有业务流程报告。

所有涉及文件的用例均在独立临时目录中运行，不会接触项目或用户已有的
shop.sqlite3。
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
SHOP = PROJECT_DIR / "shop.py"

sys.path.insert(0, str(PROJECT_DIR))
import shop  # noqa: E402

# 每个子命令的合法参数个数与相邻的非法个数（README 的参数个数规则）
COMMAND_ARITY_CASES = {
    "add": ((2,), (0, 1, 3)),
    "decrease": ((2,), (0, 1, 3)),
    "set": ((2,), (0, 1, 3)),
    "price": ((2,), (0, 1, 3)),
    "remove": ((1,), (0, 2)),
    "clear": ((0,), (1,)),
    "show": ((0,), (1,)),
    "preview": ((0,), (1, 2)),
    "catalog": ((0, 1), (2, 3)),
}


def make_args(count):
    """生成 count 个互不相同的占位参数。"""
    return [f"arg{i}" for i in range(count)]


class ParseArgsDirectTests(unittest.TestCase):
    """直接调用解析入口：不经过子进程，不触碰任何数据库文件。"""

    def assert_parse_ok(self, argv, expected):
        """解析成功：返回预期三元组，且不修改传入的参数序列。"""
        original = list(argv)
        result = shop.parse_args(argv)
        self.assertEqual(result, expected)
        self.assertEqual(argv, original)

    def assert_parse_error(self, argv):
        """解析失败：抛出 SystemExit(2)，标准输出为空，标准错误只有参数错误一行。"""
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

    def test_valid_arity_for_every_command(self):
        """每个现有子命令按 README 的合法参数个数解析成功，参数原样返回。"""
        for command, (valid_counts, _) in COMMAND_ARITY_CASES.items():
            for count in valid_counts:
                args = make_args(count)
                if command == "catalog" and count == 1:
                    args = ["关键词"]  # 空白占位参数会被 catalog 拒绝，另行覆盖
                with self.subTest(command=command, count=count):
                    self.assert_parse_ok(
                        [command] + args,
                        (shop.DEFAULT_DB, command, args),
                    )

    def test_invalid_arity_for_every_command(self):
        """每个子命令在相邻的非法参数个数下都报参数错误。"""
        for command, (_, invalid_counts) in COMMAND_ARITY_CASES.items():
            for count in invalid_counts:
                with self.subTest(command=command, count=count):
                    self.assert_parse_error([command] + make_args(count))

    def test_default_db_path(self):
        """省略 --db 时数据库路径为默认的 shop.sqlite3。"""
        self.assert_parse_ok(["show"], ("shop.sqlite3", "show", []))

    def test_db_option_separate_and_equals_forms_agree(self):
        """--db 路径 与 --db=路径 两种写法结果一致，路径原样传递。"""
        expected = ("demo.sqlite3", "add", ["P001", "007"])
        self.assert_parse_ok(["--db=demo.sqlite3", "add", "P001", "007"], expected)
        self.assert_parse_ok(["--db", "demo.sqlite3", "add", "P001", "007"], expected)

    def test_db_path_kept_verbatim(self):
        """数据库路径不做任何加工：相对、含空格、含目录的形式都原样返回。"""
        for path in ("a/b/c.sqlite3", "my shop.sqlite3", "./x.sqlite3"):
            with self.subTest(path=path):
                self.assert_parse_ok(
                    ["--db=" + path, "show"], (path, "show", [])
                )
                self.assert_parse_ok(
                    ["--db", path, "show"], (path, "show", [])
                )

    def test_quantity_stays_raw_string(self):
        """数量等参数保持原始字符串：解析入口不转换数值、不查询商品。"""
        db_path, command, args = shop.parse_args(
            ["--db=demo.sqlite3", "add", "P001", "007"]
        )
        self.assertEqual((db_path, command), ("demo.sqlite3", "add"))
        self.assertEqual(args, ["P001", "007"])
        self.assertIsInstance(args[1], str)

    def test_catalog_keyword_kept_verbatim(self):
        """含非空白字符的关键词完整保留：不裁剪、不拆词、不转换大小写。"""
        for keyword in (" 笔记 ", "P001", "虚拟 笔记本", "P"):
            with self.subTest(keyword=keyword):
                self.assert_parse_ok(
                    ["catalog", keyword],
                    (shop.DEFAULT_DB, "catalog", [keyword]),
                )

    def test_catalog_blank_keyword_rejected(self):
        """空字符串或全为空白的关键词按参数错误拒绝。"""
        for keyword in ("", " ", "\t", "  \t "):
            with self.subTest(keyword=keyword):
                self.assert_parse_error(["catalog", keyword])

    def test_no_subcommand_is_error(self):
        """没有任何参数、或只有 --db 选项而没有子命令：参数错误。"""
        self.assert_parse_error([])
        self.assert_parse_error(["--db", "demo.sqlite3"])
        self.assert_parse_error(["--db=demo.sqlite3"])

    def test_unknown_subcommand_is_error(self):
        """未知子命令报参数错误。"""
        self.assert_parse_error(["unknown"])
        self.assert_parse_error(["addd", "P001", "1"])
        self.assert_parse_error(["CATALOG"])

    def test_db_option_missing_or_empty_path_is_error(self):
        """--db 缺少路径、--db= 的值为空：参数错误。"""
        self.assert_parse_error(["--db"])
        self.assert_parse_error(["--db="])
        self.assert_parse_error(["--db=", "show"])

    def test_input_sequence_not_mutated(self):
        """成功与失败两条路径都不修改传入的参数序列（含 --db 消耗的情形）。"""
        cases = [
            ["--db", "demo.sqlite3", "add", "P001", "007"],
            ["--db=demo.sqlite3", "catalog", " 笔记 "],
            ["--db", "demo.sqlite3"],
            ["--db="],
            ["clear", "extra"],
            ["unknown"],
        ]
        for argv in cases:
            original = list(argv)
            with self.subTest(argv=argv):
                try:
                    with contextlib.redirect_stdout(io.StringIO()), (
                        contextlib.redirect_stderr(io.StringIO())
                    ):
                        shop.parse_args(argv)
                except SystemExit:
                    pass
                self.assertEqual(argv, original)


class ParseArgsSubprocessTests(unittest.TestCase):
    """端到端：解析失败先于打开数据库，业务错误仍由原有流程报告。"""

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

    def assert_failure(self, result, code, message):
        """核对失败调用：退出码、空标准输出、独占一行的错误文字、无异常堆栈。"""
        self.assertEqual(result.returncode, code)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, message + "\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_parse_failure_creates_no_default_db(self):
        """参数错误发生在打开数据库之前：默认库文件不会被创建。"""
        default_db = self.tmpdir / "shop.sqlite3"
        for args in (
            [],
            ["unknown"],
            ["add", "P001"],          # 缺少参数
            ["add", "P001", "1", "x"],  # 多出参数
            ["--db"],
            ["--db="],
            ["catalog", ""],
            ["catalog", "a", "b"],
        ):
            with self.subTest(args=args):
                result = self.run_shop(args)
                self.assert_failure(result, 2, "参数错误")
                self.assertFalse(default_db.exists())

    def test_parse_failure_creates_no_explicit_db(self):
        """显式 --db 路径下参数错误同样不创建该数据库文件。"""
        db = self.tmpdir / "explicit.sqlite3"
        for prefix in (["--db", str(db)], ["--db=" + str(db)]):
            with self.subTest(prefix=prefix):
                result = self.run_shop(prefix + ["remove"])
                self.assert_failure(result, 2, "参数错误")
                self.assertFalse(db.exists())

    def test_parse_failure_leaves_existing_db_untouched(self):
        """已有数据库在参数错误后内容不变（不新增购物车记录）。"""
        db = self.tmpdir / "kept.sqlite3"
        result = self.run_shop(["--db", str(db), "add", "P001", "2"])
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["--db", str(db), "add", "P002"])
        self.assert_failure(result, 2, "参数错误")

        result = self.run_shop(["--db", str(db), "show"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200 2 2400\n总数量 2\n总金额 2400\n",
        )

    def test_business_errors_still_come_from_business_flow(self):
        """参数齐全时数量是否合法仍由业务流程判断：解析入口不提前转换数值。"""
        result = self.run_shop(["add", "P999", "0"])
        self.assert_failure(result, 2, "数量必须为正整数")

    def test_unopenable_db_reports_unavailable(self):
        """合法参数指向不可打开的数据库：仍报数据库不可用并以退出码 1 结束。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()
        result = self.run_shop(["--db", str(directory), "show"])
        self.assert_failure(result, 1, "数据库不可用")

    def test_default_db_used_in_cwd(self):
        """省略 --db 时使用当前工作目录下的 shop.sqlite3。"""
        result = self.run_shop(["add", "P001", "1"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 1\n")
        self.assertTrue((self.tmpdir / "shop.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
