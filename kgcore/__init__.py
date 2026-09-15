"""kgcore —— 知识图谱"本体 + 评测"复用库。

包含：
  ontology_owl.py   本体引擎（读 meituan_ontology.ttl → RDF/SPARQL/白名单/校验/总结）
  kg_eval.py        评测尺子（gold vs pred → 覆盖率/精确率/召回/F1）
  kg_project.py     Web 项目存取（project dir 布局）
  paths.py          统一路径源
"""
