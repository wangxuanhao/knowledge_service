"""Build the self-contained v1.1.0 Chinese operation manual."""
from __future__ import annotations

import base64
import html
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "docs" / "assets" / "manual-v110"
OUTPUT = ROOT / "docs" / "知识图谱操作手册.html"


def marker(n: int, x: float, y: float, w: float, h: float, label: str) -> dict:
    return {"n": n, "x": x, "y": y, "w": w, "h": h, "label": label}


def shot(path: str, alt: str, *markers: dict) -> dict:
    return {"path": ASSET_DIR / path, "alt": alt, "markers": list(markers)}


SHOTS = {
    "S01a": shot("S01a-create-admin.png", "用户与权限页面的新建账号区域",
                 marker(1, 18, 25, 79, 18, "填写用户名、初始口令、角色和展示名"),
                 marker(2, 78, 33, 8, 7, "点击“创建账号”")),
    "S01b": shot("S01b-admin-created.png", "账号清单中出现新管理员",
                 marker(1, 18, 45, 79, 16, "在账号清单核对用户名、管理员角色和启用状态")),
    "S01c": shot("S01c-admin-home.png", "演示管理员登录后的首页",
                 marker(1, 1, 91, 14, 7, "核对左下角身份为管理员"),
                 marker(2, 1, 17, 14, 25, "管理员可见本体层和实例层入口")),
    "S02a": shot("S02a-create-project.png", "创建项目弹窗并选择加载默认本体",
                 marker(1, 31, 28, 38, 22, "填写项目名，选择“加载默认本体”"),
                 marker(2, 57, 51, 9, 7, "点击“创建”")),
    "S02b": shot("S02b-project-created.png", "项目管理页中的新项目卡片",
                 marker(1, 18, 29, 36, 34, "项目卡片出现且标记为当前项目"),
                 marker(2, 1, 10, 14, 7, "项目选择器已切换到新项目")),
    "S02c": shot("S02c-default-ontology-v1.png", "本体建模层显示默认本体 v1",
                 marker(1, 17, 13, 14, 73, "左栏是类层级，不是实例知识"),
                 marker(2, 31, 13, 45, 73, "中间画布展示类与关系"),
                 marker(3, 71, 90, 27, 8, "底栏显示版本动作")),
    "S03a": shot("S03a-classes-draft.png", "草案中已新增员工和部门类",
                 marker(1, 18, 7, 32, 7, "标题显示“草案 · 未发布”"),
                 marker(2, 34, 18, 38, 9, "画布出现“部门”和“员工”")),
    "S03b": shot("S03b-attributes-draft.png", "草案中属性数量增加",
                 marker(1, 23, 90, 5, 7, "属性计数增加到 12"),
                 marker(2, 78, 36, 20, 28, "右栏选择实体属性并设置 domain / range")),
    "S03c": shot("S03c-relation-draft.png", "草案中出现员工隶属于部门关系",
                 marker(1, 44, 18, 18, 9, "员工与部门之间出现“隶属于”连线"),
                 marker(2, 17, 90, 24, 7, "关系计数和属性计数均已更新")),
    "S03d": shot("S03d-validation-review.png", "草案校验和审核状态",
                 marker(1, 72, 90, 7, 7, "点击“校验并审核”"),
                 marker(2, 18, 92, 49, 5, "阅读门禁提示并逐条处理阻断项")),
    "S03e": shot("S03e-published-v2.png", "版本管理抽屉显示本体 v2",
                 marker(1, 68, 8, 31, 47, "版本管理中核对 v2、发布说明和发布时间"),
                 marker(2, 17, 83, 24, 8, "发布后画布仍可查看结构统计")),
    "S04a": shot("S04a-file-selected.png", "知识写入页面已选择固定演示文件",
                 marker(1, 18, 19, 79, 24, "选择 manual_employee_v2.txt"),
                 marker(2, 18, 45, 79, 18, "核对文件清单和大小")),
    "S04b": shot("S04b-ingest-options.png", "知识写入的切片与抽取选项",
                 marker(1, 18, 55, 79, 29, "核对标题、时间、切片和抽取模式"),
                 marker(2, 80, 86, 15, 8, "预览后再提交")),
    "S04d": shot("S04d-ingest-results.png", "仅文档模式的知识写入结果工作区",
                 marker(1, 58, 18, 39, 9, "核对任务完成状态与“仅文档·不抽取图谱”模式"),
                 marker(2, 58, 29, 39, 11, "读取“主动未执行图谱知识抽取”的结果说明"),
                 marker(3, 65, 42, 14, 6, "点击“去原文数据源”继续核对")),
    "S04c": shot("S04c-knowledge-written.png", "知识台账出现两实体一关系两属性",
                 marker(1, 18, 27, 55, 10, "统计卡显示 2 实体、1 关系、2 属性"),
                 marker(2, 18, 38, 42, 7, "用四个视角切换核对")),
    "S05a": shot("S05a-entity-mindmap.png", "以张三为根的实体脑图",
                 marker(1, 18, 16, 35, 10, "选择张三并生成脑图"),
                 marker(2, 18, 28, 79, 57, "脑图展示实体和关系，不展开属性分支")),
    "S05b": shot("S05b-entity-ledger.png", "知识台账实体视角",
                 marker(1, 18, 37, 5, 6, "选择“实体”"),
                 marker(2, 18, 61, 79, 27, "核对张三和产品部的类型与本体版本")),
    "S05c": shot("S05c-relation-ledger.png", "知识台账关系视角",
                 marker(1, 23, 37, 5, 6, "选择“关系”"),
                 marker(2, 18, 61, 79, 24, "核对张三 → 隶属于 → 产品部")),
    "S05d": shot("S05d-attribute-ledger.png", "知识台账属性视角",
                 marker(1, 28, 37, 5, 6, "选择“属性”"),
                 marker(2, 18, 61, 79, 27, "核对工号和入职日期各自的版本")),
    "S06a": shot("S06a-product-manager-draft.png", "草案新增产品经理并挂到员工下",
                 marker(1, 18, 7, 31, 7, "仍是未发布草案"),
                 marker(2, 43, 18, 28, 9, "产品经理作为员工的子类")),
    "S06b": shot("S06b-published-v3.png", "产品经理发布后本体画布",
                 marker(1, 17, 15, 18, 14, "左栏层级：员工 → 产品经理"),
                 marker(2, 34, 18, 38, 9, "画布显示员工与产品经理继承关系")),
    "S07a": shot("S07a-ontology-after-v3.png", "本体 v3 的结构结果",
                 marker(1, 17, 15, 18, 14, "结构层已包含产品经理"),
                 marker(2, 72, 90, 26, 8, "下一次发布按钮不代表当前知识已变")),
    "S07b": shot("S07b-knowledge-unchanged.png", "发布 v3 后实体台账仍显示张三为员工",
                 marker(1, 18, 61, 79, 25, "张三仍是“员工”，记录仍挂本体 v2"),
                 marker(2, 57, 37, 40, 6, "台账提示：每个实体挂在哪个本体上")),
    "S08a": shot("S08a-reclassify-entry.png", "本体建模层的受控重分类入口",
                 marker(1, 70, 80, 8, 7, "点击“适应画布”后确认结构"),
                 marker(2, 18, 90, 41, 8, "先看待迁移、冲突和阻断统计")),
    "S08b": shot("S08b-reclassify-preview.png", "受控重分类预演画面",
                 marker(1, 18, 80, 80, 10, "预演只说明本体归属变化，不推断业务身份")),
    "S08c": shot("S08c-reclassify-result.png", "受控重分类执行结果",
                 marker(1, 18, 80, 80, 10, "执行后核对迁移数、冲突数和阻断数")),
    "S08d": shot("S08d-zhangsan-still-employee.png", "重分类后张三仍保持员工类型",
                 marker(1, 18, 61, 79, 25, "员工在 v3 仍有效，因此张三不会自动变成产品经理")),
    "S09a": shot("S09a-edit-zhangsan-type.png", "单独编辑张三后类型变为产品经理",
                 marker(1, 18, 61, 79, 25, "张三类型已改为产品经理，记录版本为 v2"),
                 marker(2, 57, 37, 40, 6, "本体 v3 与记录修订是两个维度")),
    "S09b": shot("S09b-zhangsan-history.png", "张三的记录版本历史",
                 marker(1, 88, 68, 8, 6, "从“版本历史”查看 r1 / r2"),
                 marker(2, 18, 61, 79, 25, "当前版本与旧版本并存可查")),
    "S09c": shot("S09c-mindmap-after-edit.png", "修改张三类型后的实体脑图",
                 marker(1, 18, 16, 35, 10, "重新选择张三并刷新"),
                 marker(2, 18, 28, 79, 57, "脑图更新类型，但仍不显示属性分支")),
    "S10a": shot("S10a-attribute-revision.png", "工号属性修订后的属性台账",
                 marker(1, 28, 37, 5, 6, "进入属性视角"),
                 marker(2, 18, 61, 79, 26, "工号自己的修订号增加，实体修订不跟着增加")),
    "S10b": shot("S10b-relation-soft-delete.png", "隶属于关系软删除后的关系台账",
                 marker(1, 23, 37, 5, 6, "进入关系视角"),
                 marker(2, 18, 61, 79, 25, "软删除后关系退出当前视图但保留历史")),
    "S10c": shot("S10c-relation-restored.png", "恢复关系后的关系台账",
                 marker(1, 18, 61, 79, 25, "恢复生成新修订，关系重新出现在当前视图")),
}


