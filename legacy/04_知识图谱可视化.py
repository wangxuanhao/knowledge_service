"""
04 - 知识图谱可视化
=====================
生成可交互的 HTML 知识图谱可视化页面。

使用 vis-network 库（纯前端，无需安装额外 Python 包），
生成单文件 HTML，双击即可在浏览器中查看。

支持功能：
  • 拖拽节点
  • 缩放平移
  • 点击节点高亮关联
  • 节点按类型着色
  • 悬停显示详情
  • 物理仿真（节点自动布局）

运行: python 04_知识图谱可视化.py
"""

import json
import sys
from pathlib import Path


def print_header(title, char="═"):
    print(f"\n{char * 70}")
    print(f"  {title}")
    print(f"{char * 70}")


def section(title):
    print(f"\n┌─ {title} " + "─" * (62 - len(title)))


# ============================================================
# 颜色配置：不同实体类型用不同颜色
# ============================================================
TYPE_COLORS = {
    "PERSON":       {"background": "#FF6B6B", "border": "#EE5A5A", "font": "#ffffff"},
    "ORG":          {"background": "#4ECDC4", "border": "#3DBDB5", "font": "#ffffff"},
    "COMPANY":      {"background": "#45B7D1", "border": "#3498DB", "font": "#ffffff"},
    "ORGANIZATION": {"background": "#45B7D1", "border": "#3498DB", "font": "#ffffff"},
    "PRODUCT":      {"background": "#96CEB4", "border": "#7FC9A5", "font": "#ffffff"},
    "MODEL":        {"background": "#DDA0DD", "border": "#BA55D3", "font": "#ffffff"},
    "AIMODEL":      {"background": "#DDA0DD", "border": "#BA55D3", "font": "#ffffff"},
    "LOCATION":     {"background": "#FFEAA7", "border": "#F9CA24", "font": "#333333"},
    "CITY":         {"background": "#FFEAA7", "border": "#F9CA24", "font": "#333333"},
    "DATE":         {"background": "#DFE6E9", "border": "#B2BEC3", "font": "#2D3436"},
    "EVENT":        {"background": "#E17055", "border": "#D63031", "font": "#ffffff"},
    "MONEY":        {"background": "#00B894", "border": "#00A885", "font": "#ffffff"},
    "TECH":         {"background": "#A29BFE", "border": "#6C5CE7", "font": "#ffffff"},
    "CONCEPT":      {"background": "#74B9FF", "border": "#0984E3", "font": "#ffffff"},
}

DEFAULT_COLOR = {"background": "#636E72", "border": "#2D3436", "font": "#ffffff"}


def get_node_color(entity_type):
    """根据实体类型获取颜色"""
    return TYPE_COLORS.get(entity_type.upper(), DEFAULT_COLOR)


# ============================================================
# 构建可视化数据
# ============================================================
def build_graph_data(entities, relations):
    """
    将实体和关系转换为 vis-network 格式
    """
    nodes = []
    edges = []
    
    # 节点去重
    seen_nodes = {}
    for i, ent in enumerate(entities):
        key = ent.text.lower().strip()
        if key not in seen_nodes:
            color = get_node_color(ent.label)
            nodes.append({
                "id": len(nodes) + 1,
                "label": ent.text,
                "title": f"类型: {ent.label}<br>置信度: {ent.confidence:.2f}",
                "color": color,
                "size": 25 + (ent.confidence * 15),  # 置信度越高越大
                "shape": "dot",
                "font": {"color": color["font"], "size": 14, "face": "sans-serif"},
                "group": ent.label,
            })
            seen_nodes[key] = len(nodes)  # 记录索引
    
    # 边
    for rel in relations:
        subj_text = rel.subject.text if hasattr(rel.subject, 'text') else str(rel.subject)
        obj_text = rel.object.text if hasattr(rel.object, 'text') else str(rel.object)
        
        subj_key = subj_text.lower().strip()
        obj_key = obj_text.lower().strip()
        
        if subj_key in seen_nodes and obj_key in seen_nodes:
            from_id = seen_nodes[subj_key]
            to_id = seen_nodes[obj_key]
            
            edges.append({
                "from": from_id,
                "to": to_id,
                "label": rel.predicate,
                "title": f"关系: {rel.predicate}<br>置信度: {rel.confidence:.2f}",
                "arrows": "to",
                "color": {"color": "#95A5A6", "highlight": "#3498DB"},
                "font": {"size": 12, "color": "#7F8C8D", "align": "middle"},
                "width": 1 + rel.confidence * 3,
                "smooth": {"type": "curvedCW", "roundness": 0.2},
            })
    
    return nodes, edges


