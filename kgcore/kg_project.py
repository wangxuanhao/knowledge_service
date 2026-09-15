"""
kg_project - 知识图谱项目离线存储层
====================================
把一次解析产出的所有东西打包成一个 project 目录，之后可离线导入、直接看结果：

    projects/<name>/
        manifest.json       # 项目元信息
        graph.json          # 节点/边（含 label/predicate/confidence/source_file/passage）
        entities.json       # 实体列表（含向量检索用 meta）
        segments.json       # 原文段落（含段号、来源文件）
        embeddings/         # 离线 FAISS 库
            ent.index / ent.corpus.json
            seg.index / seg.corpus.json

不依赖 semantica/networkx 运行时重建：导入时读回 FAISS + JSON 即可直接检索/构图。
"""
from __future__ import annotations
import json
import shutil
from pathlib import Path


PROJECTS_DIR = Path(__file__).resolve().parent.parent / "data" / "projects"


def project_path(name: str) -> Path:
    return PROJECTS_DIR / name


def list_projects() -> list[dict]:
    out = []
    if not PROJECTS_DIR.exists():
        return out
    for d in sorted(PROJECTS_DIR.iterdir()):
        mf = d / "manifest.json"
        if d.is_dir() and mf.exists():
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:
                continue
            out.append(m)
    return out


def save_project(name: str, manifest: dict, nodes: list, edges: list,
                 entities: list, segments: list,
                 ent_index_bytes: bytes, seg_index_bytes: bytes) -> Path:
    """idempotent 写入一个 project 目录"""
    p = project_path(name)
    p.mkdir(parents=True, exist_ok=True)
    emb = p / "embeddings"
    emb.mkdir(exist_ok=True)

    (p / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (p / "graph.json").write_text(
        json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    (p / "entities.json").write_text(
        json.dumps(entities, ensure_ascii=False, indent=2), encoding="utf-8")
    (p / "segments.json").write_text(
        json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")

    (emb / "ent.index").write_bytes(bytes(ent_index_bytes))
    (emb / "ent.corpus.json").write_text(
        json.dumps(entities, ensure_ascii=False, indent=2), encoding="utf-8")
    if seg_index_bytes is not None:
        (emb / "seg.index").write_bytes(bytes(seg_index_bytes))
        (emb / "seg.corpus.json").write_text(
            json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_project(name: str) -> dict:
    """读取 project 目录，返回 dict:
        {"manifest", "nodes", "edges", "entities", "segments",
         "ent_index_path", "seg_index_path"}
    """
    p = project_path(name)
    if not (p / "manifest.json").exists():
        raise FileNotFoundError(f"project 不存在: {name}")

    manifest = json.loads((p / "manifest.json").read_text(encoding="utf-8"))
    graph = json.loads((p / "graph.json").read_text(encoding="utf-8"))
    entities = json.loads((p / "entities.json").read_text(encoding="utf-8"))
    segments = json.loads((p / "segments.json").read_text(encoding="utf-8"))

    emb = p / "embeddings"
    ent_index_path = emb / "ent.index"
    seg_index_path = emb / "seg.index"

    return {
        "manifest": manifest,
        "name": name,
        "path": str(p),
        "nodes": graph.get("nodes", []),
        "edges": graph.get("edges", []),
        "entities": entities,
        "segments": segments,
        "ent_index_path": str(ent_index_path) if ent_index_path.exists() else None,
        "seg_index_path": str(seg_index_path) if seg_index_path.exists() else None,
    }


def delete_project(name: str) -> None:
    p = project_path(name)
    shutil.rmtree(p, ignore_errors=True)