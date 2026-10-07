#!/usr/bin/env python3
"""shop.py 参数解析流程（parse_args）的回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

覆盖两个层面：

- 直接调用 shop.parse_args：合法/非法参数个数、默认数据库路径、
  两种 --db 写法、catalog 关键词边界、输入序列不被修改，以及解析
  失败时抛出 SystemExit(2) 且标准错误只有一行“参数错误”。
- 子进程端到端：解析失败发生在打开数据库之前（不建库、不改已有库），
  参数齐全时数量与商品的判定仍由业务流程完成。
"""

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import shop

SHOP = Path(__file__).resolve().parent / "shop.py"

# 固定参数个数的子命令及其合法参数个数（catalog 规则特殊，单独覆盖）
FIXED_ARITY = {
    "add": 2,
    "decrease": 2,
    "set": 2,
    "price": 2,
    "remove": 1,
    "clear": 0,
    "show": 0,
}


class ParseArgsDirectTests(unittest.TestCase):
    """直接调用 shop.parse_args 的用例，不经过子进程。"""

    def parse(self, argv):
        return shop.parse_args(list(argv))

    def assert_args_error(self, argv):
        """解析失败：抛出 SystemExit(2)，标准错误恰为一行“参数错误”。"""
        stderr = io.StringIO()
        with self.assertRaises(SystemExit) as caught, \
                contextlib.redirect_stderr(stderr):
            shop.parse_args(list(argv))
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(stderr.getvalue(), "参数错误\n")

    def test_each_command_accepts_its_exact_arity(self):
        """每个子命令在合法参数个数下返回 (默认路径, 子命令, 原样参数列表)。"""
        for command, arity in FIXED_ARITY.items():
            args = [f"arg{i}" for i in range(arity)]
            with self.subTest(command=command):
                self.assertEqual(
                    self.parse([command, *args]),
                    (shop.DEFAULT_DB, command, args),
                )
        # catalog 接受零个或一个关键词
        self.assertEqual(self.parse(["catalog"]), (shop.DEFAULT_DB, "catalog", []))
        self.assertEqual(
            self.parse(["catalog", "笔记"]),
            (shop.DEFAULT_DB, "catalog", ["笔记"]),
        )

    def test_each_command_rejects_neighboring_arities(self):
        """每个子命令在相邻的非法参数个数（少一个、多一个）下报参数错误。"""
        for command, arity in FIXED_ARITY.items():
            for bad_arity in {arity - 1, arity + 1}:
                if bad_arity < 0:
                    continue
                with self.subTest(command=command, arity=bad_arity):
                    self.assert_args_error(
                        [command, *["x"] * bad_arity]
                    )
        # catalog 的相邻非法个数：两个及以上关键词
        self.assert_args_error(["catalog", "a", "b"])
        self.assert_args_error(["catalog", "a", "b", "c"])

    def test_default_db_path_is_shop_sqlite3_in_cwd(self):
        """不传 --db 时数据库路径为相对路径 shop.sqlite3（当前工作目录）。"""
        db_path, command, args = self.parse(["show"])
        self.assertEqual(db_path, "shop.sqlite3")
        self.assertEqual((command, args), ("show", []))

    def test_db_option_separate_and_equals_forms_agree(self):
        """--db 路径 与 --db=路径 两种写法结果一致，路径原样传递。"""
        expected = ("demo.sqlite3", "add", ["P001", "007"])
        self.assertEqual(
            self.parse(["--db=demo.sqlite3", "add", "P001", "007"]), expected
        )
        self.assertEqual(
            self.parse(["--db", "demo.sqlite3", "add", "P001", "007"]), expected
        )
        # 数量等参数保持原始字符串，解析入口不做数值转换
        self.assertEqual(
            self.parse(["--db=demo.sqlite3", "add", "P001", "007"])[2][1], "007"
        )

    def test_db_option_path_is_passed_through_verbatim(self):
        """非空路径原样传递：不去空白、不规范化，重复选项只认第一个。"""
        self.assertEqual(
            self.parse(["--db", " a b.sqlite3 ", "show"]),
            (" a b.sqlite3 ", "show", []),
        )
        self.assertEqual(
            self.parse(["--db==x.sqlite3", "show"]),
            ("=x.sqlite3", "show", []),
        )
        # 第二个 --db 不再按选项处理，而是子命令位置的普通参数
        self.assert_args_error(["--db", "a.sqlite3", "--db", "b.sqlite3", "show"])

    def test_catalog_keyword_boundaries(self):
        """含非空白字符的关键词完整保留首尾空格；空串或纯空白被拒绝。"""
        self.assertEqual(
            self.parse(["catalog", " 笔记 "]),
            (shop.DEFAULT_DB, "catalog", [" 笔记 "]),
        )
        for keyword in ("", " ", "\t", "  \t "):
            with self.subTest(keyword=repr(keyword)):
                self.assert_args_error(["catalog", keyword])

    def test_missing_and_unknown_command_are_args_error(self):
        """没有子命令、未知子命令、--db 缺路径、--db= 空值均为参数错误。"""
        self.assert_args_error([])
        self.assert_args_error(["--db", "demo.sqlite3"])
        self.assert_args_error(["--db=demo.sqlite3"])
        self.assert_args_error(["frobnicate"])
        self.assert_args_error(["--db"])
        self.assert_args_error(["--db="])
        self.assert_args_error(["--db=", "show"])

    def test_input_sequence_is_not_modified(self):
        """parse_args 不修改传入的参数序列，返回的参数是独立列表。"""
        argv = ["--db=demo.sqlite3", "add", "P001", "007"]
        snapshot = list(argv)
        db_path, command, args = self.parse(argv)
        self.assertEqual(argv, snapshot)
        args.append("mutated")
        self.assertEqual(argv, snapshot)

    def test_tuple_input_is_accepted(self):
        """传入元组等其他序列同样按原样解析。"""
        self.assertEqual(
            shop.parse_args(("--db", "demo.sqlite3", "remove", "P001")),
            ("demo.sqlite3", "remove", ["P001"]),
        )


