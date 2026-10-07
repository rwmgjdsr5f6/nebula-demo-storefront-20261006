#!/usr/bin/env python3
"""shop.py preview 汇总精度的命令行回归测试（大整数边界）。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest test_preview_precision
    python -m unittest discover

本文件专门验证 preview 在“单价、数量分别合法（各自不超过 SQLite INTEGER
上界 9223372036854775807），但汇总超过该上界”时仍输出完整十进制整数。
两个样例均把 P001、P002 的数量经公开 add 命令准备为上界，再经公开
price 命令统一改价：

- 单价 1 分：每行小计仍在上界内（=数量本身），只有总数量与总金额
  18446744073709551614 越过单字段 INTEGER 上界（“仅汇总越界”）；
  优惠 500，应付 18446744073709551114。
- 单价 2 分：每行小计 18446744073709551614 已越界，总金额
  36893488147419103228 越界更多（“行小计与汇总同时越界”）；
  优惠仍为 500，应付 36893488147419102728。

两种情况下 preview 都应成功退出 0、标准错误为空，金额保持完整十进制
整数：不因汇总越界报“数量超出范围”或“数据库不可用”，也不出现小数、
科学记数法、截断或异常堆栈。每个样例在两个独立命令进程中各预览一次，
每次都与独立确定的固定预期逐字符比较，而不是以两次输出一致代替正确性。

preview 是纯只读：预览前后的商品资料、购物车记录与数据库对象结构逐项
比对，确认没有保存优惠状态、创建订单或改变数量；同一时刻的 show 仍只
展示优惠前金额。

所有期望数字都是本文件手写的独立字面量，并由仅依赖小整数的独立算术
（上界 + 上界、上界 * 1、上界 * 2、再相加、再减 500）核对，不导入也不
调用 shop.py 的任何内部计算函数。所有用例均在独立临时目录中运行并显式
传入 --db，结束即清理，不会接触项目或用户已有的 shop.sqlite3；临时目录
中也不会冒出默认数据库文件，连续执行任意次数结果相同。
"""

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

# 独立标尺：数量允许达到的 SQLite INTEGER 上界（作为命令行参数与库读回
# 的独立标尺，不引用产品源码中的常量）
MAX_QTY = 9223372036854775807

# 仅汇总越界（单价 1 分）
PRICE_ONE = 1
SUBTOTAL_ONE = 9223372036854775807
TOTAL_QTY = 18446744073709551614
TOTAL_AMOUNT_ONE = 18446744073709551614
DISCOUNT = 500
PAYABLE_ONE = 18446744073709551114

# 行小计与汇总同时越界（单价 2 分）
PRICE_TWO = 2
SUBTOTAL_TWO = 18446744073709551614
TOTAL_AMOUNT_TWO = 36893488147419103228
PAYABLE_TWO = 36893488147419102728

# 名称沿用默认目录（与产品源码默认目录一致，但这里作为独立字面量手写）
NAME_P001 = "虚拟笔记本"
NAME_P002 = "虚拟马克杯"

# 单价 1 分样例的完整 preview 输出：商品行按编号升序，字段间各一个空格，
# 每行以换行结束；随后是 preview 约定的四行汇总，末尾恰好一个换行。
PREVIEW_ONE = (
    f"P001 {NAME_P001} {PRICE_ONE} {MAX_QTY} {SUBTOTAL_ONE}\n"
    f"P002 {NAME_P002} {PRICE_ONE} {MAX_QTY} {SUBTOTAL_ONE}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_AMOUNT_ONE}\n"
    f"优惠金额 {DISCOUNT}\n"
    f"应付金额 {PAYABLE_ONE}\n"
)

# 同一时刻 show 的输出：只有优惠前金额，没有“优惠金额/应付金额”两行
SHOW_ONE = (
    f"P001 {NAME_P001} {PRICE_ONE} {MAX_QTY} {SUBTOTAL_ONE}\n"
    f"P002 {NAME_P002} {PRICE_ONE} {MAX_QTY} {SUBTOTAL_ONE}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_AMOUNT_ONE}\n"
)

