"""统一路径源（唯一该改路径的地方）。

apps/* 与 kgcore/* 一律 import 本模块拿目录，避免各自硬编码相对路径。
"""
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent     # 仓库根（知识图谱/）
APPS = REPO / "apps"
CORE = REPO / "kgcore"
ONTOLOGY = REPO / "ontology"
DOCS = REPO / "docs"
DATA = REPO / "data"

MODEL_DIR = DATA / "model"                       # 本地 bge-m3
RULE_DEMO = DATA / "rule_demo"                    # 演示规则文档
RULE_TXT = DATA / "rule_txt"                      # 批量 txt 语料
RULE_OUT = DATA / "rule_demo_output"              # 15/17 直接运行的输出
PROJECTS = DATA / "projects"                      # Web 项目 / 导入产物（含 graph.json）
GOLD = DATA / "gold"                              # 评测 gold 建议放这
KG_WEB = DATA / "kg_web"                          # Web 静态资源 + 项目缓存

ONTOLOGY_FILE = ONTOLOGY / "meituan_ontology.ttl"  # ★正式本体
