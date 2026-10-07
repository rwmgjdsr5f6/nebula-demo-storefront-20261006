#!/usr/bin/env python3
"""shop.py preview 汇总精度的命令行回归测试（大整数边界）。

只使用 Python 3 标准库；在项目目录执行：

    python -m unittest discover

本文件专门验证 preview 对超出单字段 INTEGER 上界的小计与合计仍以完整
十进制整数精确显示，并且优惠金额与应付金额在超大总金额上同样精确：
单价、数量均到达 SQLite INTEGER 上界 9223372036854775807 时，行小计、
总数量、总金额均超过该上界，固定满减 500 只减一次。随后把 P001 单价
设为零，验证零金额商品行继续存在、数量不变、优惠按新总金额重算。

期望结果由固定样例独立确定：大数字面量均手写并用独立算术自检，
既不导入也不调用 shop.py 的任何内部计算函数。
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHOP = Path(__file__).resolve().parent / "shop.py"

MAX_FIELD = 9223372036854775807
SUBTOTAL = 85070591730234615847396907784232501249
TOTAL_QTY = 18446744073709551614
TOTAL_AMOUNT = 170141183460469231694793815568465002498
DISCOUNT = 500
# 改价后仅剩一个行小计的总金额（仍远超满减门槛）
TOTAL_AMOUNT_ONE = SUBTOTAL

# 双上界状态下的完整 preview 输出：优惠 500 只减一次
AT_MAX_PREVIEW = (
    f"P001 虚拟笔记本 {MAX_FIELD} {MAX_FIELD} {SUBTOTAL}\n"
    f"P002 虚拟马克杯 {MAX_FIELD} {MAX_FIELD} {SUBTOTAL}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_AMOUNT}\n"
    f"优惠金额 {DISCOUNT}\n"
    f"应付金额 {TOTAL_AMOUNT - DISCOUNT}\n"
)

# P001 单价置零后：P001 行仍在、小计为零，总金额只剩 P002 的小计
ZERO_PRICE_PREVIEW = (
    f"P001 虚拟笔记本 0 {MAX_FIELD} 0\n"
    f"P002 虚拟马克杯 {MAX_FIELD} {MAX_FIELD} {SUBTOTAL}\n"
    f"总数量 {TOTAL_QTY}\n"
    f"总金额 {TOTAL_AMOUNT_ONE}\n"
    f"优惠金额 {DISCOUNT}\n"
    f"应付金额 {TOTAL_AMOUNT_ONE - DISCOUNT}\n"
)


class ShopPreviewPrecisionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = Path(self._tmp.name)
        self.db = self.tmpdir / "precision.sqlite3"

    def run_shop(self, args):
        cmd = [sys.executable, str(SHOP), "--db", str(self.db)] + list(args)
        env = dict(os.environ)
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            cmd,
            cwd=str(self.tmpdir),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

    def prepare_at_max(self):
        """用 add / price 把两件商品的数量与单价都准备到 INTEGER 上界。"""
        steps = (
            ["add", "P001", str(MAX_FIELD)],
            ["add", "P002", str(MAX_FIELD)],
            ["price", "P001", str(MAX_FIELD)],
            ["price", "P002", str(MAX_FIELD)],
        )
        for args in steps:
            result = self.run_shop(args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "", result.stderr)

    def test_preview_at_max_precision_then_zero_price(self):
        """双上界样例精确显示优惠与应付；改零单价后零金额行仍计数。"""
        # 独立算术自检期望值
        self.assertEqual(MAX_FIELD * MAX_FIELD, SUBTOTAL)
        self.assertEqual(SUBTOTAL + SUBTOTAL, TOTAL_AMOUNT)
        self.assertGreater(TOTAL_AMOUNT, MAX_FIELD)

        self.prepare_at_max()

        result = self.run_shop(["preview"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, AT_MAX_PREVIEW)

        # 重复预览结果一致
        result = self.run_shop(["preview"])
        self.assertEqual(result.stdout, AT_MAX_PREVIEW)

        # P001 单价置零后重算：行仍在、小计为零，优惠按新总金额仍为 500
        result = self.run_shop(["price", "P001", "0"])
        self.assertEqual(result.returncode, 0, result.stderr)

        result = self.run_shop(["preview"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "", result.stderr)
        self.assertEqual(result.stdout, ZERO_PRICE_PREVIEW)


if __name__ == "__main__":
    unittest.main()