def data_url(path: Path) -> str:
    payload = path.read_bytes()
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        mime = "image/png"
    elif payload.startswith(b"\xff\xd8\xff"):
        mime = "image/jpeg"
    else:
        raise ValueError(f"截图格式不支持：{path}")
    return f"data:{mime};base64," + base64.b64encode(payload).decode("ascii")


def screenshot(figure_id: str, path: Path, alt: str, markers: list[dict]) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"截图 {figure_id} 不存在：{path}")
    boxes = "".join(
        '<span class="shot-marker" data-n="{n}" data-x="{x}" data-y="{y}" '
        'data-w="{w}" data-h="{h}" style="--x:{x}%;--y:{y}%;--w:{w}%;--h:{h}%"></span>'.format(**item)
        for item in markers
    )
    notes = "".join(f'<li><b>{item["n"]}</b>{html.escape(item["label"])}</li>' for item in markers)
    return f'''<figure data-shot-id="{figure_id}">
      <div class="shot-head"><span>{figure_id}</span><strong>{html.escape(alt)}</strong></div>
      <div class="shot-stage"><img src="{data_url(path)}" alt="{html.escape(alt)}">{boxes}</div>
      <figcaption><ol>{notes}</ol><div class="completion-sign">完成标志：画面与说明中的目标状态一致后再继续。</div></figcaption>
    </figure>'''