# ============================================================
# 生成 HTML 可视化页面
# ============================================================
def generate_html(nodes, edges, title="知识图谱可视化", output_path="graph_visualization.html"):
    """
    生成完整的单文件 HTML 可视化页面
    """
    nodes_json = json.dumps(nodes, ensure_ascii=False, indent=2)
    edges_json = json.dumps(edges, ensure_ascii=False, indent=2)
    
    # 收集所有类型用于图例
    groups = set()
    for node in nodes:
        groups.add(node.get("group", "Unknown"))
    
    legend_items = ""
    for g in sorted(groups):
        color = get_node_color(g)
        legend_items += f'''
        <div class="legend-item">
            <span class="legend-dot" style="background-color: {color['background']}; border-color: {color['border']};"></span>
            <span>{g}</span>
        </div>'''
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: #1A1A2E;
            color: #fff;
            overflow: hidden;
        }}
        #container {{
            display: flex;
            height: 100vh;
        }}
        #sidebar {{
            width: 260px;
            background: #16213E;
            padding: 20px;
            border-right: 1px solid #0F3460;
            overflow-y: auto;
        }}
        #sidebar h1 {{
            font-size: 18px;
            margin-bottom: 8px;
            color: #E94560;
        }}
        #sidebar .subtitle {{
            font-size: 12px;
            color: #7F8C8D;
            margin-bottom: 20px;
        }}
        .stat-card {{
            background: #0F3460;
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 12px;
        }}
        .stat-card .stat-value {{
            font-size: 24px;
            font-weight: bold;
            color: #E94560;
        }}
        .stat-card .stat-label {{
            font-size: 11px;
            color: #7F8C8D;
            margin-top: 4px;
        }}
        #legend {{
            margin-top: 20px;
        }}
        #legend h3 {{
            font-size: 14px;
            margin-bottom: 10px;
            color: #BDC3C7;
        }}
        .legend-item {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 8px;
            font-size: 13px;
        }}
        .legend-dot {{
            width: 14px;
            height: 14px;
            border-radius: 50%;
            border: 2px solid;
            flex-shrink: 0;
        }}
        .controls {{
            margin-top: 20px;
        }}
        .controls h3 {{
            font-size: 14px;
            margin-bottom: 10px;
            color: #BDC3C7;
        }}
        .btn {{
            display: block;
            width: 100%;
            padding: 8px 12px;
            margin-bottom: 8px;
            border: 1px solid #0F3460;
            border-radius: 6px;
            background: #0F3460;
            color: #fff;
            font-size: 13px;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .btn:hover {{
            background: #E94560;
            border-color: #E94560;
        }}
        #graph {{
            flex: 1;
            position: relative;
        }}
        #graph-container {{
            width: 100%;
            height: 100%;
        }}
        #node-info {{
            position: absolute;
            top: 20px;
            right: 20px;
            background: rgba(22, 33, 62, 0.95);
            border: 1px solid #0F3460;
            border-radius: 10px;
            padding: 16px;
            min-width: 250px;
            display: none;
            backdrop-filter: blur(10px);
            box-shadow: 0 4px 20px rgba(0,0,0,0.3);
        }}
        #node-info h3 {{
            color: #E94560;
            margin-bottom: 8px;
        }}
        #node-info .info-row {{
            font-size: 13px;
            margin-bottom: 6px;
            color: #BDC3C7;
        }}
        #node-info .info-row span.key {{
            color: #7F8C8D;
            display: inline-block;
            width: 70px;
        }}
        #search-box {{
            width: 100%;
            padding: 8px 12px;
            border: 1px solid #0F3460;
            border-radius: 6px;
            background: #0F3460;
            color: #fff;
            font-size: 13px;
            margin-bottom: 10px;
        }}
        #search-box:focus {{
            outline: none;
            border-color: #E94560;
        }}
    </style>
