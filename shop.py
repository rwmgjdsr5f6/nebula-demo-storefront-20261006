#!/usr/bin/env python3
"""本地虚拟商店：固定商品目录 + 持久化购物车（SQLite）。

用法：
    python shop.py [--db 数据库文件] add 商品编号 数量
    python shop.py [--db 数据库文件] decrease 商品编号 数量
    python shop.py [--db 数据库文件] remove 商品编号
    python shop.py [--db 数据库文件] show
    python shop.py [--db 数据库文件] catalog

不指定 --db 时使用当前工作目录下的 shop.sqlite3。
"""

import sqlite3
import sys

DEFAULT_DB = "shop.sqlite3"

# 固定商品目录：(编号, 名称, 单价/分)
CATALOG = [
    ("P001", "虚拟笔记本", 1200),
    ("P002", "虚拟马克杯", 2500),
]

ERR_ARGS = "参数错误"
ERR_QUANTITY = "数量必须为正整数"
ERR_UNKNOWN_PRODUCT = "未知商品"
ERR_NOT_IN_CART = "商品不在购物车"
ERR_DECREASE_TOO_MUCH = "减少数量超过购物车数量"
ERR_DB = "数据库不可用"


def fail(message, code):
    print(message, file=sys.stderr)
    sys.exit(code)


def parse_args(argv):
    """解析 [--db 路径] 子命令 [参数...]，返回 (db_path, command, args)。"""
    db_path = DEFAULT_DB
    rest = list(argv)
    if rest and rest[0] == "--db":
        if len(rest) < 2:
            fail(ERR_ARGS, 2)
        db_path = rest[1]
        rest = rest[2:]
    elif rest and rest[0].startswith("--db="):
        db_path = rest[0][len("--db="):]
        rest = rest[1:]
        if not db_path:
            fail(ERR_ARGS, 2)
    if not rest:
        fail(ERR_ARGS, 2)
    command, args = rest[0], rest[1:]
    if command == "add":
        if len(args) != 2:
            fail(ERR_ARGS, 2)
    elif command == "decrease":
        if len(args) != 2:
            fail(ERR_ARGS, 2)
    elif command == "remove":
        if len(args) != 1:
            fail(ERR_ARGS, 2)
    elif command == "show":
        if args:
            fail(ERR_ARGS, 2)
    elif command == "catalog":
        if args:
            fail(ERR_ARGS, 2)
    else:
        fail(ERR_ARGS, 2)
    return db_path, command, args


def open_db(db_path):
    """打开（必要时创建）数据库并初始化商品目录。"""
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS products ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, price INTEGER NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS cart ("
            "product_id TEXT PRIMARY KEY REFERENCES products(id), "
            "quantity INTEGER NOT NULL)"
        )
        conn.executemany(
            "INSERT OR IGNORE INTO products (id, name, price) VALUES (?, ?, ?)",
            CATALOG,
        )
        conn.commit()
    except sqlite3.Error:
        fail(ERR_DB, 1)
    return conn


def parse_quantity(text):
    """数量只接受由 0-9 组成且数值大于零的字符串，允许前导零。"""
    if not text or any(ch not in "0123456789" for ch in text):
        fail(ERR_QUANTITY, 2)
    value = int(text)
    if value <= 0:
        fail(ERR_QUANTITY, 2)
    return value


def cmd_add(conn, product_id, quantity_text):
    # 数量校验优先于编号：即使编号未知也先报告数量错误
    quantity = parse_quantity(quantity_text)
    try:
        row = conn.execute(
            "SELECT 1 FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if row is None:
            fail(ERR_UNKNOWN_PRODUCT, 2)
        conn.execute(
            "INSERT INTO cart (product_id, quantity) VALUES (?, ?) "
            "ON CONFLICT(product_id) DO UPDATE SET quantity = quantity + ?",
            (product_id, quantity, quantity),
        )
        # 提交前读取累计数量：任一步骤失败都回滚，保留本次加入前的购物车
        total = conn.execute(
            "SELECT quantity FROM cart WHERE product_id = ?", (product_id,)
        ).fetchone()[0]
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        fail(ERR_DB, 1)
    print(f"{product_id} 数量 {total}")


def cmd_decrease(conn, product_id, quantity_text):
    # 数量校验优先于编号：即使编号未知也先报告数量错误
    quantity = parse_quantity(quantity_text)
    try:
        row = conn.execute(
            "SELECT 1 FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if row is None:
            fail(ERR_UNKNOWN_PRODUCT, 2)
        row = conn.execute(
            "SELECT quantity FROM cart WHERE product_id = ?", (product_id,)
        ).fetchone()
        if row is None:
            fail(ERR_NOT_IN_CART, 2)
        current = row[0]
        if quantity > current:
            fail(ERR_DECREASE_TOO_MUCH, 2)
        remaining = current - quantity
        if remaining == 0:
            conn.execute(
                "DELETE FROM cart WHERE product_id = ?", (product_id,)
            )
        else:
            conn.execute(
                "UPDATE cart SET quantity = ? WHERE product_id = ?",
                (remaining, product_id),
            )
        conn.commit()
    except sqlite3.Error:
        fail(ERR_DB, 1)
    print(f"{product_id} 数量 {remaining}")


def cmd_remove(conn, product_id):
    try:
        row = conn.execute(
            "SELECT 1 FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if row is None:
            fail(ERR_UNKNOWN_PRODUCT, 2)
        row = conn.execute(
            "SELECT 1 FROM cart WHERE product_id = ?", (product_id,)
        ).fetchone()
        if row is None:
            fail(ERR_NOT_IN_CART, 2)
        conn.execute("DELETE FROM cart WHERE product_id = ?", (product_id,))
        conn.commit()
    except sqlite3.Error:
        fail(ERR_DB, 1)
    print(f"{product_id} 已移除")


def cmd_show(conn):
    try:
        rows = conn.execute(
            "SELECT p.id, p.name, p.price, c.quantity "
            "FROM cart c JOIN products p ON p.id = c.product_id "
            "ORDER BY p.id"
        ).fetchall()
    except sqlite3.Error:
        fail(ERR_DB, 1)
    total_qty = 0
    total_amount = 0
    for pid, name, price, qty in rows:
        subtotal = price * qty
        total_qty += qty
        total_amount += subtotal
        print(f"{pid} {name} {price} {qty} {subtotal}")
    print(f"总数量 {total_qty}")
    print(f"总金额 {total_amount}")


def cmd_catalog(conn):
    try:
        rows = conn.execute(
            "SELECT id, name, price FROM products ORDER BY id"
        ).fetchall()
    except sqlite3.Error:
        fail(ERR_DB, 1)
    for pid, name, price in rows:
        print(f"{pid} {name} {price}")


def main(argv):
    db_path, command, args = parse_args(argv)
    conn = open_db(db_path)
    try:
        if command == "add":
            cmd_add(conn, args[0], args[1])
        elif command == "decrease":
            cmd_decrease(conn, args[0], args[1])
        elif command == "remove":
            cmd_remove(conn, args[0])
        elif command == "catalog":
            cmd_catalog(conn)
        else:
            cmd_show(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
