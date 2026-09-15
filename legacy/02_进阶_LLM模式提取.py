"""
02 - 进阶：LLM 模式要素提取 + 自定义 Schema
==================================================
使用大语言模型驱动的高质量语义提取，支持自定义实体/关系 Schema。

适用场景：
- 开放域复杂文本
- 需要理解语义和上下文
- 自定义领域实体和关系类型
- 高质量提取要求

运行: python 02_进阶_LLM模式提取.py

环境变量配置（任选其一）：
    export OPENAI_API_KEY=sk-xxx
    export OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
    export OPENAI_MODEL=doubao-seed-1-6-250615
"""

import os
import sys
import json
from pathlib import Path


# ============================================================
# 测试文本
# ============================================================
SAMPLE_TEXT = """
2024年3月15日，字节跳动在北京发布了新一代大语言模型豆包4.0。
该模型由字节跳动人工智能实验室研发，团队负责人是李明博士。
豆包4.0在多项中文基准测试中超越了GPT-4，特别是在数学推理和代码生成方面。
据了解，字节跳动计划在2024年下半年将豆包4.0集成到旗下所有产品中，包括抖音、今日头条和飞书。
此外，阿里巴巴的通义千问也于近期发布了3.0版本，由阿里云智能集团开发。
百度的文心一言4.0也在2024年初上线，由王海峰博士带队研发。
国内大模型市场呈现出字节跳动、阿里巴巴、百度三足鼎立的格局。
"""


def print_header(title, char="═"):
    print(f"\n{char * 70}")
    print(f"  {title}")
    print(f"{char * 70}")


def section(title):
    print(f"\n┌─ {title} " + "─" * (62 - len(title)))