</head>
<body>
    <div id="container">
        <div id="sidebar">
            <h1>🕸️ 知识图谱</h1>
            <div class="subtitle">{title}</div>
            
            <div class="stat-card">
                <div class="stat-value">{len(nodes)}</div>
                <div class="stat-label">实体节点</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{len(edges)}</div>
                <div class="stat-label">关系边</div>
            </div>
            
            <input type="text" id="search-box" placeholder="🔍 搜索节点...">
            
            <div class="controls">
                <h3>操作</h3>
                <button class="btn" onclick="network.fit()">🎯 适应视图</button>
                <button class="btn" onclick="togglePhysics()">⚙️ 切换物理仿真</button>
                <button class="btn" onclick="exportJSON()">💾 导出 JSON</button>
            </div>
            
            <div id="legend">
                <h3>图例</h3>
                {legend_items}
            </div>
        </div>
        
        <div id="graph">
            <div id="graph-container"></div>
            <div id="node-info">
                <h3 id="info-title">节点信息</h3>
                <div class="info-row"><span class="key">名称:</span><span id="info-name">-</span></div>
                <div class="info-row"><span class="key">类型:</span><span id="info-type">-</span></div>
                <div class="info-row"><span class="key">置信度:</span><span id="info-conf">-</span></div>
                <div class="info-row"><span class="key">度数:</span><span id="info-degree">-</span></div>
            </div>
        </div>
    </div>

    <script>
        // 数据
        var nodes = new vis.DataSet({nodes_json});
        var edges = new vis.DataSet({edges_json});
        
        // 容器
        var container = document.getElementById('graph-container');
        
        // 数据
        var data = {{
            nodes: nodes,
            edges: edges
        }};
        
        // 配置
        var options = {{
            nodes: {{
                borderWidth: 2,
                shadow: {{
                    enabled: true,
                    color: 'rgba(0,0,0,0.3)',
                    size: 10,
                    x: 3,
                    y: 3
                }}
            }},
            edges: {{
                arrows: {{
                    to: {{ enabled: true, scaleFactor: 0.6 }}
                }},
                smooth: {{
                    type: 'curvedCW',
                    roundness: 0.2
                }},
                shadow: {{
                    enabled: true,
                    color: 'rgba(0,0,0,0.2)',
                    size: 5,
                    x: 2,
                    y: 2
                }}
            }},
            physics: {{
                enabled: true,
                barnesHut: {{
                    gravitationalConstant: -3000,
                    centralGravity: 0.3,
                    springLength: 150,
                    springConstant: 0.04,
                    damping: 0.09,
                    avoidOverlap: 0.5
                }},
                stabilization: {{
                    enabled: true,
                    iterations: 200,
                    updateInterval: 25
                }}
            }},
            interaction: {{
                hover: true,
                tooltipDelay: 100,
                hideEdgesOnDrag: false,
                hideNodesOnDrag: false,
                navigationButtons: true,
                keyboard: true
            }},
            layout: {{
                improvedLayout: true
            }}
        }};
        
        // 创建网络
        var network = new vis.Network(container, data, options);
        
        // 物理仿真开关
        var physicsEnabled = true;
        function togglePhysics() {{
            physicsEnabled = !physicsEnabled;
            network.setOptions({{ physics: {{ enabled: physicsEnabled }} }});
        }}
        
        // 点击节点显示详情
        network.on('click', function(params) {{
            var nodeInfo = document.getElementById('node-info');
            if (params.nodes.length > 0) {{
                var nodeId = params.nodes[0];
                var node = nodes.get(nodeId);
                var degree = 0;
                edges.forEach(function(edge) {{
                    if (edge.from === nodeId || edge.to === nodeId) degree++;
                }});
                
                document.getElementById('info-title').textContent = '🕸️ 节点详情';
                document.getElementById('info-name').textContent = node.label;
                document.getElementById('info-type').textContent = node.group;
                document.getElementById('info-conf').textContent = (node.size ? node.size.toFixed(1) : '-');
                document.getElementById('info-degree').textContent = degree;
                nodeInfo.style.display = 'block';
            }} else {{
                nodeInfo.style.display = 'none';
            }}
        }});
        
        // 搜索功能
        var searchBox = document.getElementById('search-box');
        searchBox.addEventListener('input', function() {{
            var query = this.value.toLowerCase();
            var matched = [];
            
            nodes.forEach(function(node) {{
                if (node.label.toLowerCase().includes(query)) {{
                    matched.push(node.id);
                }}
            }});
            
            if (matched.length > 0 && query.length > 0) {{
                network.selectNodes(matched);
                network.focus(matched[0], {{ scale: 1.5, animation: true }});
            }} else {{
                network.selectNodes([]);
            }}
        }});
        
        // 导出 JSON
        function exportJSON() {{
            var data = {{
                nodes: nodes.get(),
                edges: edges.get()
            }};
            var blob = new Blob([JSON.stringify(data, null, 2)], {{ type: 'application/json' }});
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'knowledge_graph.json';
            a.click();
        }}
        
        // 稳定化完成后适应视图
        network.on('stabilizationIterationsDone', function() {{
            network.fit({{ animation: {{ duration: 500, easingFunction: 'easeInOutQuad' }} }});
        }});
    </script>