class ParseArgsSubprocessTests(unittest.TestCase):
    """以子进程端到端核对解析失败与业务流程的边界。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, cwd=None):
        cmd = [sys.executable, str(SHOP), *args]
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

    def assert_args_failure(self, result):
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "参数错误\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_parse_failure_happens_before_opening_db(self):
        """解析失败不创建数据库文件：标准输出为空，退出码 2。"""
        db = self.tmpdir / "should_not_exist.sqlite3"
        for args in (
            [],
            ["frobnicate"],
            ["--db", str(db)],          # 第二个 --db 不再识别为选项
            ["--db="],                  # 同上，出现在子命令位置即未知命令
            ["add", "P001"],            # 缺少参数
            ["add", "P001", "1", "x"],  # 多出参数
            ["catalog", ""],
            ["catalog", "a", "b"],
        ):
            with self.subTest(args=args):
                result = self.run_shop(["--db", str(db), *args])
                self.assert_args_failure(result)
                self.assertFalse(db.exists())

    def test_parse_failure_leaves_existing_db_untouched(self):
        """已有数据库在解析失败后内容不变。"""
        db = self.tmpdir / "existing.sqlite3"
        result = self.run_shop(["--db", str(db), "add", "P001", "2"])
        self.assertEqual(result.returncode, 0, result.stderr)
        before = db.read_bytes()

        result = self.run_shop(["--db", str(db), "add", "P001"])
        self.assert_args_failure(result)
        self.assertEqual(db.read_bytes(), before)

        result = self.run_shop(["--db", str(db), "show"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("P001 虚拟笔记本 1200 2 2400\n", result.stdout)

    def test_business_validation_still_happens_after_parsing(self):
        """参数齐全时数量是否合法仍由业务流程判断，解析入口不提前转换。"""
        db = self.tmpdir / "biz.sqlite3"
        result = self.run_shop(["--db", str(db), "add", "P999", "0"])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数量必须为正整数\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_unopenable_db_reports_unavailable_after_successful_parse(self):
        """合法参数指向不可打开的数据库：报数据库不可用，退出码 1。"""
        directory = self.tmpdir / "a_directory"
        directory.mkdir()
        result = self.run_shop(["--db", str(directory), "show"])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "数据库不可用\n")
        self.assertNotIn("Traceback", result.stderr)

    def test_equals_form_db_option_end_to_end(self):
        """--db=路径 写法端到端可用，数量按原始字符串解析为 7。"""
        db = self.tmpdir / "demo.sqlite3"
        result = self.run_shop([f"--db={db}", "add", "P001", "007"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(result.stdout, "P001 数量 7\n")

    def test_default_db_lands_in_cwd(self):
        """省略 --db 时在子进程工作目录下创建 shop.sqlite3。"""
        workdir = self.tmpdir / "work"
        workdir.mkdir()
        result = self.run_shop(["add", "P001", "1"], cwd=workdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((workdir / "shop.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