# ============================================================
# 检测可用的 LLM Provider
# ============================================================
def detect_llm_provider():
    """检测环境中可用的 LLM provider"""
    providers = []
    
    # 检查 OpenAI 兼容
    if os.environ.get("OPENAI_API_KEY"):
        providers.append({
            "name": "openai",
            "config": {
                "api_key": os.environ["OPENAI_API_KEY"],
                "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                "model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            }
        })
    
    # 检查 Anthropic
    if os.environ.get("ANTHROPIC_API_KEY"):
        providers.append({
            "name": "anthropic",
            "config": {
                "api_key": os.environ["ANTHROPIC_API_KEY"],
                "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            }
        })
    
    return providers


# ============================================================
# Demo 1: 自定义 Schema 提取
# ============================================================
def demo_custom_schema_ner(text, llm_provider=None):
    section("自定义 Schema 的实体提取")
    
    from semantica.semantic_extract import NERExtractor
    
    if llm_provider:
        print("│ 使用 LLM 模式 + 自定义实体类型")
        
        # 自定义实体类型 Schema
        custom_types = [
            {"type": "COMPANY", "description": "公司或企业实体，如字节跳动、阿里巴巴"},
            {"type": "PERSON", "description": "人物实体，如李明博士、王海峰博士"},
            {"type": "MODEL", "description": "AI 大模型产品，如豆包4.0、GPT-4、文心一言"},
            {"type": "PRODUCT", "description": "互联网产品，如抖音、今日头条、飞书"},
            {"type": "DATE", "description": "日期，如2024年3月15日、2024年初"},
            {"type": "LOCATION", "description": "地点，如北京、杭州"},
            {"type": "BENCHMARK", "description": "基准测试名称"},
        ]
        
        try:
            ner = NERExtractor(
                method="llm",
                llm_provider=llm_provider,
                entity_types=custom_types,
                confidence_threshold=0.7,
            )
            entities = ner.extract(text)
        except Exception as e:
            print(f"│ LLM NER 失败: {e}")
            print("│ 回退到 pattern 模式...")
            ner = NERExtractor(method="pattern")
            entities = ner.extract(text)
    else:
        print("│ 未配置 LLM，使用 pattern 模式（提取质量有限）")
        print("│ 配置方式: 设置 OPENAI_API_KEY 环境变量")
        ner = NERExtractor(method="pattern")
        entities = ner.extract(text)
    
    print(f"│")
    print(f"│ 提取到 {len(entities)} 个实体:")
    print(f"│")
    print(f"│ {'#':>3}  {'实体':<22} {'类型':<14} {'置信度':>6}")
    print(f"│ {'─'*3}  {'─'*22} {'─'*14} {'─'*6}")
    
    for i, ent in enumerate(entities, 1):
        name = ent.text[:20] + (".." if len(ent.text) > 20 else "")
        print(f"│ {i:>3}  {name:<22} {ent.label:<14} {ent.confidence:>6.2f}")
    
    # 类型分布
    type_counts = {}
    for ent in entities:
        type_counts[ent.label] = type_counts.get(ent.label, 0) + 1
    print(f"│")
    print(f"│ 类型分布: {type_counts}")
    
    return entities


# ============================================================
# Demo 2: LLM 关系抽取
# ============================================================
def demo_llm_relations(text, entities, llm_provider=None):
    section("LLM 驱动的关系抽取")
    
    from semantica.semantic_extract import RelationExtractor
    
    if llm_provider:
        print("│ 使用 LLM 模式提取关系")
        
        # 自定义关系类型
        custom_relations = [
            {"predicate": "founded_by", "description": "公司由某人创立"},
            {"predicate": "developed_by", "description": "产品/模型由某公司或团队开发"},
            {"predicate": "released_on", "description": "产品发布日期"},
            {"predicate": "released_in", "description": "发布地点"},
            {"predicate": "competes_with", "description": "与...竞争"},
            {"predicate": "surpasses", "description": "在某方面超越"},
            {"predicate": "integrated_into", "description": "集成到某产品中"},
            {"predicate": "leads_team", "description": "带领某团队"},
        ]
        
        try:
            rel_extractor = RelationExtractor(
                method="llm",
                llm_provider=llm_provider,
                relation_types=custom_relations,
                confidence_threshold=0.6,
                bidirectional=True,
            )
            relations = rel_extractor.extract(text, entities=entities)
        except Exception as e:
            print(f"│ LLM 关系抽取失败: {e}")
            print("│ 回退到 pattern 模式...")
            rel_extractor = RelationExtractor(method="pattern")
            relations = rel_extractor.extract(text, entities=entities)
    else:
        print("│ 未配置 LLM，使用 pattern 模式")
        rel_extractor = RelationExtractor(method="pattern")
        relations = rel_extractor.extract(text, entities=entities)
    
    print(f"│")
    print(f"│ 提取到 {len(relations)} 条关系:")
    print(f"│")
    print(f"│ {'#':>3}  {'主语':<18} {'谓词':<20} {'宾语':<18} {'置信度':>6}")
    print(f"│ {'─'*3}  {'─'*18} {'─'*20} {'─'*18} {'─'*6}")
    
    for i, rel in enumerate(relations, 1):
        subj = (rel.subject.text if hasattr(rel.subject, 'text') else str(rel.subject))[:16]
        pred = rel.predicate[:18]
        obj = (rel.object.text if hasattr(rel.object, 'text') else str(rel.object))[:16]
        conf = rel.confidence
        print(f"│ {i:>3}  {subj:<18} {pred:<20} {obj:<18} {conf:>6.2f}")
    
    return relations


# ============================================================
# Demo 3: 自定义要素提取（从长文本提取特定字段）
# ============================================================
def demo_custom_field_extraction(text, llm_provider=None):
    section("自定义字段要素提取 (从长文本提取结构化信息)")
    
    print("│")
    print("│ 场景：从科技新闻中提取以下结构化字段:")
    print("│   - 公司名称")
    print("│   - 产品/模型名称")
    print("│   - 发布时间")
    print("│   - 关键人物")
    print("│   - 核心技术亮点")
    print("│   - 市场格局")
    print("│")
    
    if not llm_provider:
        print("│ ⚠️  需要 LLM 才能完成自定义字段提取")
        print("│    配置 OPENAI_API_KEY 后可启用")
        return None
    
    # 使用 LLM 直接提取结构化 JSON
    try:
        extraction_prompt = f"""
从以下文本中提取结构化信息，返回 JSON 格式：

文本：
\"\"\"
{text}
\"\"\"

提取字段：
1. companies: 提到的所有公司列表，每项包含 name, industry
2. products: 提到的所有AI模型和产品列表，每项包含 name, type, developer
3. key_people: 关键人物列表，每项包含 name, role, affiliation
4. events: 重要事件列表，每项包含 event_type, subject, date, description
5. market_landscape: 市场格局描述
6. key_claims: 文本中的核心观点或声明

只返回 JSON，不要其他说明文字。
"""
        
        # 尝试用 LLM 生成
        if hasattr(llm_provider, 'chat'):
            response = llm_provider.chat(extraction_prompt)
        else:
            # 手动调用 OpenAI 兼容 API
            import requests
            base_url = llm_provider.base_url if hasattr(llm_provider, 'base_url') else os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
            model = llm_provider.model if hasattr(llm_provider, 'model') else os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            api_key = llm_provider.api_key if hasattr(llm_provider, 'api_key') else os.environ.get("OPENAI_API_KEY")
            
            resp = requests.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": extraction_prompt}],
                    "temperature": 0.1,
                },
                timeout=60,
            )
            resp.raise_for_status()
            response = resp.json()["choices"][0]["message"]["content"]
        
        # 尝试解析 JSON
        import re
        json_match = re.search(r'```json\s*(.*?)\s*```', response, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group(1))
        else:
            result = json.loads(response)
        
        print("│ ✅ 提取成功!")
        print("│")
        
        # 打印结果
        if "companies" in result:
            print(f"│ 🏢 公司 ({len(result['companies'])} 家):")
            for c in result["companies"][:5]:
                name = c.get("name", c) if isinstance(c, dict) else c
                industry = c.get("industry", "") if isinstance(c, dict) else ""
                print(f"│    • {name} {industry}")
        
        if "products" in result:
            print(f"│")
            print(f"│ 🤖 产品/模型 ({len(result['products'])} 个):")
            for p in result["products"][:5]:
                name = p.get("name", p) if isinstance(p, dict) else p
                dev = p.get("developer", "") if isinstance(p, dict) else ""
                ptype = p.get("type", "") if isinstance(p, dict) else ""
                print(f"│    • {name} [{ptype}] - {dev}")
        
        if "key_people" in result:
            print(f"│")
            print(f"│ 👤 关键人物 ({len(result['key_people'])} 人):")
            for p in result["key_people"][:5]:
                name = p.get("name", p) if isinstance(p, dict) else p
                role = p.get("role", "") if isinstance(p, dict) else ""
                aff = p.get("affiliation", "") if isinstance(p, dict) else ""
                print(f"│    • {name} - {role} @ {aff}")
        
        if "events" in result:
            print(f"│")
            print(f"│ 📅 关键事件 ({len(result['events'])} 件):")
            for e in result["events"][:5]:
                etype = e.get("event_type", "") if isinstance(e, dict) else ""
                subj = e.get("subject", "") if isinstance(e, dict) else ""
                date = e.get("date", "") if isinstance(e, dict) else ""
                desc = e.get("description", "") if isinstance(e, dict) else str(e)
                print(f"│    • [{etype}] {subj} ({date}): {desc[:40]}")
        
        if "market_landscape" in result:
            print(f"│")
            print(f"│ 📊 市场格局:")
            landscape = result["market_landscape"]
            if isinstance(landscape, str):
                print(f"│    {landscape[:100]}")
            else:
                print(f"│    {str(landscape)[:100]}")
        
        # 保存完整结果
        output_path = Path(__file__).parent / "output_custom_fields.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"│")
        print(f"│ 💾 完整结果已保存到: {output_path}")
        
        return result
        
    except Exception as e:
        print(f"│ ❌ 自定义字段提取失败: {e}")
        import traceback
        traceback.print_exc()
        return None