</body>
</html>'''
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    return output_path


# ============================================================
# Demo 1: 用本地模式提取的数据生成可视化
# ============================================================
def demo_basic_visualization():
    section("Demo 1: 基础知识图谱可视化（本地模式数据）")
    
    from semantica.semantic_extract import NERExtractor, RelationExtractor
    
    # 示例文本
    text = """
Apple Inc. was founded by Steve Jobs and Steve Wozniak in 1976 in Cupertino, California.
Tim Cook is the CEO of Apple.
Apple develops the iPhone and the Mac computer.
Google, based in Mountain View, develops Android and Gmail.
Microsoft, led by Satya Nadella, invested in OpenAI.
OpenAI released GPT-4 in March 2023.
Amazon, founded by Jeff Bezos in Seattle, sells AWS cloud services.
Meta Platforms owns Facebook, Instagram, and WhatsApp.
"""
    
    # 提取
    ner = NERExtractor(method="pattern", confidence_threshold=0.5)
    entities = ner.extract(text)
    
    rel_extractor = RelationExtractor(method="pattern", confidence_threshold=0.5)
    relations = rel_extractor.extract(text, entities=entities)
    
    print(f"│ 提取到 {len(entities)} 个实体, {len(relations)} 条关系")
    
    # 构建可视化数据
    nodes, edges = build_graph_data(entities, relations)
    
    print(f"│ 可视化节点: {len(nodes)}, 边: {len(edges)}")
    
    # 生成 HTML
    output_path = Path(__file__).parent / "graph_basic.html"
    generate_html(nodes, edges, title="科技公司知识图谱", output_path=str(output_path))
    
    print(f"│")
    print(f"│ ✅ 可视化页面已生成:")
    print(f"│    {output_path}")
    print(f"│")
    print(f"│    双击在浏览器中打开，即可查看交互式图谱")
    
    return entities, relations, output_path


# ============================================================
# Demo 2: 自定义数据可视化
# ============================================================
def demo_custom_visualization():
    section("Demo 2: 自定义领域图谱可视化（中文示例）")
    
    # 手动构造数据，演示中文领域图谱
    from semantica.semantic_extract.types import Entity, Relation
    
    # 构造实体
    entities_data = [
        ("字节跳动", "COMPANY"),
        ("阿里巴巴", "COMPANY"),
        ("腾讯", "COMPANY"),
        ("百度", "COMPANY"),
        ("张一鸣", "PERSON"),
        ("马云", "PERSON"),
        ("马化腾", "PERSON"),
        ("李彦宏", "PERSON"),
        ("豆包4.0", "MODEL"),
        ("通义千问", "MODEL"),
        ("混元大模型", "MODEL"),
        ("文心一言", "MODEL"),
        ("抖音", "PRODUCT"),
        ("淘宝", "PRODUCT"),
        ("微信", "PRODUCT"),
        ("北京", "CITY"),
        ("杭州", "CITY"),
        ("深圳", "CITY"),
        ("人工智能", "TECH"),
        ("大语言模型", "TECH"),
    ]
    
    entities = []
    for name, etype in entities_data:
        entities.append(Entity(
            text=name,
            label=etype,
            confidence=0.95,
            start_char=0,
            end_char=len(name),
        ))
    
    # 构造关系
    relations_data = [
        ("字节跳动", "founded_by", "张一鸣"),
        ("阿里巴巴", "founded_by", "马云"),
        ("腾讯", "founded_by", "马化腾"),
        ("百度", "founded_by", "李彦宏"),
        ("字节跳动", "develops", "豆包4.0"),
        ("阿里巴巴", "develops", "通义千问"),
        ("腾讯", "develops", "混元大模型"),
        ("百度", "develops", "文心一言"),
        ("字节跳动", "develops", "抖音"),
        ("阿里巴巴", "develops", "淘宝"),
        ("腾讯", "develops", "微信"),
        ("字节跳动", "located_in", "北京"),
        ("阿里巴巴", "located_in", "杭州"),
        ("腾讯", "located_in", "深圳"),
        ("百度", "located_in", "北京"),
        ("豆包4.0", "is_a", "大语言模型"),
        ("通义千问", "is_a", "大语言模型"),
        ("文心一言", "is_a", "大语言模型"),
        ("大语言模型", "belongs_to", "人工智能"),
        ("字节跳动", "competes_with", "阿里巴巴"),
        ("字节跳动", "competes_with", "腾讯"),
        ("百度", "competes_with", "字节跳动"),
    ]
    
    relations = []
    for subj, pred, obj in relations_data:
        subj_ent = next(e for e in entities if e.text == subj)
        obj_ent = next(e for e in entities if e.text == obj)
        relations.append(Relation(
            subject=subj_ent,
            predicate=pred,
            object=obj_ent,
            confidence=0.9,
        ))
    
    print(f"│ 自定义图谱: {len(entities)} 个实体, {len(relations)} 条关系")
    
    # 构建可视化数据
    nodes, edges = build_graph_data(entities, relations)
    
    # 生成 HTML
    output_path = Path(__file__).parent / "graph_chinese_tech.html"
    generate_html(nodes, edges, title="中国科技公司知识图谱", output_path=str(output_path))
    
    print(f"│")
    print(f"│ ✅ 中文图谱可视化已生成:")
    print(f"│    {output_path}")
    
    return entities, relations, output_path


# ============================================================
# Demo 3: 多层级图谱可视化
# ============================================================
def demo_hierarchical_visualization():
    section("Demo 3: 层级结构图谱（树状布局）")
    
    from semantica.semantic_extract.types import Entity, Relation
    
    # 构造层级数据
    entities_data = [
        ("人工智能", "TECH"),
        ("机器学习", "TECH"),
        ("深度学习", "TECH"),
        ("大语言模型", "TECH"),
        ("计算机视觉", "TECH"),
        ("自然语言处理", "TECH"),
        ("强化学习", "TECH"),
        ("Transformer", "TECH"),
        ("CNN", "TECH"),
        ("RNN", "TECH"),
        ("GPT系列", "MODEL"),
        ("BERT", "MODEL"),
        ("豆包", "MODEL"),
        ("Claude", "MODEL"),
        ("监督学习", "TECH"),
        ("无监督学习", "TECH"),
    ]
    
    entities = []
    for name, etype in entities_data:
        entities.append(Entity(
            text=name, label=etype, confidence=0.95,
            start_char=0, end_char=len(name),
        ))
    
    # 层级关系
    relations_data = [
        ("人工智能", "includes", "机器学习"),
        ("机器学习", "includes", "深度学习"),
        ("深度学习", "includes", "大语言模型"),
        ("深度学习", "includes", "计算机视觉"),
        ("深度学习", "includes", "自然语言处理"),
        ("机器学习", "includes", "强化学习"),
        ("机器学习", "includes", "监督学习"),
        ("机器学习", "includes", "无监督学习"),
        ("深度学习", "based_on", "Transformer"),
        ("计算机视觉", "based_on", "CNN"),
        ("自然语言处理", "based_on", "RNN"),
        ("大语言模型", "includes", "GPT系列"),
        ("大语言模型", "includes", "BERT"),
        ("大语言模型", "includes", "豆包"),
        ("大语言模型", "includes", "Claude"),
    ]
    
    relations = []
    for subj, pred, obj in relations_data:
        subj_ent = next(e for e in entities if e.text == subj)
        obj_ent = next(e for e in entities if e.text == obj)
        relations.append(Relation(
            subject=subj_ent, predicate=pred, object=obj_ent, confidence=0.9,
        ))
    
    print(f"│ 层级图谱: {len(entities)} 个实体, {len(relations)} 条关系")
    
    # 生成 HTML（使用层次布局）
    nodes, edges = build_graph_data(entities, relations)
    
    # 为层级图使用不同的布局
    output_path = Path(__file__).parent / "graph_hierarchical.html"
    
    # 生成带层级配置的 HTML
    nodes_json = json.dumps(nodes, ensure_ascii=False, indent=2)
    edges_json = json.dumps(edges, ensure_ascii=False, indent=2)
    
    # 收集类型用于图例
    groups = set(node.get("group", "Unknown") for node in nodes)
    legend_items = ""
    for g in sorted(groups):
        color = get_node_color(g)
        legend_items += f'''
        <div class="legend-item">
            <span class="legend-dot" style="background-color: {color['background']}; border-color: {color['border']};"></span>
            <span>{g}</span>
        </div>'''
    
    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>AI 技术层级图谱</title>
    <script src="https://unpkg.com/vis-network/standalone/umd/vis-network.min.js"></script>
    <style>
        body {{ margin:0; padding:0; background:#1A1A2E; color:#fff; font-family: sans-serif; overflow:hidden; }}
        #container {{ display:flex; height:100vh; }}
        #sidebar {{ width:240px; background:#16213E; padding:20px; border-right:1px solid #0F3460; }}
        h1 {{ font-size:18px; color:#E94560; margin-bottom:10px; }}
        .subtitle {{ font-size:12px; color:#7F8C8D; margin-bottom:20px; }}
        .legend-item {{ display:flex; align-items:center; gap:10px; margin-bottom:8px; font-size:13px; }}
        .legend-dot {{ width:14px; height:14px; border-radius:50%; border:2px solid; }}
        #graph {{ flex:1; }}
    </style>
</head>
<body>
    <div id="container">
        <div id="sidebar">
            <h1>🌳 AI 技术图谱</h1>
            <div class="subtitle">层级结构（从上到下）</div>
            <div id="legend"><h3 style="font-size:14px;color:#BDC3C7;margin:20px 0 10px;">图例</h3>{legend_items}</div>
        </div>
        <div id="graph"></div>
    </div>
    <script>
        var nodes = new vis.DataSet({nodes_json});
        var edges = new vis.DataSet({edges_json});
        var container = document.getElementById('graph');
        var data = {{ nodes: nodes, edges: edges }};
        var options = {{
            nodes: {{ borderWidth: 2, shadow: true }},
            edges: {{ arrows: {{ to: {{ enabled: true }} }}, smooth: {{ type: 'cubicBezier' }} }},
            layout: {{
                hierarchical: {{
                    enabled: true,
                    direction: 'UD',
                    sortMethod: 'directed',
                    levelSeparation: 120,
                    nodeSpacing: 100,
                    treeSpacing: 150,
                    shakeTowards: 'roots'
                }}
            }},
            physics: {{ enabled: false }},
            interaction: {{ hover: true, navigationButtons: true }}
        }};
        new vis.Network(container, data, options);
    </script>
</body>
</html>'''
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    
    print(f"│")
    print(f"│ ✅ 层级图谱可视化已生成:")
    print(f"│    {output_path}")
    
    return entities, relations, output_path


