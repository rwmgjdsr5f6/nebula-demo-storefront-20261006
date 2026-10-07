#!/usr/bin/env python3
"""本地虚拟商店：固定商品目录 + 持久化购物车（SQLite）。

用法：
    python shop.py [--db 数据库文件] add 商品编号 数量
    python shop.py [--db 数据库文件] decrease 商品编号 数量
    python shop.py [--db 数据库文件] set 商品编号 目标数量
    python shop.py [--db 数据库文件] price 商品编号 单价
    python shop.py [--db 数据库文件] remove 商品编号
    python shop.py [--db 数据库文件] clear
    python shop.py [--db 数据库文件] show
    python shop.py [--db 数据库文件] preview
    python shop.py [--db 数据库文件] catalog [关键词]
    python shop.py [--db 数据库文件] catalog --sort price
    python shop.py [--db 数据库文件] catalog 关键词 --sort price

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

# 单价上界：同为 SQLite INTEGER 的最大值，单价允许为零
MAX_PRICE = 9223372036854775807

# preview 的固定满减：优惠前总金额达到该门槛（分）时减固定金额，只减一次
DISCOUNT_THRESHOLD = 5000
DISCOUNT_AMOUNT = 500

ERR_ARGS = "参数错误"
ERR_QUANTITY = "数量必须为正整数"
ERR_SET_QUANTITY = "数量必须为非负整数"
ERR_PRICE = "单价必须为非负整数"
ERR_RANGE = "数量超出范围"
ERR_PRICE_RANGE = "单价超出范围"
ERR_UNKNOWN_PRODUCT = "未知商品"
ERR_NOT_IN_CART = "商品不在购物车"
ERR_DECREASE_TOO_MUCH = "减少数量超过购物车数量"
ERR_DB = "数据库不可用"


# catalog 唯一接受的排序片段：由末尾相邻的两个独立参数 "--sort" 与
# "price" 组成，只接受这一种大小写写法
SORT_PRICE_ARGS = ("--sort", "price")


def fail(message, code):
    print(message, file=sys.stderr)
    sys.exit(code)


# 除 catalog 外各子命令允许的参数个数：元组中的每个元素都是一种合法
# 个数。catalog 的合法形式不按参数个数区分（空、单关键词、末尾的
# --sort price 排序片段），由 parse_catalog_args 单独校验，故不在此表。
COMMAND_ARITY = {
    "add": (2,),
    "decrease": (2,),
    "set": (2,),
    "price": (2,),
    "remove": (1,),
    "clear": (0,),
    "show": (0,),
    "preview": (0,),
}


def parse_args(argv):
    """解析 [--db 路径] 子命令 [参数...]，返回 (db_path, command, args)。

    合法性在打开数据库之前全部判定：catalog 交给 parse_catalog_args
    识别空、单关键词与末尾的 --sort price 排序片段四种形式；其余命令
    按 COMMAND_ARITY 的固定参数个数校验。返回值中的 args 始终是原样的
    原始参数列表，catalog 的关键词与排序标志由 main 再用
    parse_catalog_args 解析。
    """
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
    if command == "catalog":
        # catalog 的合法形式不按参数个数区分：[]、[关键词]、
        # [--sort, price]、[关键词, --sort, price] 四种，由专用校验在
        # 打开数据库前判定；其余命令仍按固定的参数个数表校验。
        parse_catalog_args(args)
        return db_path, command, args
    arity = COMMAND_ARITY.get(command)
    if arity is None or len(args) not in arity:
        fail(ERR_ARGS, 2)
    return db_path, command, args


def parse_catalog_args(args):
    """校验 catalog 的精确参数形式，返回 (关键词或 None, 是否按单价排序)。

    只接受四种形式：
        []                      全部商品，按编号升序
        [关键词]                 匹配商品，按编号升序
        [--sort, price]          全部商品，按单价升序、同价按编号升序
        [关键词, --sort, price]  匹配商品，排序同上

    排序片段由末尾两个相邻的独立参数组成，只接受这一种大小写；未使用
    该形式时单参数一律按原始关键词处理，因此 "catalog --sort" 仍把
    "--sort" 当作关键词。空串或纯空白关键词按参数错误拒绝。本函数在
    打开数据库之前运行：任何不合法形式都不会创建或打开数据库。
    """
    sort_by_price = False
    positional = list(args)
    if len(positional) >= 2 and tuple(positional[-2:]) == SORT_PRICE_ARGS:
        sort_by_price = True
        positional = positional[:-2]
    # 去掉末尾排序片段后至多剩一个关键词：两个普通参数即多个关键词；
    # 排序片段不在末尾（如 catalog --sort price 关键词）同样走到这里被拒
    if len(positional) > 1:
        fail(ERR_ARGS, 2)
    keyword = positional[0] if positional else None
    if keyword is not None and not keyword.strip():
        # 关键词是含非空白字符的完整原始参数：首尾空格也参与匹配；
        # 空串或纯空白参数按参数错误拒绝。
        fail(ERR_ARGS, 2)
    return keyword, sort_by_price


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


def parse_quantity_digits(text, format_error, allow_zero):
    """数量格式的共用校验：只接受由 0-9 组成的完整字符串，允许前导零。

    返回去掉前导零后的十进制数字串；全零输入在 allow_zero 为真时返回
    空串，否则按格式错误拒绝。不转换成 int：五千位等超长数字串在
    Python 3.11 默认的数字转换位数限制下执行 int() 会抛 ValueError，
    而调用方可能只需按字符串比较（如 decrease 的减少量没有数值上界）。
    """
    if not text or any(ch not in "0123456789" for ch in text):
        fail(format_error, 2)
    digits = text.lstrip("0")
    if not digits and not allow_zero:
        fail(format_error, 2)
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


def parse_quantity(text, max_value=None):
    """数量只接受由 0-9 组成且数值大于零的字符串，允许前导零。

    指定 max_value 时，数值超过上界报“数量超出范围”。范围比较按
    去掉前导零后的十进制字符串进行：任意长度的数字串都有确定结果，
    不会因超长转换而出异常堆栈。
    """
    digits = parse_quantity_digits(text, ERR_QUANTITY, allow_zero=False)
    if max_value is not None and decimal_greater(digits, max_value):
        fail(ERR_RANGE, 2)
    return int(digits)


def parse_decrease_quantity(text):
    """decrease 专用：只校验数量格式，返回去掉前导零后的十进制数字串。

    与 parse_quantity 不同，这里不把数字串转换成 int：decrease 的数量
    没有数值上界（即使超过 SQLite 可保存的整数上界，也仍按业务规则与
    购物车数量比较）。格式规则与 parse_quantity 完全一致：只接受由
    0-9 组成且数值大于零的文本。
    """
    return parse_quantity_digits(text, ERR_QUANTITY, allow_zero=False)


def parse_nonnegative_quantity(text):
    """set 专用：接受由 0-9 组成的非负整数，允许前导零（000 即零）。

    数值超过 MAX_QUANTITY 时报“数量超出范围”。格式校验先于范围校验，
    范围比较按去掉前导零后的十进制字符串进行：任意长度的数字串都有
    确定结果，不会因超长转换而出异常堆栈。
    """
    digits = parse_quantity_digits(text, ERR_SET_QUANTITY, allow_zero=True)
    if not digits:
        return 0
    if decimal_greater(digits, MAX_QUANTITY):
        fail(ERR_RANGE, 2)
    return int(digits)


def parse_price(text):
    """price 专用：接受由 0-9 组成的非负整数单价，允许前导零（000 即零）。

    数值超过 MAX_PRICE 时报“单价超出范围”。格式校验先于范围校验，
    范围比较按去掉前导零后的十进制字符串进行：任意长度的数字串都有
    确定结果，不会因超长转换而出异常堆栈。
    """
    digits = parse_quantity_digits(text, ERR_PRICE, allow_zero=True)
    if not digits:
        return 0
    if decimal_greater(digits, MAX_PRICE):
        fail(ERR_PRICE_RANGE, 2)
    return int(digits)


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


def cmd_set(conn, product_id, quantity_text):
    # 数量校验（格式先于范围）优先于编号：即使编号未知也先报告数量错误。
    # 目标数量是确定的最终件数：不累计、不按减少量解释；零表示移除记录。
    target = parse_nonnegative_quantity(quantity_text)
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
            # set 不负责首次加入：目标为零时同样要求商品已在购物车中
            fail(ERR_NOT_IN_CART, 2)
        if target == 0:
            conn.execute(
                "DELETE FROM cart WHERE product_id = ?", (product_id,)
            )
        else:
            conn.execute(
                "UPDATE cart SET quantity = ? WHERE product_id = ?",
                (target, product_id),
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        fail(ERR_DB, 1)
    print(f"{product_id} 数量 {target}")


def cmd_price(conn, product_id, price_text):
    # 单价格式校验先于范围校验，二者优先于编号：即使编号未知也先报告价格错误
    price = parse_price(price_text)
    try:
        row = conn.execute(
            "SELECT 1 FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if row is None:
            fail(ERR_UNKNOWN_PRODUCT, 2)
        # 只覆盖单价：名称、编号与购物车数量均不受影响，是否已加入购物车都可修改；
        # 设成现有单价时这条 UPDATE 也照常执行并按成功处理
        conn.execute(
            "UPDATE products SET price = ? WHERE id = ?",
            (price, product_id),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        fail(ERR_DB, 1)
    print(f"{product_id} 单价 {price}")


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


def read_cart_summary(conn):
    """读取购物车明细并汇总，返回 (商品行列表, 总数量, 总金额)。

    show 与 preview 共用的唯一读取流程：名称、单价、数量全部取自数据库
    当前内容（不使用内置目录价格），按编号升序生成商品行。price、qty
    都是 SQLite 整数（至多 19 位）；乘积与累加交给 Python 任意精度整数，
    即使小计或合计超过 SQLite INTEGER 上界也输出精确的十进制整数。
    查询失败时报“数据库不可用”并退出 1，不产出任何部分明细。
    """
    try:
        rows = conn.execute(
            "SELECT p.id, p.name, p.price, c.quantity "
            "FROM cart c JOIN products p ON p.id = c.product_id "
            "ORDER BY p.id"
        ).fetchall()
    except sqlite3.Error:
        fail(ERR_DB, 1)
    lines = []
    total_qty = 0
    total_amount = 0
    for pid, name, price, qty in rows:
        subtotal = price * qty
        total_qty += qty
        total_amount += subtotal
        lines.append(f"{pid} {name} {price} {qty} {subtotal}")
    return lines, total_qty, total_amount


def write_lines(lines):
    """全部行准备好后一次性输出：失败路径不会出现部分明细。"""
    sys.stdout.write("\n".join(lines) + "\n")


def cmd_show(conn):
    lines, total_qty, total_amount = read_cart_summary(conn)
    lines.append(f"总数量 {total_qty}")
    lines.append(f"总金额 {total_amount}")
    write_lines(lines)


def cmd_preview(conn):
    # 纯只读结算预览：商品明细与汇总和 show 共用同一读取流程；
    # 不创建订单、不清空购物车、不保存优惠状态。
    lines, total_qty, total_amount = read_cart_summary(conn)
    # 固定满减只按门槛判定一次：达到或超过门槛减固定金额，否则优惠为零，
    # 不存在多档叠加
    discount = DISCOUNT_AMOUNT if total_amount >= DISCOUNT_THRESHOLD else 0
    lines.append(f"总数量 {total_qty}")
    lines.append(f"总金额 {total_amount}")
    lines.append(f"优惠金额 {discount}")
    lines.append(f"应付金额 {total_amount - discount}")
    write_lines(lines)


def cmd_catalog(conn, keyword=None, sort_by_price=False):
    # 排序只影响本次展示，不落库、不改商品资料或购物车。默认按编号升序；
    # --sort price 时按单价的数据库整数值升序（零在正数之前），同价再按
    # 编号升序。price 列为 SQLite INTEGER，ORDER BY 按数值而非文本比较，
    # 9223372036854775807 等整数也精确参与排序。
    order_clause = "price, id" if sort_by_price else "id"
    try:
        rows = conn.execute(
            f"SELECT id, name, price FROM products ORDER BY {order_clause}"
        ).fetchall()
    except sqlite3.Error:
        fail(ERR_DB, 1)
    for pid, name, price in rows:
        # 关键词按区分大小写的原始文字做子串匹配：编号或名称任一字段
        # 包含整个关键词即输出，每件商品只出现一次。在 Python 侧过滤
        # 而不用 SQL LIKE：百分号、下划线等符号一律按普通字符处理，
        # 也不存在 ASCII 大小写折叠。关键词为 None 时输出全部商品。
        # 过滤在排序结果之上进行，展示顺序保持不变。
        if keyword is not None and keyword not in pid and keyword not in name:
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
        elif command == "set":
            cmd_set(conn, args[0], args[1])
        elif command == "price":
            cmd_price(conn, args[0], args[1])
        elif command == "remove":
            cmd_remove(conn, args[0])
        elif command == "clear":
            cmd_clear(conn)
        elif command == "preview":
            cmd_preview(conn)
        elif command == "catalog":
            # parse_args 已用同一函数校验过 catalog 形式；这里在打开
            # 数据库之后重新解析出关键词与排序标志，仅用于本次展示。
            keyword, sort_by_price = parse_catalog_args(args)
            cmd_catalog(conn, keyword, sort_by_price)
        else:
            cmd_show(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main(sys.argv[1:])
