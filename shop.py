#!/usr/bin/env python3
"""本地虚拟商店命令行：固定商品目录、加入购物车、查看购物车。

仅使用 Python 3 标准库，数据保存在 SQLite 数据库中，可完全离线运行。
"""

import sqlite3
import sys

DEFAULT_DB = "shop.sqlite3"

# 固定商品目录：(编号, 名称, 单价（分）)
PRODUCTS = (
    ("P001", "虚拟笔记本", 1200),
    ("P002", "虚拟马克杯", 2500),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS product (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    price_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cart_item (
    code     TEXT PRIMARY KEY REFERENCES product(code),
    quantity INTEGER NOT NULL CHECK (quantity > 0)
);
"""


def fail(message, exit_code):
    """向标准错误输出提示信息并返回退出码。"""
    print(message, file=sys.stderr)
    return exit_code


def parse_args(argv):
    """解析命令行参数，失败时返回 None（调用方输出“参数错误”）。"""
    db_path = DEFAULT_DB
    i = 0
    # 子命令之前只允许 --db 选项
    while i < len(argv) and argv[i].startswith("--"):
        option = argv[i]
        if option == "--db":
            if i + 1 >= len(argv):
                return None
            db_path = argv[i + 1]
            i += 2
        elif option.startswith("--db="):
            db_path = option[len("--db="):]
            i += 1
        else:
            return None

    if i >= len(argv):
        return None

    command = argv[i]
    rest = argv[i + 1:]
    if command == "add":
        if len(rest) != 2:
            return None
        return db_path, "add", rest[0], rest[1]
    if command == "show":
        if rest:
            return None
        return db_path, "show", None, None
    return None


def is_positive_int_text(text):
    """仅接受由数字 0 至 9 组成且数值大于零的字符串，允许前导零。"""
    return bool(text) and all("0" <= ch <= "9" for ch in text) and int(text) > 0


def open_shop(db_path):
    """打开（必要时创建）数据库，初始化表并写入固定商品目录。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT OR IGNORE INTO product(code, name, price_cents) "
            "VALUES (?, ?, ?)",
            PRODUCTS,
        )
        conn.commit()
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def cmd_add(conn, code, quantity):
    row = conn.execute(
        "SELECT 1 FROM product WHERE code = ?", (code,)
    ).fetchone()
    if row is None:
        return fail("未知商品", 2)

    with conn:
        conn.execute(
            "INSERT INTO cart_item(code, quantity) VALUES (?, ?) "
            "ON CONFLICT(code) DO UPDATE SET "
            "quantity = quantity + excluded.quantity",
            (code, quantity),
        )
    total = conn.execute(
        "SELECT quantity FROM cart_item WHERE code = ?", (code,)
    ).fetchone()[0]
    print(f"{code} {total}")
    return 0


def cmd_show(conn):
    rows = conn.execute(
        "SELECT p.code, p.name, p.price_cents, c.quantity, "
        "p.price_cents * c.quantity "
        "FROM cart_item AS c JOIN product AS p ON p.code = c.code "
        "ORDER BY p.code ASC"
    ).fetchall()

    total_quantity = 0
    total_amount = 0
    for code, name, price, quantity, subtotal in rows:
        print(f"{code} {name} {price} {quantity} {subtotal}")
        total_quantity += quantity
        total_amount += subtotal
    print(f"总数量 {total_quantity}")
    print(f"总金额 {total_amount}")
    return 0


def main(argv):
    parsed = parse_args(argv)
    if parsed is None:
        return fail("参数错误", 2)
    db_path, command, code, quantity_text = parsed

    if command == "add" and not is_positive_int_text(quantity_text):
        # 数量错误优先于未知商品，且在打开数据库之前判定，失败不影响购物车
        return fail("数量必须为正整数", 2)

    try:
        conn = open_shop(db_path)
    except sqlite3.Error:
        return fail("数据库不可用", 1)

    try:
        if command == "add":
            try:
                return cmd_add(conn, code, int(quantity_text))
            except sqlite3.Error:
                return fail("数据库不可用", 1)
        try:
            return cmd_show(conn)
        except sqlite3.Error:
            return fail("数据库不可用", 1)
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
