"""
07 - 归一化 (Normalize) 模块
====================================
Semantica 的 normalize 模块是知识抽取管线中的"数据清洗层"，
位于 parse（解析）之后、split（分块）之前。

作用：把脏数据标准化，让下游的实体抽取/关系抽取更准确。

七大类归一化能力：
  1. 文本归一化   — Unicode/空白/大小写/特殊字符
  2. 实体规范化   — 别名解析/消歧/名字变体
  3. 日期时间归一化 — 格式/时区/相对日期（"昨天""3天前"）
  4. 数字数量归一化 — 千分位/百分比/单位换算/货币/科学计数法
  5. 数据清洗     — 去重/校验/缺失值
  6. 语言检测     — 50+ 语言
  7. 编码处理     — 编码检测/UTF-8转换/BOM移除

运行: python 07_归一化normalize.py
"""

from semantica.normalize import (
    normalize_text, clean_text, normalize_entity, resolve_aliases,
    disambiguate_entity, normalize_date, normalize_time, normalize_number,
    normalize_quantity, clean_data, detect_duplicates, detect_language,
    handle_encoding, list_available_methods,
)


def print_header(title, char="═"):
    print(f"\n{char * 70}")
    print(f"  {title}")
    print(f"{char * 70}")


def section(title):
    print(f"\n┌─ {title} " + "─" * (62 - len(title)))


def show(label, value):
    print(f"│  {label:<28} →  {repr(value)}")


# ============================================================
# 1. 文本归一化
# ============================================================
def demo_text_normalization():
    section("1. 文本归一化（Text Normalization）")

    print("│")
    print("│ 【Unicode 归一化】")
    show("原始(带组合字符)", "Cafe\u0301")  # é = e + 组合重音
    show("NFC 组合形式", normalize_text("Cafe\u0301", unicode_form="NFC"))
    show("NFD 分解形式", normalize_text("Cafe\u0301", unicode_form="NFD"))

    print("│")
    print("│ 【空白归一化】")
    show("原始(多空格+制表符)", "Hello\t   World\r\nTest")
    show("归一化后", normalize_text("Hello\t   World\r\nTest"))

    print("│")
    print("│ 【大小写归一化】")
    show("原始", "Apple Inc.")
    show("lower", normalize_text("Apple Inc.", case="lower"))
    show("upper", normalize_text("Apple Inc.", case="upper"))
    show("title", normalize_text("apple inc.", case="title"))

    print("│")
    print("│ 【智能引号/破折号归一化】")
    show("原始(智能引号)", "He said \u201cHello\u201d \u2014 end")
    show("归一化后", normalize_text("He said \u201cHello\u201d \u2014 end"))


# ============================================================
# 2. 文本清洗
# ============================================================
def demo_text_cleaning():
    section("2. 文本清洗（Text Cleaning）")

    print("│")
    show("原始(带HTML)", "<p>Hello <b>World</b></p>")
    show("去HTML后", clean_text("<p>Hello <b>World</b></p>", remove_html=True))

    dirty = "   Hello   World!!!   \n\n  Multiple    Spaces   "
    show("原始(脏文本)", dirty)
    show("清洗后", clean_text(dirty))


# ============================================================
# 3. 实体规范化
# ============================================================
def demo_entity_normalization():
    section("3. 实体规范化（Entity Normalization）")

    print("│")
    print("│ 【名字标准化】")
    show("带头衔", normalize_entity("Dr. John Smith", entity_type="Person"))
    show("多空格", normalize_entity("  John   Smith  ", entity_type="Person"))
    show("公司名", normalize_entity("Apple Inc.", entity_type="Organization"))

    print("│")
    print("│ 【别名解析】")
    show("别名 'IBM'", resolve_aliases("IBM", entity_type="Organization"))
    show("别名 'J. Doe'", resolve_aliases("J. Doe", entity_type="Person"))

    print("│")
    print("│ 【实体消歧】")
    result = disambiguate_entity("John Smith", entity_type="Person")
    if isinstance(result, dict):
        for k, v in result.items():
            print(f"│    {k}: {v}")
    else:
        show("消歧结果", result)


# ============================================================
# 4. 日期时间归一化
# ============================================================
def demo_date_normalization():
    section("4. 日期时间归一化（Date/Time Normalization）")

    print("│")
    print("│ 【标准日期格式】")
    show("ISO 日期", normalize_date("2024-03-15"))
    show("美国格式", normalize_date("03/15/2024"))
    show("中文格式", normalize_date("2024年3月15日"))

    print("│")
    print("│ 【相对日期（自然语言）】")
    show("yesterday", normalize_date("yesterday", method="relative"))
    show("3 days ago", normalize_date("3 days ago", method="relative"))

    print("│")
    print("│ 【时间】")
    show("标准时间", normalize_time("10:30:00"))
    show("带毫秒", normalize_time("10:30:45.123"))


