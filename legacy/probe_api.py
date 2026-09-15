"""
探针：验证 semantica 各模块真实 API
用于确认写 demo 前的真实接口，避免猜 API
"""
import sys
import traceback

def probe(name, fn):
    print(f"\n{'='*60}")
    print(f"  探针: {name}")
    print(f"{'='*60}")
    try:
        result = fn()
        if result is not None:
            print(f"  返回: {result}")
        print(f"  ✅ OK")
    except Exception as e:
        print(f"  ❌ 失败: {type(e).__name__}: {e}")

# ========== 1. normalize ==========
def p_normalize():
    from semantica.normalize import (
        normalize_text, clean_text, normalize_entity, normalize_date,
        normalize_time, normalize_number, normalize_quantity,
        detect_language, handle_encoding, list_available_methods,
    )
    print("  可用方法:", list_available_methods())
    print("  normalize_text:", repr(normalize_text("Hello   World  ", method="default")))
    print("  clean_text:", repr(clean_text("<p>Hello <b>World</b></p>", remove_html=True)))
    print("  normalize_entity:", repr(normalize_entity("Dr. John Doe", entity_type="Person")))
    print("  normalize_date:", repr(normalize_date("2024-03-15")))
    print("  normalize_number:", repr(normalize_number("1,234.56")))
    print("  normalize_quantity:", repr(normalize_quantity("5 km")))
    print("  detect_language:", repr(detect_language("Hello world this is a test")))

probe("normalize 模块", p_normalize)

# ========== 2. conflicts ==========
def p_conflicts():
    import inspect
    from semantica.conflicts import ConflictDetector, ConflictResolver
    cd = ConflictDetector()
    print("  ConflictDetector 方法:", [m for m in dir(cd) if not m.startswith('_')][:20])
    cr = ConflictResolver()
    print("  ConflictResolver 方法:", [m for m in dir(cr) if not m.startswith('_')][:20])

probe("conflicts 模块", p_conflicts)

# ========== 3. provenance ==========
def p_provenance():
    import semantica.provenance as p
    print("  provenance 导出:", [x for x in dir(p) if not x.startswith('_')][:30])

probe("provenance 模块", p_provenance)

# ========== 4. vector_store ==========
def p_vector_store():
    import semantica.vector_store as vs
    print("  vector_store 导出:", [x for x in dir(vs) if not x.startswith('_')][:30])

probe("vector_store 模块", p_vector_store)

# ========== 5. pipeline ==========
def p_pipeline():
    import semantica.pipeline as pl
    print("  pipeline 导出:", [x for x in dir(pl) if not x.startswith('_')][:30])

probe("pipeline 模块", p_pipeline)

# ========== 6. graph_store ==========
def p_graph_store():
    import semantica.graph_store as gs
    print("  graph_store 导出:", [x for x in dir(gs) if not x.startswith('_')][:30])

probe("graph_store 模块", p_graph_store)

# ========== 7. triplet_store ==========
def p_triplet_store():
    import semantica.triplet_store as ts
    print("  triplet_store 导出:", [x for x in dir(ts) if not x.startswith('_')][:30])

probe("triplet_store 模块", p_triplet_store)

# ========== 8. embeddings ==========
def p_embeddings():
    import semantica.embeddings as eb
    print("  embeddings 导出:", [x for x in dir(eb) if not x.startswith('_')][:30])

probe("embeddings 模块", p_embeddings)

# ========== 9. split ==========
def p_split():
    import semantica.split as sp
    print("  split 导出:", [x for x in dir(sp) if not x.startswith('_')][:30])

probe("split 模块", p_split)

print("\n\n探针完成")