# ============================================================
# 主函数
# ============================================================
def main():
    print_header("可视化篇：知识图谱交互式可视化")
    print("""
本脚本生成可交互的 HTML 知识图谱可视化页面。

使用 vis-network 前端库，生成单文件 HTML：
  • 拖拽节点 / 缩放平移 / 点击高亮
  • 节点按类型着色，图例清晰
  • 悬停显示详情，搜索定位节点
  • 物理仿真自动布局
  • 支持层级布局（树状结构）

生成的文件用浏览器直接打开即可查看，无需安装任何东西。
""")
    
    # Demo 1: 基础可视化（英文科技公司）
    demo_basic_visualization()
    
    # Demo 2: 自定义中文图谱
    demo_custom_visualization()
    
    # Demo 3: 层级结构
    demo_hierarchical_visualization()
    
    # 总结
    print_header("可视化篇总结", char="═")
    print(f"""
✅ 已生成 3 个可视化页面：

  1. graph_basic.html         - 基础知识图谱（力导向布局）
  2. graph_chinese_tech.html  - 中国科技公司图谱（力导向布局）
  3. graph_hierarchical.html  - AI 技术层级图谱（树状布局）

💡 查看方式：双击 HTML 文件，用浏览器打开

🎮 交互操作：
   • 滚轮缩放
   • 拖拽平移
   • 点击节点查看详情
   • 左侧搜索框快速定位
   • 节点可自由拖拽调整位置

📌 其他可视化选项：
   • 内置 Explorer: pip install "semantica[explorer]"; semantica-explorer
   • 导出 GraphML → Gephi 桌面版
   • 导出 DOT → Graphviz
   • 接入 Neo4j Browser
""")


if __name__ == "__main__":
    main()