# ============================================================
# 5. 数字数量归一化
# ============================================================
def demo_number_normalization():
    section("5. 数字/数量归一化（Number/Quantity Normalization）")

    print("│")
    print("│ 【数字格式】")
    show("千分位", normalize_number("1,234.56"))
    show("百分比", normalize_number("50%"))
    show("科学计数法", normalize_number("1.5e3"))
    show("空格分隔", normalize_number("1 234 567"))

    print("│")
    print("│ 【数量+单位】")
    show("公里", normalize_quantity("5 km"))
    show("千克", normalize_quantity("2.5 kg"))
    show("英里", normalize_quantity("10 miles"))

    print("│")
    print("│ 【货币】")
    show("美元", normalize_number("$1,000", method="currency"))
    show("欧元", normalize_number("€500", method="currency"))


# ============================================================
# 6. 语言检测
# ============================================================
def demo_language_detection():
    section("6. 语言检测（Language Detection）")

    print("│")
    show("英文", detect_language("Hello world, this is a test"))
    show("中文", detect_language("这是一个中文测试句子"))
    show("日语", detect_language("これは日本語のテストです"))
    show("西班牙语", detect_language("Hola mundo, esto es una prueba"))
    show("法语", detect_language("Bonjour le monde, ceci est un test"))


# ============================================================
# 7. 编码处理
# ============================================================
def demo_encoding_handling():
    section("7. 编码处理（Encoding Handling）")

    print("│")
    # 模拟 GBK 编码的字节
    gbk_bytes = "中文测试".encode("gbk")
    show("GBK编码的字节", gbk_bytes)
    result = handle_encoding(gbk_bytes)
    show("处理后", result)

    # UTF-8 BOM
    bom_text = "\ufeffHello World"
    show("带BOM文本", bom_text)
    show("去BOM后", handle_encoding(bom_text))


# ============================================================
# 8. 数据清洗（去重/缺失值）
# ============================================================
def demo_data_cleaning():
    section("8. 数据清洗（去重/缺失值/校验）")

    print("│")
    # 重复检测
    records = [
        {"id": 1, "name": "Alice", "age": 30},
        {"id": 2, "name": "Alice", "age": 30},
        {"id": 3, "name": "Bob", "age": 25},
        {"id": 4, "name": "Alice", "age": 31},
    ]
    print("│ 【重复检测】")
    print("│   输入 4 条记录（有重复）")
    try:
        dups = detect_duplicates(records, method="default")
        show("重复检测结果", dups)
    except Exception as e:
        print(f"│   detect_duplicates 需要特定格式: {e}")

    print("│")
    print("│ 【缺失值处理】")
    dirty_data = [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "age": None},
        {"name": "Charlie", "age": 25},
        {"name": None, "age": 40},
    ]
    try:
        cleaned = clean_data(dirty_data, method="missing")
        show("缺失值处理后", cleaned)
    except Exception as e:
        print(f"│   clean_data 调用方式: {e}")


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("归一化篇：Normalize 模块完整演示")
    print("""
归一化 (Normalize) 在 Semantica 完整管线中的位置：

   Sources → Parse → 【Normalize】 → Split → Extract → Conflicts
                                                    ↓
   ... → Deduplication → KG → Ontology/Reasoning/Provenance

为什么需要归一化？
  脏数据会直接导致下游实体抽取出错。例如：
  - "Apple  Inc."（多空格）和 "Apple Inc." 会被当成两个不同实体
  - "2024年3月15日" 和 "2024-03-15" 无法统一比较
  - "1,234" 和 "1234" 会被当成不同数字
  归一化在抽取前就把这些差异抹平，大幅提升下游准确率。
""")

    # 1-8 逐个演示
    demo_text_normalization()
    demo_text_cleaning()
    demo_entity_normalization()
    demo_date_normalization()
    demo_number_normalization()
    demo_language_detection()
    demo_encoding_handling()
    demo_data_cleaning()

    # 总结
    print_header("归一化篇总结", char="═")
    print("""
📌 七大类归一化能力速查：

  归一化类型      便利函数              典型用途
  ─────────────  ────────────────────  ──────────────────────────
  文本           normalize_text()      Unicode/空白/大小写统一
  文本清洗       clean_text()          去HTML/特殊字符
  实体           normalize_entity()    人名/公司名标准化
  别名           resolve_aliases()     "IBM"→"International Business Machines"
  消歧           disambiguate_entity() 同名实体区分
  日期           normalize_date()      各种日期格式统一为ISO
  数字           normalize_number()    千分位/百分比/科学计数法
  数量           normalize_quantity()  单位换算(km→kilometer)
  语言           detect_language()     自动识别文本语言
  编码           handle_encoding()     GBK→UTF-8、去BOM

📌 最佳实践：
  • 抽取前必做：normalize_text + clean_text
  • 实体入库前必做：normalize_entity + resolve_aliases
  • 时间字段必做：normalize_date（统一时区）
  • 数值字段必做：normalize_number + normalize_quantity
""")


if __name__ == "__main__":
    main()
