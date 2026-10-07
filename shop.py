#!/usr/bin/env python3
"""本地虚拟商店：固定商品目录 + 持久化购物车（SQLite）。

用法：
    python shop.py [--db 数据库文件] add 商品编号 数量
    python shop.py [--db 数据库文件] decrease 商品编号 数量
    python shop.py [--db 数据库文件] remove 商品编号
    python shop.py [--db 数据库文件] clear
    python shop.py [--db 数据库文件] show
    python shop.py [--db 数据库文件] catalog [关键词]

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

# 数量上界：SQLite INTEGER 的最大值，保证落库后仍是可精确保存的整数
MAX_QUANTITY = 9223372036854775807

ERR_ARGS = "参数错误"
ERR_QUANTITY = "数量必须为正整数"
ERR_RANGE = "数量超出范围"
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
    elif command == "clear":
        if args:
            fail(ERR_ARGS, 2)
    elif command == "show":
        if args:
            fail(ERR_ARGS, 2)
    elif command == "catalog":
        # 可选关键词：至多一个；为空字符串或全为空白时按参数错误处理。
        # 校验发生在打开数据库之前，拒绝调用不会触碰已有购物车。
        if len(args) > 1:
            fail(ERR_ARGS, 2)
        if args and not args[0].strip():
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


def parse_quantity(text, max_value=None):
    """数量只接受由 0-9 组成且数值大于零的字符串，允许前导零。

    指定 max_value 时，数值超过上界报“数量超出范围”。范围比较按
    去掉前导零后的十进制字符串进行：任意长度的数字串都有确定结果，
    不会因超长转换而出异常堆栈。
    """
    if not text or any(ch not in "0123456789" for ch in text):
        fail(ERR_QUANTITY, 2)
    digits = text.lstrip("0")
    if not digits:
        fail(ERR_QUANTITY, 2)
    if max_value is not None:
        limit = str(max_value)
        if len(digits) > len(limit) or (
            len(digits) == len(limit) and digits > limit
        ):
            fail(ERR_RANGE, 2)
    return int(digits)


def parse_decrease_quantity(text):
    """decrease 专用：只校验数量格式，返回去掉前导零后的十进制数字串。

    与 parse_quantity 不同，这里不把数字串转换成 int：decrease 的数量
    没有数值上界（即使超过 SQLite 可保存的整数上界，也仍按业务规则与
    购物车数量比较），而五千位等超长数字串在 Python 3.11 默认的数字
    转换位数限制下执行 int() 会抛 ValueError。格式规则与
    parse_quantity 完全一致：只接受由 0-9 组成且数值大于零的文本。
    """
    if not text or any(ch not in "0123456789" for ch in text):
        fail(ERR_QUANTITY, 2)
    digits = text.lstrip("0")
    if not digits:
        fail(ERR_QUANTITY, 2)
    return digits


def decimal_greater(digits, value):
    """判断十进制数字串 digits 表示的正整数是否大于非负整数 value。

    两边都按字符串比较，不经过 int 转换，因此数字串任意长度都有确定
    结果，不受解释器数字转换位数限制的影响。
    """
    limit = str(value)
    return len(digits) > len(limit) or (
        len(digits) == len(limit) and digits > limit
    )


def cmd_add(conn, product_id, quantity_text):
    # 数量校验（格式先于范围）优先于编号：即使编号未知也先报告数量错误
    quantity = parse_quantity(quantity_text, MAX_QUANTITY)
    try:
        row = conn.execute(
            "SELECT 1 FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if row is None:
            fail(ERR_UNKNOWN_PRODUCT, 2)
        row = conn.execute(
            "SELECT quantity FROM cart WHERE product_id = ?", (product_id,)
        ).fetchone()
        existing = row[0] if row is not None else 0
        # 累计越界在写入之前判定：失败后购物车与商品资料保持原样
        if existing + quantity > MAX_QUANTITY:
            fail(ERR_RANGE, 2)
        total = existing + quantity
        conn.execute(
            "INSERT INTO cart (product_id, quantity) VALUES (?, ?) "
            "ON CONFLICT(product_id) DO UPDATE SET quantity = quantity + ?",
            (product_id, quantity, quantity),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        fail(ERR_DB, 1)
    print(f"{product_id} 数量 {total}")


def cmd_decrease(conn, product_id, quantity_text):
    # 数量校验优先于编号：即使编号未知也先报告数量错误。
    # 这里得到的是去掉前导零的数字串而非 int：减少量没有数值上界，
    # 五千位等超长文本也要能与购物车数量作确定比较，不能因数字转换
    # 位数限制抛出未捕获异常。
    quantity_digits = parse_decrease_quantity(quantity_text)
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
        # 按十进制字符串比较，不转 int：减少量即使超过 SQLite 可保存的
        # 整数上界，也照样得到“超过购物车数量”的业务结果
        if decimal_greater(quantity_digits, current):
            fail(ERR_DECREASE_TOO_MUCH, 2)
        # 能减少说明 quantity <= current；current 是 SQLite 整数
        # （至多 19 位），此时转换不会触及解释器的数字转换位数限制
        remaining = current - int(quantity_digits)
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


def cmd_clear(conn):
    try:
        # 单条 DELETE 是一条原子语句：失败时整笔回滚，不会留下只删掉部分条目的购物车
        conn.execute("DELETE FROM cart")
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        fail(ERR_DB, 1)
    print("购物车已清空")


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


def cmd_catalog(conn, keyword=None):
    try:
        rows = conn.execute(
            "SELECT id, name, price FROM products ORDER BY id"
        ).fetchall()
    except sqlite3.Error:
        fail(ERR_DB, 1)
    for pid, name, price in rows:
        if keyword is not None:
            # 用整个原始参数（含首尾空格）做区分大小写的子串匹配：编号或
            # 名称任一字段包含该关键词即输出，每件商品只来自 products 表
            # 的一行，因此只会出现一次。不做大小写/全角转换，不按空格拆
            # 词，百分号、下划线等符号也一律按普通字符处理。
            if keyword not in pid and keyword not in name:
                continue
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
        elif command == "clear":
            cmd_clear(conn)
        elif command == "catalog":
            cmd_catalog(conn, args[0] if args else None)
        else:
            cmd_show(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
