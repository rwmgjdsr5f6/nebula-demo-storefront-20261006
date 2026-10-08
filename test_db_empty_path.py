#!/usr/bin/env python3
"""shop.py 显式空数据库路径（--db "" 与 --db=）的回归测试。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

修复的差异：--db= 的空值原本就被拒绝，而 --db 后紧跟的长度为零的
独立参数却能通过解析。两种写法现在统一在打开数据库之前报参数错误：
标准输出为空，标准错误恰为“参数错误”加换行，退出码 2，不创建任何
数据库文件，也不读取或修改已有数据库。

覆盖两个层面：

- 直接调用 shop.parse_args：SystemExit(2)、错误输出、传入的参数序列
  不被修改；非空路径（含首尾空格、中文、相对路径）仍原样返回；
- 子进程端到端：空路径优先于业务校验，失败调用不产生 shop.sqlite3，
  已有购物车内容不受影响。

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


class EmptyDbPathParseTests(unittest.TestCase):
    """直接调用解析入口：不经过子进程，不触碰任何数据库文件。"""

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

    def test_empty_db_path_rejected_in_both_forms(self):
        """--db "" 与 --db= 两种写法的空路径都按参数错误拒绝。"""
        self.assert_parse_error(["--db", "", "show"])
        self.assert_parse_error(["--db=", "show"])

    def test_empty_db_path_rejected_for_every_command(self):
        """空路径拒绝发生在打开数据库之前，与各子命令及其参数无关。"""
        for tail in (
            ["show"],
            ["add", "P001", "2"],
            ["decrease", "P001", "1"],
            ["set", "P001", "3"],
            ["price", "P001", "100"],
            ["remove", "P001"],
            ["clear"],
            ["preview"],
            ["catalog"],
            ["budget", "2000"],
        ):
            for prefix in (["--db", ""], ["--db="]):
                with self.subTest(argv=prefix + tail):
                    self.assert_parse_error(prefix + tail)

    def test_nonempty_db_path_kept_verbatim(self):
        """非空路径不受空值校验影响：不裁剪首尾空格，两种写法结果一致。"""
        for path in ("demo.sqlite3", " demo.sqlite3 ", "a/b/我的 商店.sqlite3"):
            with self.subTest(path=path):
                expected = (path, "show", [])
                original = ["--db", path, "show"]
                self.assertEqual(shop.parse_args(list(original)), expected)
                self.assertEqual(shop.parse_args(["--db=" + path, "show"]), expected)

    def test_default_db_path_unaffected(self):
        """省略 --db 时仍解析为默认的 shop.sqlite3。"""
        self.assertEqual(
            shop.parse_args(["show"]), (shop.DEFAULT_DB, "show", [])
        )


class EmptyDbPathSubprocessTests(unittest.TestCase):
    """端到端：空路径失败先于打开数据库，已有数据与默认库均不受影响。"""

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

    def test_empty_path_creates_no_default_db(self):
        """没有默认库时，两种写法的空路径失败调用都不产生 shop.sqlite3。"""
        default_db = self.tmpdir / "shop.sqlite3"
        for args in (["--db", "", "show"], ["--db=", "show"]):
            with self.subTest(args=args):
                result = self.run_shop(args)
                self.assert_failure(result, 2, "参数错误")
                self.assertFalse(default_db.exists())

    def test_empty_path_beats_business_validation(self):
        """空路径优先于业务校验：add P999 0 仍只报参数错误。"""
        for prefix in (["--db", ""], ["--db="]):
            with self.subTest(prefix=prefix):
                result = self.run_shop(prefix + ["add", "P999", "0"])
                self.assert_failure(result, 2, "参数错误")

    def test_empty_path_leaves_existing_cart_untouched(self):
        """默认库加入两件 P001 后，空路径 show 失败，再次正常 show 内容不变。"""
        result = self.run_shop(["add", "P001", "2"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "P001 数量 2\n")

        for prefix in (["--db", ""], ["--db="]):
            with self.subTest(prefix=prefix):
                result = self.run_shop(prefix + ["show"])
                self.assert_failure(result, 2, "参数错误")

        result = self.run_shop(["show"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200 2 2400\n总数量 2\n总金额 2400\n",
        )

    def test_nonempty_path_forms_select_same_file(self):
        """相对、含中文和空格的路径在两种写法下仍选择同一文件。"""
        workdir = self.tmpdir / "样例 目录"
        workdir.mkdir()
        relative = "我的 商店.sqlite3"
        for prefix in (["--db", relative], ["--db=" + relative]):
            with self.subTest(prefix=prefix):
                result = self.run_shop(prefix + ["add", "P001", "1"], cwd=workdir)
                self.assertEqual(result.returncode, 0, result.stderr)
        # 两次加法落在同一文件：数量为 2，且没有产生其他数据库文件
        result = self.run_shop(["--db", relative, "show"], cwd=workdir)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "P001 虚拟笔记本 1200 2 2400\n总数量 2\n总金额 2400\n",
        )
        self.assertEqual(
            sorted(p.name for p in workdir.iterdir()), [relative]
        )


if __name__ == "__main__":
    unittest.main()