# 单价 2 分样例的完整 preview 输出
PREVIEW_TWO = (
    f"P001 {NAME_P001} {PRICE_TWO} {MAX_QTY} {SUBTOTAL_TWO}\n"
    f"P002 {NAME_P002} {PRICE_TWO} {MAX_QTY} {SUBTOTAL_TWO}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_AMOUNT_TWO}\n"
    f"优惠金额 {DISCOUNT}\n"
    f"应付金额 {PAYABLE_TWO}\n"
)

# 单价 2 分样例同一时刻的 show 输出（只到总金额为止）
SHOW_TWO = (
    f"P001 {NAME_P001} {PRICE_TWO} {MAX_QTY} {SUBTOTAL_TWO}\n"
    f"P002 {NAME_P002} {PRICE_TWO} {MAX_QTY} {SUBTOTAL_TWO}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_AMOUNT_TWO}\n"
)

# 汇总行“标签 值”的值部分必须是纯十进制整数：不得出现小数点、科学
# 记数法或负号
_DIGITS = re.compile(r"^[0-9]+$")


class ShopPreviewPrecisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)

    def run_shop(self, args, db=...):
        """以全新子进程运行 shop.py；db 缺省用本用例的独立临时库。

        每次调用都是独立进程，对 preview 的核对即等价于“重新启动命令后”
        核对持久化结果。cwd 设为临时目录，若产品错误地回退到默认库，也
        只会落在临时目录中并被检查到，不会接触项目已有的 shop.sqlite3。
        """
        cmd = [sys.executable, str(SHOP)]
        if db is ...:
            db = self.tmpdir / "precision_preview.sqlite3"
        if db is not None:
            cmd += ["--db", str(db)]
        cmd += list(args)
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"  # 强制子进程按 UTF-8 输出，结果与环境语言无关
        return subprocess.run(
            cmd,
            cwd=str(self.tmpdir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

    def read_db_state(self, db):
        """读取数据库全部对象结构、商品资料与购物车原始记录。

        结构按 sqlite_master 的每个对象（表、索引、触发器、视图）逐项
        取出并比对：新增订单表、保存优惠状态的表/索引/触发器等任何对象
        都会被发现。
        """
        conn = sqlite3.connect(str(db))
        try:
            objects = conn.execute(
                "SELECT type, name, tbl_name, sql FROM sqlite_master "
                "ORDER BY type, name"
            ).fetchall()
            products = conn.execute(
                "SELECT id, name, price FROM products ORDER BY id"
            ).fetchall()
            cart = conn.execute(
                "SELECT product_id, quantity FROM cart ORDER BY product_id"
            ).fetchall()
        finally:
            conn.close()
        return objects, products, cart

    def assert_exact_success(self, result, expected_stdout):
        """核对一次成功调用：退出码 0、stderr 为空、stdout 与固定预期逐字相等。

        退出码与空 stderr 同时排除了“数量超出范围”“数据库不可用”等失败
        路径以及任何异常堆栈。
        """
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", repr(result.stderr))
        self.assertNotIn("Traceback", result.stdout)
        self.assertEqual(result.stdout, expected_stdout)

    def assert_plain_decimal_summaries(self, stdout):
        """四行汇总的值部分必须是完整十进制整数。

        逐字符相等已经排除了格式差异，这里再显式断言没有小数、科学记数法
        （e/E）、负号或千分位，并核对数值的十进制位数确实越过 19 位上界
        的量级，防止期望文本本身被悄悄缩短仍“看起来通过”。
        """
        lines = stdout.splitlines()
        summary = dict(
            line.split(" ", 1) for line in lines if " " in line and
            line.split(" ", 1)[0] in
            ("总数量", "总金额", "优惠金额", "应付金额")
        )
        self.assertEqual(
            sorted(summary),
            ["优惠金额", "应付金额", "总数量", "总金额"],
        )
        for value in summary.values():
            self.assertRegex(value, _DIGITS)
            self.assertNotIn(".", value)
            self.assertNotIn("e", value)
            self.assertNotIn("E", value)
            self.assertNotIn("-", value)

    def prepare_sample(self, unit_price):
        """用公开 add / price 命令准备“数量双上界、统一单价”样例。

        两条 add 把 P001、P002 数量分别加到 SQLite INTEGER 上界（每个
        字段单独合法），两条 price 把单价统一改为 unit_price；每条准备
        命令都核对退出码 0、stderr 为空、stdout 与独立预期完全一致。
        返回临时库路径。
        """
        db = self.tmpdir / "precision_preview.sqlite3"
        steps = (
            (["add", "P001", str(MAX_QTY)], f"P001 数量 {MAX_QTY}\n"),
            (["add", "P002", str(MAX_QTY)], f"P002 数量 {MAX_QTY}\n"),
            (["price", "P001", str(unit_price)],
             f"P001 单价 {unit_price}\n"),
            (["price", "P002", str(unit_price)],
             f"P002 单价 {unit_price}\n"),
        )
        for args, expected_stdout in steps:
            with self.subTest(args=args):
                result = self.run_shop(args, db=db)
                self.assert_exact_success(result, expected_stdout)
        return db

    def assert_expected_products_and_cart(self, products, cart, unit_price):
        """商品资料与购物车记录按独立预期逐值核对（数量未被预览改动）。"""
        self.assertEqual(
            products,
            [
                ("P001", NAME_P001, unit_price),
                ("P002", NAME_P002, unit_price),
            ],
        )
        self.assertEqual(
            cart,
            [("P001", MAX_QTY), ("P002", MAX_QTY)],
        )

    def run_precision_scenario(self, unit_price, expected_preview,
                               expected_show, subtotal, total_amount, payable):
        """两个样例共用的核对流程。

        1. 独立算术核对本用例的大数字面量彼此自洽（不接触产品代码）；
        2. 经公开命令准备样例；
        3. 在两个独立命令进程中各 preview 一次，每次都与同一套独立确定
           的固定预期逐字符比较，并核对纯十进制格式；
        4. 每次预览前后比对数据库对象结构、商品资料、购物车记录；
        5. 同状态下 show 只展示优惠前金额；
        6. 临时目录中没有因漏掉 --db 而产生的默认 shop.sqlite3。
        """
        # 期望值自洽性：小计与汇总完全由小整数算术独立推出
        self.assertEqual(1 * MAX_QTY, SUBTOTAL_ONE)
        self.assertEqual(2 * MAX_QTY, SUBTOTAL_TWO)
        self.assertEqual(MAX_QTY + MAX_QTY, TOTAL_QTY)
        self.assertEqual(SUBTOTAL_ONE + SUBTOTAL_ONE, TOTAL_AMOUNT_ONE)
        self.assertEqual(SUBTOTAL_TWO + SUBTOTAL_TWO, TOTAL_AMOUNT_TWO)
        self.assertEqual(TOTAL_AMOUNT_ONE - DISCOUNT, PAYABLE_ONE)
        self.assertEqual(TOTAL_AMOUNT_TWO - DISCOUNT, PAYABLE_TWO)
        # 本样例关键的越界事实
        self.assertGreater(TOTAL_QTY, MAX_QTY)
        self.assertGreater(total_amount, MAX_QTY)
        if unit_price == PRICE_TWO:
            self.assertGreater(subtotal, MAX_QTY)
        else:
            self.assertLessEqual(subtotal, MAX_QTY)
        # 优惠金额固定、应付与总金额之差恰为优惠
        self.assertEqual(total_amount - payable, DISCOUNT)

        db = self.prepare_sample(unit_price)

        # 准备完成后的数据库基线
        before = self.read_db_state(db)
        objects_before, products_before, cart_before = before
        # 只有初始化创建的两张表及其主键自动索引：没有订单表，也没有
        # 任何保存优惠状态的对象
        self.assertEqual(
            [(t, n) for t, n, _t, _s in objects_before],
            [
                ("index", "sqlite_autoindex_cart_1"),
                ("index", "sqlite_autoindex_products_1"),
                ("table", "cart"),
                ("table", "products"),
            ],
        )
        self.assert_expected_products_and_cart(
            products_before, cart_before, unit_price
        )

        # 两个独立进程各预览一次：每次都独立核对完整文本，并核对预览
        # 前后数据库逐对象、逐记录不变（不是只比较两次输出）
        for _ in range(2):
            state_before = self.read_db_state(db)
            result = self.run_shop(["preview"], db=db)
            self.assert_exact_success(result, expected_preview)
            self.assert_plain_decimal_summaries(result.stdout)
            self.assertEqual(self.read_db_state(db), state_before)

        # 预览结束后再与准备完成时的基线整体比对
        self.assertEqual(self.read_db_state(db), before)
        objects_after, products_after, cart_after = self.read_db_state(db)
        self.assert_expected_products_and_cart(
            products_after, cart_after, unit_price
        )
        self.assertEqual(
            [(t, n) for t, n, _t, _s in objects_after],
            [
                ("index", "sqlite_autoindex_cart_1"),
                ("index", "sqlite_autoindex_products_1"),
                ("table", "cart"),
                ("table", "products"),
            ],
        )

        # 同一状态下 show 仍只展示优惠前金额：无“优惠金额/应付金额”
        show_result = self.run_shop(["show"], db=db)
        self.assert_exact_success(show_result, expected_show)
        self.assertNotIn("优惠金额", show_result.stdout)
        self.assertNotIn("应付金额", show_result.stdout)
        # show 同样是只读的
        self.assertEqual(self.read_db_state(db), before)

        # 默认数据库语义不变：所有调用都显式带了 --db，临时目录中只应有
        # 这一个独立临时库，不得产生 shop.sqlite3
        files = sorted(p.name for p in self.tmpdir.iterdir())
        self.assertEqual(files, ["precision_preview.sqlite3"])

    def test_preview_aggregate_only_overflows_sqlite_integer(self):
        """单价 1：行小计合法，仅总数量/总金额越界，优惠 500，结果精确。"""
        self.run_precision_scenario(
            PRICE_ONE,
            PREVIEW_ONE,
            SHOW_ONE,
            SUBTOTAL_ONE,
            TOTAL_AMOUNT_ONE,
            PAYABLE_ONE,
        )

    def test_preview_subtotal_and_aggregate_overflow_sqlite_integer(self):
        """单价 2：行小计与总数量/总金额全部越界，优惠仍 500，结果精确。"""
        self.run_precision_scenario(
            PRICE_TWO,
            PREVIEW_TWO,
            SHOW_TWO,
            SUBTOTAL_TWO,
            TOTAL_AMOUNT_TWO,
            PAYABLE_TWO,
        )

    def test_preview_precision_repeatable_across_runs(self):
        """整段流程再跑一遍并使用全新临时目录：重复执行结果完全相同。"""
        # 单价 2 的样例覆盖越界面最大；两个独立临时目录各准备各预览，
        # 结果必须与同一固定预期一致，证明可重复且不依赖任何残留状态。
        db = self.prepare_sample(PRICE_TWO)
        for _ in range(2):
            result = self.run_shop(["preview"], db=db)
            self.assert_exact_success(result, PREVIEW_TWO)
        self.assertEqual(
            self.read_db_state(db)[2],
            [("P001", MAX_QTY), ("P002", MAX_QTY)],
        )


if __name__ == "__main__":
    unittest.main()