# ============================================================
# Demo 4: 长文本分块提取
# ============================================================
def demo_long_text_extraction(text, llm_provider=None):
    section("长文本分块提取策略 (Entity-aware Chunking)")
    
    print("│")
    print("│ 长文本处理策略：")
    print("│ 1. 按语义边界分块（不是简单按 token 切）")
    print("│ 2. 每块独立提取实体和关系")
    print("│ 3. 跨块实体消歧与合并")
    print("│ 4. 全局关系整合")
    print("│")
    
    # 模拟分块
    chunks = []
    chunk_size = 300
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i+chunk_size]
        chunks.append({
            "id": f"chunk_{len(chunks)+1}",
            "text": chunk,
            "start": i,
            "end": min(i+chunk_size, len(text)),
        })
    
    print(f"│ 文本总长: {len(text)} 字符")
    print(f"│ 分块数量: {len(chunks)} 块")
    print(f"│")
    for i, chunk in enumerate(chunks):
        preview = chunk["text"][:50].replace("\n", " ")
        print(f"│   Chunk {i+1}: [{chunk['start']}-{chunk['end']}] {preview}...")
    
    # 逐块提取
    from semantica.semantic_extract import NERExtractor
    
    method = "llm" if llm_provider else "pattern"
    ner = NERExtractor(method=method, llm_provider=llm_provider if llm_provider else None)
    
    all_entities = []
    print(f"│")
    print(f"│ 逐块提取实体 (method={method}):")
    
    for chunk in chunks:
        try:
            chunk_entities = ner.extract(chunk["text"])
            for ent in chunk_entities:
                # 调整位置偏移
                ent.start_char += chunk["start"]
                ent.end_char += chunk["start"]
                ent.metadata["chunk_id"] = chunk["id"]
            all_entities.extend(chunk_entities)
            print(f"│   Chunk {chunk['id']}: 提取到 {len(chunk_entities)} 个实体")
        except Exception as e:
            print(f"│   Chunk {chunk['id']}: 提取失败 ({e})")
    
    print(f"│")
    print(f"│ 合计提取: {len(all_entities)} 个实体（含跨块重复）")
    
    # 简单去重
    seen = {}
    unique_entities = []
    for ent in all_entities:
        key = f"{ent.text.lower().strip()}|{ent.label}"
        if key not in seen:
            seen[key] = ent
            unique_entities.append(ent)
    
    print(f"│ 去重后: {len(unique_entities)} 个实体")
    
    return unique_entities, chunks


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("进阶篇：LLM 模式要素提取 + 自定义 Schema")
    print("""
本脚本演示如何使用 LLM 驱动高质量语义提取，以及如何自定义提取 Schema。

核心能力：
  • 自定义实体类型（指定你想提取什么）
  • 自定义关系类型（指定实体间有哪些关系）
  • 自定义字段提取（从长文本提取结构化 JSON）
  • 长文本分块提取策略
""")
    
    # 检测 LLM provider
    providers = detect_llm_provider()
    llm_provider = None
    
    if providers:
        print(f"✅ 检测到 LLM Provider: {providers[0]['name']}")
        print(f"   Model: {providers[0]['config'].get('model', 'default')}")
        
        # 尝试创建 LLM provider 对象
        try:
            if providers[0]["name"] == "openai":
                from semantica.llms import OpenAILLM
                llm_provider = OpenAILLM(
                    model=providers[0]["config"]["model"],
                    api_key=providers[0]["config"]["api_key"],
                    base_url=providers[0]["config"].get("base_url"),
                )
        except Exception as e:
            print(f"⚠️  创建 LLM provider 失败: {e}")
            print("   将回退到 pattern 模式")
    else:
        print("⚠️  未检测到 LLM API Key，将使用 pattern 模式（提取质量有限）")
        print("   配置方式:")
        print("     set OPENAI_API_KEY=sk-xxx")
        print("     set OPENAI_BASE_URL=https://ark.cn-beijing.volces.com/api/v3")
        print("     set OPENAI_MODEL=doubao-seed-1-6-250615")
    
    # Demo 1: 自定义 Schema NER
    entities = demo_custom_schema_ner(SAMPLE_TEXT, llm_provider)
    
    # Demo 2: LLM 关系抽取
    relations = demo_llm_relations(SAMPLE_TEXT, entities, llm_provider)
    
    # Demo 3: 自定义字段提取
    demo_custom_field_extraction(SAMPLE_TEXT, llm_provider)
    
    # Demo 4: 长文本分块提取
    demo_long_text_extraction(SAMPLE_TEXT, llm_provider)
    
    # 总结
    print_header("进阶篇总结", char="═")
    print("""
📌 LLM 模式提取的核心优势：
  ✅ 语义理解强：能理解上下文、歧义、隐含关系
  ✅ 自定义灵活：通过 Schema 定义你要提取的实体/关系类型
  ✅ 领域适配好：只要描述清楚，就能适配任何领域
  ✅ 质量更高：复杂文本提取准确率远高于规则方法

📌 LLM 模式的代价：
  ❌ 成本：按 token 计费，大批量提取费用可观
  ❌ 速度：比规则方法慢 10-100 倍
  ❌ 不确定性：同一输入可能输出不同结果
  ❌ 幻觉风险：可能生成不存在的实体和关系

💡 最佳实践：混合策略
  1. 先用 pattern/rule 快速提取高置信度实体
  2. 再用 LLM 提取复杂关系和事件
  3. 用规则/SHACL 做后处理校验，过滤幻觉
  4. 长文本先分块，再做跨块实体消歧
""")


if __name__ == "__main__":
    main()