FIXTURE_SHOTS = {
    "P15a": "P15a-main-graph.png",
    "P15b": "P15b-attribute-form.png",
}


def render_manual(shots: dict | None = None) -> str:
    """Render the editable HTML source with verified and fixture screenshots."""
    shots = SHOTS if shots is None else shots
    if tuple(shots) != tuple(SHOTS):
        raise ValueError("截图清单的 ID 或顺序与手册契约不一致")
    document = (ASSET_DIR / "manual-template.html").read_text(encoding="utf-8")
    for key, value in shots.items():
        token = "{{SHOT:" + key + "}}"
        if document.count(token) != 1:
            raise ValueError(f"截图占位符必须且只能出现一次：{key}")
        document = document.replace(token, screenshot(
            key, Path(value["path"]), value["alt"], value["markers"]))
    for key, filename in FIXTURE_SHOTS.items():
        document = document.replace("{{ASSET:" + key + "}}", data_url(ASSET_DIR / filename))
    sample = (ASSET_DIR / "manual_employee_v2.txt").read_text(encoding="utf-8").strip()
    document = document.replace("{{SAMPLE_TEXT}}", html.escape(sample))
    return document.replace("{{SAMPLE_DOWNLOAD}}", "data:text/plain;charset=utf-8," + quote(sample))


def main() -> None:
    OUTPUT.write_text(render_manual(), encoding="utf-8")
    print(f"built {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
