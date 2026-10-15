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


def step(number: int, title: str, action: str, expected: str, image_html: str = "") -> str:
    return f'''<article class="step"><div class="step-no">{number:02d}</div><div class="step-body">
      <h3>{html.escape(title)}</h3><p><b>操作：</b>{action}</p><p><b>看到：</b>{expected}</p>{image_html}
    </div></article>'''


def chapter(chapter_id: str, number: str, title: str, lead: str, body: str,
            completion: str, misunderstanding: str) -> str:
    return f'''<section id="{chapter_id}" class="core-chapter">
      <header class="chapter-head"><span>{number}</span><div><h2>{html.escape(title)}</h2><p>{lead}</p></div></header>
      {body}
      <div class="chapter-close"><div class="completion-sign"><b>完成标志</b>{completion}</div>
      <div class="common-misunderstanding"><b>常见误解</b>{misunderstanding}</div></div>
    </section>'''


def trouble(title: str, symptom: str, cause: str, next_step: str) -> str:
    return f'''<article class="troubleshooting-item"><h3>{html.escape(title)}</h3>
      <p data-field="看到什么"><b>看到什么</b>{symptom}</p>
      <p data-field="原因"><b>原因</b>{cause}</p>
      <p data-field="下一步"><b>下一步</b>{next_step}</p></article>'''


def render_manual(shots: dict | None = None) -> str:
    shots = SHOTS if shots is None else shots
    expected = tuple(SHOTS)
    if tuple(shots) != expected:
        raise ValueError("截图清单的 ID 或顺序与手册契约不一致")
    figures = {key: screenshot(key, Path(value["path"]), value["alt"], value["markers"])
               for key, value in shots.items()}
    sample = (ASSET_DIR / "manual_employee_v2.txt").read_text(encoding="utf-8").strip()
    download = "data:text/plain;charset=utf-8," + quote(sample)

    chapters = []
    chapters.append(chapter("chapter-1", "01", "登录与角色：先确认自己能做什么",
        "主流程用管理员执行。超级管理员负责开账号；只读用户只能查看消费层。",
        step(1, "超级管理员开管理员账号", "进入“用户与权限”，创建一个管理员账号。", "账号清单出现新行，角色是管理员，状态是启用。", figures["S01a"] + figures["S01b"])
        + step(2, "切换到管理员", "退出超级管理员，用新账号登录。", "左下角角色徽章为“管理员”，本体层和实例层可见。", figures["S01c"]),
        "管理员账号能看到“本体建模层、知识写入、知识台账”。",
        "看得到按钮不等于权限只靠前端；真正的写权限仍由服务端校验。"))

    chapters.append(chapter("chapter-2", "02", "创建项目并识别默认本体 v1",
        "选择“加载默认本体”后，项目创建完成时已经有一份可用的结构版本。",
        step(1, "创建项目", "项目与运行 → 项目设置 → 创建项目，选择“加载默认本体”。", "项目卡片出现，并成为当前项目。", figures["S02a"] + figures["S02b"])
        + step(2, "查看默认结构", "进入“本体建模层”。", "标题为已发布本体，画布有默认类和关系；这就是本体 v1。", figures["S02c"])
        + '''<div class="callout"><b>先记住：</b>项目创建后并不是“还没有本体”。加载默认本体模式会自动得到本体 v1；后面的第一次自定义发布会得到本体 v2。</div>''',
        "当前项目可切换，且本体建模层显示“已发布本体”。",
        "画布上的类节点不是张三、产品部这类实例；它们是允许出现哪些知识的结构定义。"))

    chapters.append(chapter("chapter-3", "03", "本体建模层：从草案发布本体 v2",
        "完整建立员工、部门、工号、入职日期和隶属于。草案只有发布后才成为正式结构。",
        step(1, "新增两个实体类", "新增“员工”和“部门”，每次动作写入同一份草案。", "顶部显示草案未发布，画布出现两个类。", figures["S03a"])
        + step(2, "新增两个属性定义", "新增“工号”“入职日期”，domain 选员工，range 选字符串。", "属性计数增加；属性挂在员工类的定义上。", figures["S03b"])
        + '''<div class="rule"><b>属性不会单独画成节点。</b>本体画布把属性作为类详情里的定义，不把“工号”画成与“员工”平级的节点。这是刻意的视觉口径，不是属性丢了。</div>'''
        + step(3, "新增关系并审核", "创建“隶属于”，起点选员工，终点选部门；再点“校验并审核”。", "连线出现，阻断项处理完毕，发布按钮可用。", figures["S03c"] + figures["S03d"])
        + step(4, "写说明并发布", "发布说明写“新增员工、部门、属性和隶属关系”，发布。", "版本管理中出现本体 v2，并保留 v1。", figures["S03e"]),
        "版本管理显示 v2，发布说明能回答“这一版改了什么”。",
        "草案计数增加不代表已经生效；只有通过审核并发布，消费层才读到新结构。"))

    chapters.append(chapter("chapter-4", "04", "知识写入：准备文件、批次处理与结果核对",
        "按选模式、准备文件、设置解析与并发、上传、处理失败、核对结果和原文的顺序完成一次写入。固定材料用于演示，实际模型的措辞、数量和置信度可能变化。",
        '''<h3>先选对知识写入模式</h3>
        <p>这三项是“知识写入模式”，不是三种推理方式。只有前两种会做图谱抽取。</p>
        <table><thead><tr><th>模式</th><th>什么时候用</th><th>是否入图</th><th>结果去向</th></tr></thead><tbody>
          <tr><td><b>使用项目本体 · 正式入图</b></td><td>已有稳定的类、关系和属性定义，要按当前本体抽取。</td><td>通过的知识正式入图；待确认项保留待审核。</td><td>已入图去知识台账；待审核去知识审核。</td></tr>
          <tr><td><b>开放本体发现 · 候选暂存</b></td><td>文档可能出现当前本体里没有的概念，先发现再人工决定。</td><td>开放发现不正式入图。发现结果全部作为待建模候选暂存，不直接进入正式图谱。</td><td>去本体画布处理候选；人工建模与审核后才能成为正式结构。</td></tr>
          <tr><td><b>仅文档检索 · 不抽取图谱</b></td><td>只要保存切片供全文/向量检索，不需要实体、关系或属性。</td><td>否。这是主动不抽取，结果里没有实体是正常情况。</td><td>去原文数据源核对文档和切片。</td></tr>
        </tbody></table>'''
        + f'''<div class="sample"><div><b>manual_employee_v2.txt</b><a download="manual_employee_v2.txt" href="{download}">下载演示材料</a></div><pre>{html.escape(sample)}</pre></div>'''
        + step(1, "选模式", "进入“知识写入”，根据上表选择模式。本章固定材料选“使用项目本体”。", "模式说明与你要的入图结果一致。")
        + step(2, "选文件", "演示选择固定文本文件；批量规则推荐 Markdown + 相对附件 ZIP，办公文档可用 Word。图片路径须指向包内附件，网络图片只登记为外部链接。", "文件清单显示文件名和大小；按服务能力提示控制体积，每批最多 1000 个文档，更多材料拆成多个批次。", figures["S04a"])
        + step(3, "预览切片并设置并发", "点击“预览切片”，核对完整性；在“高级解析设置”调整切片策略、长度、重叠及解析并发，选择自动推荐或 1、2、4、8。本章需要工号、入职日期时勾选抽取实体属性。", "预览区切片可读；设置与所选模式一致。实际并发由服务能力决定。", figures["S04b"])
        + step(4, "上传并处理", "点击“上传并处理”。浏览器最多三路上传，全部文件上传成功后开始处理；部分上传失败时点“重试失败”补传，成功文件保留。", "页面出现批次卡，显示申请并发、实际并发和排队、处理中、完成、需要处理数量。")
        + step(5, "查看批次进度", "按状态筛选文件，用“下一页”查看后续文件。需要时暂停或继续；取消未开始文件不撤销已完成知识。遇到失败先按下面的恢复说明处理。", "每个文件有明确状态，失败行显示阶段、切片、公开原因、自动重试次数和下次时间。")
        + '''<h3>失败恢复：先处理原因，再选择继续方式</h3>
        <p>临时超时、限流和服务不可用自动退避重试。同批大量相同模型错误会暂停批次，检查模型连接后点“继续”；自动重试耗尽后需要人工处理，可下载失败明细逐文件核对。</p>
        <table><thead><tr><th>操作</th><th>何时使用</th><th>之后核对什么</th></tr></thead><tbody>
          <tr><td><b>从断点继续</b></td><td>原因已解决，继续失败与未完成切片。</td><td>兼容的成功切片被复用，文档继续处理。</td></tr>
          <tr><td><b>只重试失败切片</b></td><td>只重新执行明确失败的切片。</td><td>未开始切片仍待处理，之后用“从断点继续”完成。</td></tr>
          <tr><td><b>整份重新处理</b></td><td>模型、本体或关键设置改变，旧检查点不兼容。</td><td>产生新尝试，旧尝试与来源保留；修正文件内容须重新上传。</td></tr>
        </tbody></table>
        <h4>图片 OCR 失败怎么处理</h4>
        <p>ZIP 内本地图片先 OCR 再提取文字知识。OCR 失败或低置信度会阻止文档提交：点“查看原文与 OCR”，到解析视图核对文字、引擎和置信度，再点“重试 OCR”。若确认图片无知识文字，点“该图片无知识文字，忽略并继续”，填写原因；系统记录操作者与时间，然后回批次“从断点继续”。缺失附件应补齐知识包后重新上传。</p>
        <p>网络图片显示“外部图片，未固化”；OCR 只识别文字，不能判断流程线与图标语义。</p>'''
        + step(6, "打开结果工作区并按结果动作继续", "文件完成后点“查看抽取结果”，或打开结果工作区；按实际已入图、待审核、待建模、仅文档或失败分支继续。图中的仅文档任务应点“去原文数据源”。", "页面先切换到任务所属项目，再打开对应入口；不把“任务完成”当作知识正确的证明。", figures["S04d"])
        + '''<div class="callout"><b>正式入图的验收位置：</b>使用项目本体并完成入图后，点“去知识台账核对”，再按下图核对实体、关系和属性。</div>''' + figures["S04c"]
        + '''<h3>结果工作区五类分支</h3>
        <table><thead><tr><th>看到什么</th><th>点击哪里</th><th>完成标志</th></tr></thead><tbody>
          <tr><td><b>已入图</b></td><td>去知识台账核对</td><td>在实体、关系、属性视角核对到正确的知识和来源。</td></tr>
          <tr><td><b>待审核</b></td><td>去知识审核</td><td>对待确认知识完成收下、退回或不收的决定。</td></tr>
          <tr><td><b>待建模</b></td><td>去本体画布处理候选</td><td>在本体建模层核对新概念；需要时直接编辑草案并提交审核。</td></tr>
          <tr><td><b>仅文档</b></td><td>去原文数据源</td><td>能找到上传文档和切片；不要期待出现实体。</td></tr>
          <tr><td><b>失败</b></td><td>先核对批次行错误与失败明细；旧任务可查看任务日志</td><td>根据错误原因修正文件、模型或运行配置，再重试。</td></tr>
        </tbody></table>
        <div class="callout"><b>这是结果引导，不是新流程状态机。</b>本体建模层仍然可以直接编辑草案；结果卡只根据已有任务与断言状态告诉你下一步去哪里。</div>'''
        + '''<h3>核对固定版本原文：确认知识来自哪里</h3>
        <p>在图谱知识证据或原文数据源点击“在原文中打开”。阅读视图核对正文、表格与固化图片；解析视图核对块、行号、OCR 文字与图片区域框；原始视图只读源码并下载固定版本原文件。</p>
        <p>来自图片 OCR 的知识应能定位到原图文字区域。多来源知识分别核对各个证据分支；历史映射缺失时页面明确说明，不自动跳到新版文档。</p>'''
        + '''<p class="demo-note">隔离演示环境截图：为了让手册可重复验证，演示结果固定为“张三、产品部、隶属于、工号、入职日期”。生产环境由实际模型与材料决定，数量不一致时按本章验收方法核对，而不要强求与截图数字相同。</p>''',
        "知识台账能找到张三、产品部、隶属于、工号 E001 和入职日期 2025-01-06。",
        "“抽取完成”只说明任务结束；是否正确必须回到台账分视角核对。"))

    chapters.append(chapter("chapter-5", "05", "同一份知识，在脑图和台账中怎么看",
        "脑图回答“实体如何相连”；台账回答“每条记录是什么、挂在哪个本体、修订到第几版”。",
        step(1, "看实体脑图", "选择张三为根节点并生成脑图。", "张三与产品部通过隶属于相连。", figures["S05a"])
        + '''<div class="rule"><b>实体脑图当前不展示属性分支。</b>在服务 v1.1.0 中，工号和入职日期不会成为张三下面的脑图分支；需要去知识台账的属性视角查看。</div>'''
        + step(2, "依次核对三类正式知识", "在知识台账切换实体、关系、属性。", "实体看类型，关系看两端，属性看 subject、值与各自修订。", figures["S05b"] + figures["S05c"] + figures["S05d"])
        + '''<div class="callout"><b>知识台账可以切换到“属性”视角。</b>属性在这里是一等记录：有自己的记录 ID、本体归属、当前值和修订历史。</div>''',
        "脑图关系正确；实体、关系、属性三张台账都能对上固定材料。",
        "脑图没显示工号不等于工号没入库；脑图和属性台账服务于不同核对问题。"))

    chapters.append(chapter("chapter-6", "06", "发布本体 v3：结构变了，知识不会跟着猜",
        "在员工下面新增产品经理，再发布一版；然后立刻回到台账观察张三。",
        step(1, "新增产品经理", "新增实体类“产品经理”，父类选择员工。", "草案中出现员工 → 产品经理的继承关系。", figures["S06a"])
        + step(2, "发布本体 v3", "校验、审核、填写发布说明并发布。", "已发布结构包含产品经理，本体版本成为 v3。", figures["S06b"] + figures["S07a"])
        + step(3, "验证知识没有自动变化", "回到实体台账查张三。", "张三仍是员工，仍挂在写入时的本体 v2。", figures["S07b"])
        + '''<div class="rule"><b>发布本体不会自动修改已有知识。</b>发布改变的是“允许怎样描述知识”的结构，不是“张三究竟是什么岗位”的业务事实。</div>''',
        "本体 v3 有产品经理；张三的实体记录仍保持员工。",
        "新增子类不是批量改数据命令。系统不会根据姓名或上下级关系自行推断张三应变成产品经理。"))

    chapters.append(chapter("chapter-7", "07", "受控重分类：只迁本体归属，不替你判断业务事实",
        "先预演，再执行。员工在 v3 仍有效，可以保持类型不变、只迁本体归属；这同样会生成新的知识修订。迁移不等于业务岗位变化。",
        step(1, "打开迁移入口并预演", "在本体建模层查看待迁移、冲突和阻断，再执行预演。", "预演列出会迁移的组；没有依据的类型转换不会被加入。", figures["S08a"] + figures["S08b"])
        + step(2, "执行并回查", "只执行经过核对的迁移组，然后回到实体台账。记录张三当前的修订号和依据本体，作为下一步编辑的基线。", "被选中且成功迁移的记录生成 rN+1、关联本体 v3；未选中的记录保持不变。张三不会因此自动成为产品经理。", figures["S08c"] + figures["S08d"])
        + '''<div class="rule"><b>受控重分类不会自动把“员工”改成“产品经理”。</b>它解决术语停用、替换或本体归属迁移；它不创造岗位晋升这一业务事实。</div>''',
        "迁移结果可解释，张三仍为员工，没有出现系统擅自推断的类型变化。",
        "“本体有一个更具体的子类”不等于“所有父类实例都应归到这个子类”。"))

    chapters.append(chapter("chapter-8", "08", "单独修改张三，并核对脑图和历史",
        "业务确认张三确实是产品经理后，先确认这条记录已迁到包含该类型的本体 v3，再从图谱或台账编辑同一条记录。普通编辑不会自动迁移本体。",
        step(1, "编辑实体类型", "在张三行点编辑，核对“修订 rN · 依据本体 v3”，再把员工改为产品经理并保存。若仍依据 v2，先执行上一章的受控迁移。", "保存回执显示新的修订 rN+1，本体归属仍是 v3。迁移若已生成 r2，本次编辑通常生成 r3；不能照抄旧截图中的修订号。", figures["S09a"])
        + step(2, "查看修订历史", "点击“修订历史”，依次选择旧修订和新修订。", "旧的员工记录、迁移产生的修订和本次编辑都可追溯；每次修订保留自己的本体发布 ID。", figures["S09b"])
        + step(3, "重新生成脑图", "回到实体脑图刷新张三。", "脑图使用所选时间范围内的实体和关系，仍不展示属性分支。", figures["S09c"])
        + '''<h3 id="graph-edit-workflow">直接在图谱编辑：和台账核对同一条修订</h3>
        <p class="demo-note">本节操作与版本规则更新于 2026-10-15。已有截图用于说明入口；按钮文案、修订号和本体归属以当前界面及保存回执为准。</p>
        <ol>
          <li><b>选择项目和知识范围。</b>进入“检索与交互图谱”，点击实体节点或关系连线；若设置了系统时间或本体归属筛选，先确认正在查看的范围。</li>
          <li><b>打开编辑弹窗。</b>详情中点击“编辑这个实体”或“编辑这条关系”。核对记录名称、知识修订 rN 和依据本体 vN；同号本体的标注发布还需核对发布短标识。</li>
          <li><b>修改具体知识。</b>实体可修改名称、类型和业务有效时间；关系可修改显示文本、关系类型、起点、终点和业务有效时间。类型选项来自这条知识绑定的本体；要用新本体才有的类型，先迁移，再重新打开编辑。</li>
          <li><b>保存新修订。</b>点击“保存实体修改为新修订”或“保存关系修改为新修订”。保存成功生成该记录的 rN+1，旧修订保留；普通编辑不改变本体发布 ID、不创建本体版本，也不增加无关实体或关系的修订号。</li>
          <li><b>核对图谱与台账。</b>保存回执、已打开的图谱详情和台账自动刷新到新修订。关闭弹窗后点击详情中的“在台账查看”，核对同一记录 ID、相同 rN+1 和相同“依据本体 vN”；台账不会另外生成一份记录或再增一次修订。</li>
          <li><b>查看历史。</b>点击“修订历史”，核对前后内容、不可变修订 ID、本体归属、记录时间和业务有效期。选择旧修订只读查看，不会覆盖当前记录。</li>
        </ol>
        <div class="rule"><b>完成标志：</b>图谱详情与台账的同一条记录显示相同的新修订，历史保留旧修订，本体归属与普通编辑前一致。仅打开、取消或保存失败不会新增修订。</div>
        <h4>保存时遇到问题</h4>
        <ul>
          <li><b>版本冲突：</b>其他操作已修改记录。重新读取最新修订，核对变化后再编辑；不要手工提高预期修订号强行覆盖。</li>
          <li><b>未知本体或本体加载失败：</b>结构化编辑不会回退到当前本体。先核对项目、网络与本体归属；归属确实未知时通过治理明确目标，勿随意在高级 JSON 中改本体 ID。</li>
          <li><b>已保存，但部分视图刷新失败：</b>修订已经提交，先刷新图谱或台账核对回执中的修订号，不要把“保存”当成刷新按钮重复提交。</li>
          <li><b>切到历史范围后看到旧内容：</b>系统时间和本体归属是查询条件。本体筛选表示所选时间内仍绑定该发布的知识，不是整库历史快照；跨归属端点可能被过滤。先统一查询范围，再比较图谱和台账。</li>
        </ul>''',
        "张三当前类型为产品经理，历史中仍能看到员工版本。",
        "知识修订 rN 不等于本体 vN；图谱与台账共用同一份记录，不存在各自独立的一套知识版本。"))

    chapters.append(chapter("chapter-9", "09", "属性修订、关系软删除与恢复",
        "实体、关系、属性各自有修订历史；改一条不会让另外两类记录的修订号同步增加。",
        step(1, "修订工号", "在属性视角把工号修订为 E001-A。", "工号记录的修订号增加；张三实体记录不因此再增一版。", figures["S10a"])
        + step(2, "软删除关系", "删除“张三隶属于产品部”并确认影响。", "关系退出当前视图，但旧版和操作记录仍保留。", figures["S10b"])
        + step(3, "恢复关系", "从可撤销操作或历史版本恢复。", "恢复生成新修订，当前关系重新出现。", figures["S10c"]),
        "工号有自己的新修订；关系软删除后可恢复且历史仍可查。",
        "软删除不是数据库物理删除；恢复也不是把时间倒回去，而是生成一个新的当前修订。"))

    version_body = '''<div class="version-grid">
      <article><span>软件</span><h3>服务 v1.1.0</h3><p>整套应用的发布版本。手册截图和界面能力以它为准。</p></article>
      <article><span>项目结构</span><h3>本体 v1 / v2 / v3</h3><p>一个项目内的不可变结构发布：默认 v1，自定义 v2，产品经理 v3。</p></article>
      <article><span>单条知识</span><h3>记录修订 rN</h3><p>每条实体、关系、属性、片段各自递增；张三 r2 不会让产品部也变 r2。</p></article>
    </div>
    <p><b>两条独立版本轴：</b>知识用不可变的 ontology_id 关联具体本体发布，不是按 rN 与 vN 的数字相等关联。以下以单条实体为例；关系和属性遵循相同规则。这里的本体版本是项目当前发布，知识自己的“依据本体”另行列明。</p>
    <table><thead><tr><th>动作</th><th>服务版本</th><th>当前本体</th><th>张三记录及其依据本体</th><th>工号属性</th></tr></thead><tbody>
      <tr><td>创建项目</td><td>v1.1.0</td><td>v1</td><td>—</td><td>—</td></tr>
      <tr><td>发布员工/部门结构</td><td>v1.1.0</td><td>v2</td><td>—</td><td>—</td></tr>
      <tr><td>写入固定材料</td><td>v1.1.0</td><td>v2</td><td>员工 · r1 · 依据 v2</td><td>E001 · r1 · 依据 v2</td></tr>
      <tr><td>在图谱修改张三名称</td><td>v1.1.0</td><td>v2</td><td>员工 · r2 · 仍依据 v2；台账同步为 r2</td><td>r1（不变）</td></tr>
      <tr><td>发布产品经理类型</td><td>v1.1.0</td><td>v3</td><td>员工 · r2 · 仍依据 v2</td><td>r1 · 仍依据 v2</td></tr>
      <tr><td>只将张三迁到 v3</td><td>v1.1.0</td><td>v3</td><td>员工 · r3 · 依据 v3</td><td>未选中则不变；若一并迁移则产生自己的新修订</td></tr>
      <tr><td>单独编辑张三为产品经理</td><td>v1.1.0</td><td>v3</td><td>产品经理 · r4 · 仍依据 v3</td><td>不因实体编辑而增修订</td></tr>
      <tr><td>单独修订工号</td><td>v1.1.0</td><td>v3</td><td>r4（不变）</td><td>仅工号增加修订；保留它当时的依据本体</td></tr>
    </tbody></table>
    <div class="compare"><article><h3>只发布 v3</h3><p>本体画布：出现产品经理。</p><p>图谱与台账：已有知识的内容、修订号和本体归属保持不变。</p></article><article><h3>迁移 + 单独编辑张三</h3><p>本体结构仍是 v3。</p><p>迁移和编辑分别产生新的知识修订；图谱与台账按相同时间范围读取同一份结果。</p><p>修订号取决于实际操作次数，不能用截图数字判断成功与否。</p></article></div>'''
    chapters.append(chapter("chapter-10", "10", "三种版本到底是什么关系",
        "版本号看起来相似，但它们的对象、触发动作和影响范围完全不同。", version_body,
        "能对任意一个版本号回答：它属于软件、项目结构，还是某一条知识记录。",
        "不要说“现在是 v2”而不带对象；应说“本体 v2”或“张三记录 r2”。"))

    qa_runtime_body = '''<div class="callout"><b>问答先看原文，再看结论。</b>
      每条回答下方的前三条依据默认展开；每条依据的第一份冻结原文默认可见。
      “另有 N 个来源”只展开这一条依据的其他来源，“再看 N 条依据”才展开其余依据。</div>
    <div class="rule"><b>图谱关系路径不是 OWL 推理。</b>它只展示命中实体沿现有关系边可到达的多跳路径，用来解释“这些实体在当前图中怎么连”。它不根据 OWL 公理生成新事实。<b>本阶段暂不实施 OWL 推理</b>，也不把路径展示宣称为 OWL 2 RL 能力。</div>
    <div class="version-grid">
      <article><span>冻结来源</span><h3>不追当前版本</h3><p>标题、摘录、文档版本与检索通道来自生成答案时保存的 offered 溯源分支；手工录入或历史缺口会明确标注，不拿今天的文档补写。</p></article>
      <article><span>运行能力</span><h3>Milvus collection_ready</h3><p>项目与运行的健康信息要同时核对 configured、connected、collection_ready、实际维度与期望维度。已配置但不可用时状态是 degraded。</p></article>
      <article><span>发布门禁</span><h3>Semantica 质量检查</h3><p>草案校验保留 Semantica 版本、指标、阈值与问题列表；质量引擎不可用属于阻断错误，不能假装校验通过。</p></article>
    </div>
    <div class="rule"><b>本体切片收据：</b>受控抽取应记录用了哪个本体版本、命中了哪些术语以及选择原因；看到降级为全本体时要单独审核，绝不能静默切换成开放发现。</div>
    <ol><li>问答完成后，先直接阅读默认展开的原文，再核对答案中的 [E1] / [E2]。</li>
      <li>需要审计时点击引用编号，打开完整的历史溯源抽屉。</li>
      <li>到“项目与运行”核对本地模型 identity 与 1024 维是否和 Milvus collection 一致。</li>
      <li>草案接口的 validation_report.semantica_quality 保存 Semantica 质量报告；阻断项修复后重新校验。
      当前页面主要展示现有业务校验警告，Semantica 警告尚未统一接入逐条确认入口，需另行核对质量报告。</li></ol>'''
    chapters.append(chapter("chapter-11", "11", "问答原文、Milvus 与本体质量怎么验收",
        "把消费层看到的依据、运行时向量能力和本体发布门禁放在同一套验收口径里。",
        qa_runtime_body,
        "问答能直接看到冻结原文；运行状态显示 1024 维且 collection_ready；草案保存 Semantica 质量报告。",
        "只看到回答正文不代表可追溯；只看到 Milvus 进程 healthy 也不代表 collection 与模型维度一致。"))

    operations_body = '''<div class="callout"><b>审计人由登录身份决定。</b>
      创建、审核、发布和重分类由服务器记录用户 ID、用户名和显示名；页面不能指定另一位操作人。
      关闭认证的环境会明确显示“未启用认证”。</div>
    <div class="callout"><b>统一运行环境：</b>本项目的 Python/pytest 命令使用 conda 环境 <code>llm_model</code>。</div>
    <ol><li>探活访问 /livez；就绪检查访问 /readyz。模型仍在加载或必需向量后端不可用时，就绪返回 503。</li>
      <li>/api/health 的 capability_matrix 分别报告 keyword、semantic、llm、ocr、neo4j。
      semantic 为 ready 才代表真实模型已完成编码验证且向量 collection 就绪；远程 LLM 未探测时显示 degraded。</li>
      <li>本体发布成功意味着权威版本已提交。索引由后台 outbox 同步，pending/running/retry_wait 都不表示索引已完成。
      disabled 表示未启用 Milvus，failed 保留失败原因。只有 completed 才表示消费成功。</li>
      <li>GET /api/projects/{项目ID}/ontology-index 查看权威版本与索引版本；POST 同一路径的 /repair 触发补偿。</li>
      <li>维度自动读取本地 model 的 config 并核对真实编码输出；不要把 1024 当成所有模型的固定值。
      换模型导致维度不匹配时，“构建语义索引”会迁移共享集合并恢复全部项目；普通重建只处理当前项目。
      rebuilding 期间项目对账不报告 ready；失败后重试会继续恢复全项目，不能把空集合当完成。</li>
      <li>进入“项目与运行 → 后台任务”，任务卡片可取消、重试或重新排队。
      接口对应 POST /api/jobs/{任务ID}/retry、/cancel、/resume。
      重启后持久任务会在原租约过期后重新领取；取消在下一个处理检查点生效，已提交的事实不自动回滚。
      上传文件在停止任务期间保留以支持重试。</li></ol>'''
    chapters.append(chapter('chapter-12', '12', '身份、任务和索引同步怎么核查',
        '发布版本与索引同步分别核查；任务失败可查看原因并重新处理。', operations_body,
        '能辨认当前用户、模型就绪、任务状态和权威版本与索引版本是否一致。',
        'Milvus 进程 healthy 或本体发布成功都不能替代项目索引对账。'))

    troubleshooting = "".join([
        trouble("创建项目后看不到本体", "本体建模层提示读不到状态或空画布。", "项目尚未选中，或页面还在加载。", "先在左上角选择项目；仍为空就刷新页面，再确认项目模式是加载默认本体。"),
        trouble("发布按钮是灰的", "按钮不可点。", "草案未校验、仍有待审核操作、没有发布说明，或警告未确认。", "从底栏提示依次处理：校验并审核 → 逐条处理 → 填发布说明 → 确认警告。"),
        trouble("属性没有画在本体节点上", "画布只看到类和关系。", "v1.1.0 以类详情承载属性定义，属性不是独立节点。", "选中员工查看属性定义；实例值到知识台账的属性视角核对。"),
        trouble("脑图里没有工号", "张三脑图只有实体和关系。", "实体脑图当前排除属性分支。", "进入知识台账 → 属性，按张三筛选并查看工号、入职日期。"),
        trouble("发布 v3 后张三没变", "本体已有产品经理，张三仍是员工。", "发布本体不修改实例；员工在 v3 仍有效。", "先确认业务事实，再在知识台账单独编辑张三类型。"),
        trouble("受控重分类没有把员工变成产品经理", "预演没有该转换。", "重分类只处理有结构依据的归属迁移，不做岗位推断。", "若业务确认张三是产品经理，单独修订实体；不要强行扩大迁移范围。"),
        trouble("抽取数量和截图不同", "模型返回的实体或关系数量不同。", "真实 LLM、模型参数和文本细节会影响结果。", "以固定材料和本体约束为准，逐条审核；截图只教入口和验收方式。"),
        trouble("仅文档模式没有实体", "任务完成，但结果工作区没有实体、关系或属性。", "“仅文档检索”主动不执行图谱知识抽取。", "去原文数据源核对文档和切片；需要入图时重新选择项目本体或开放发现模式。"),
        trouble("抽取结果为空", "使用项目本体或开放发现模式，任务完成却没有识别出知识。", "系统已执行抽取，但材料、切片、本体匹配或模型输出没有产生可用断言。", "先查切片预览和任务日志，再核对模式与项目本体；修正材料或配置后重试。"),
        trouble("待确认去哪里处理", "结果卡显示待审核或待建模，但不确定下一步。", "待审核是已有结构下的知识确认；待建模是本体中还没有相应概念。", "待审核点“去知识审核”；待建模点“去本体画布处理候选”。"),
        trouble("软删除后想找回", "当前关系视角不再显示该关系。", "软删除生成了删除修订，不是物理抹除。", "打开历史或可撤销操作，核对目标 ID 后恢复；恢复会生成新修订。"),
    ])

    body = "".join(chapters) + f'''<section id="troubleshooting" class="appendix"><header class="chapter-head"><span>13</span><div><h2>卡住时按现象排查</h2><p>每一项都给出“看到什么 / 原因 / 下一步”，不要只停在错误描述。</p></div></header><div class="trouble-grid">{troubleshooting}</div></section>'''

    css = r'''
    :root{--ink:#123a3d;--muted:#61787a;--paper:#f6f2e9;--card:#fffdf7;--teal:#006c68;--teal2:#0b8f86;--line:#cbd8d2;--amber:#f2a900;--red:#e43e36;--nav:#0b3334}
    *{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--ink);background:linear-gradient(120deg,#ede7d9,#f8f5ec 45%,#e5efeb);font-family:"Microsoft YaHei UI","Noto Sans CJK SC",sans-serif;line-height:1.72}
    .cover{min-height:82vh;padding:72px max(7vw,40px);color:#effffb;background:radial-gradient(circle at 80% 15%,#0ba59655,transparent 33%),linear-gradient(135deg,#072f31,#0b5653 70%,#08766e);position:relative;overflow:hidden}.cover:after{content:"";position:absolute;right:-90px;bottom:-160px;width:480px;height:480px;border:1px solid #6ee6d855;border-radius:50%;box-shadow:0 0 0 55px #6ee6d815,0 0 0 110px #6ee6d810}.kicker{letter-spacing:.23em;color:#67eadc;font-weight:800}.cover h1{font-family:"STZhongsong","SimSun",serif;font-size:clamp(42px,6vw,86px);line-height:1.06;margin:.22em 0}.cover .subtitle{font-size:20px;max-width:780px;color:#c7e6e1}.cover-meta{display:flex;gap:12px;flex-wrap:wrap;margin-top:34px}.cover-meta span{border:1px solid #78d8cd66;border-radius:99px;padding:8px 15px;background:#ffffff0d}.route{display:grid;grid-template-columns:repeat(7,1fr);gap:7px;margin-top:56px;max-width:1100px}.route span{padding:10px 8px;text-align:center;border:1px solid #83dcd155;border-radius:8px;background:#062929aa;font-size:13px}.route i{font-style:normal;color:#f8b933}
    .layout{display:grid;grid-template-columns:260px minmax(0,1120px);gap:34px;max-width:1460px;margin:0 auto;padding:42px 28px}.toc{position:sticky;top:20px;align-self:start;background:var(--nav);color:#d8f2ee;border-radius:16px;padding:20px;box-shadow:0 16px 40px #143b3630}.toc b{display:block;color:#67eadc;margin-bottom:10px}.toc a{display:block;color:#d8f2ee;text-decoration:none;border-left:2px solid #2b7470;padding:7px 11px;font-size:14px}.toc a:hover{border-color:var(--amber);color:#fff}.content{min-width:0}.intro{background:var(--card);border:1px solid var(--line);padding:28px;border-radius:16px;margin-bottom:28px}.intro h2{margin-top:0}.legend{display:flex;gap:14px;flex-wrap:wrap}.legend span{font-size:13px}.legend i{display:inline-grid;place-items:center;width:24px;height:24px;margin-right:5px;border-radius:50%;background:var(--red);color:#fff;font-style:normal;font-weight:900}
    .core-chapter,.appendix{background:var(--card);border:1px solid var(--line);border-radius:18px;margin:0 0 32px;padding:32px;box-shadow:0 10px 30px #153e3820}.chapter-head{display:flex;gap:18px;border-bottom:1px solid var(--line);padding-bottom:19px;margin-bottom:24px}.chapter-head>span{font:800 36px/1 Georgia,serif;color:var(--teal2)}.chapter-head h2{margin:0;font-family:"STZhongsong","SimSun",serif;font-size:29px}.chapter-head p{margin:4px 0 0;color:var(--muted)}
    .step{display:grid;grid-template-columns:54px minmax(0,1fr);gap:14px;margin:28px 0}.step-no{font:800 20px/46px Georgia,serif;text-align:center;width:46px;height:46px;border-radius:50%;background:var(--ink);color:#fff}.step-body h3{margin:4px 0 7px}.step-body>p{margin:4px 0}.step-body>p b{color:var(--teal)}
    figure{margin:20px 0 30px;border:1px solid #b9ccc6;border-radius:12px;overflow:hidden;background:#eef5f2}.shot-head{display:flex;gap:12px;align-items:center;padding:9px 13px;background:#113e40;color:#effffb}.shot-head span{background:var(--amber);color:#173437;border-radius:4px;padding:2px 8px;font-weight:900}.shot-stage{position:relative;line-height:0;background:#fff}.shot-stage img{display:block;width:100%;height:auto}.shot-marker{position:absolute;left:var(--x);top:var(--y);width:var(--w);height:var(--h);border:3px solid var(--red);border-radius:8px;box-shadow:0 0 0 2px #fff8,0 5px 14px #6b100b45;pointer-events:none}.shot-marker:before{content:attr(data-n);position:absolute;left:-4px;top:-30px;display:grid;place-items:center;width:27px;height:27px;border-radius:50%;background:var(--red);color:#fff;font:900 14px/1 sans-serif;box-shadow:0 2px 0 #9f1e19}.shot-stage:hover .shot-marker{animation:pulse 1.15s ease-in-out infinite alternate}@keyframes pulse{to{box-shadow:0 0 0 5px #fff9,0 6px 17px #6b100b55}}
    figcaption{padding:15px 18px 17px;background:#f3f8f6;line-height:1.6}figcaption ol{margin:0;padding:0;list-style:none;display:grid;gap:5px}figcaption li b{display:inline-grid;place-items:center;width:24px;height:24px;border-radius:50%;background:var(--red);color:#fff;margin-right:8px}.completion-sign{border-left:4px solid #10a37f;background:#e8f7f1;padding:11px 14px;margin-top:12px}.chapter-close{display:grid;grid-template-columns:1fr 1fr;gap:13px;margin-top:28px}.chapter-close>div{margin:0}.chapter-close b{display:block}.common-misunderstanding{border-left:4px solid var(--amber);background:#fff5d9;padding:11px 14px}.callout,.rule,.demo-note{padding:15px 18px;border-radius:10px;margin:20px 0}.callout{background:#e7f3f0;border:1px solid #acd2ca}.rule{background:#173f40;color:#effffb;border-left:6px solid var(--amber)}.demo-note{background:#fff2d8;border:1px dashed #d69715}.sample{background:#102f31;color:#dff6f2;border-radius:12px;padding:16px 18px;margin:18px 0}.sample>div{display:flex;justify-content:space-between}.sample a{color:#ffd56a}.sample pre{white-space:pre-wrap;font:16px/1.8 "Microsoft YaHei UI",sans-serif;margin:12px 0 0;color:#fff}
    .version-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.version-grid article{border-top:5px solid var(--teal);background:#eef5f2;padding:17px;border-radius:8px}.version-grid span{font-size:12px;color:var(--muted);letter-spacing:.15em}.version-grid h3{margin:4px 0}table{width:100%;border-collapse:collapse;margin:22px 0;font-size:14px}th,td{text-align:left;padding:10px 9px;border-bottom:1px solid var(--line)}th{background:#143f40;color:#fff}.compare{display:grid;grid-template-columns:1fr 1fr;gap:14px}.compare article{border:1px solid var(--line);padding:16px;border-radius:10px}.compare h3{margin-top:0;color:var(--teal)}
    .trouble-grid{display:grid;grid-template-columns:1fr 1fr;gap:13px}.troubleshooting-item{border:1px solid var(--line);border-radius:10px;padding:16px;background:#fff}.troubleshooting-item h3{margin:0 0 8px}.troubleshooting-item p{margin:7px 0;color:#334f50}.troubleshooting-item p b{display:inline-block;min-width:72px;color:var(--teal)}.foot{text-align:center;padding:34px;color:#607675}.foot strong{color:var(--ink)}
    @media(max-width:900px){.layout{grid-template-columns:1fr}.toc{position:static}.route{grid-template-columns:repeat(2,1fr)}.chapter-close,.version-grid,.compare,.trouble-grid{grid-template-columns:1fr}.core-chapter,.appendix{padding:20px}.step{grid-template-columns:42px 1fr}.step-no{width:38px;height:38px;line-height:38px}.shot-head{align-items:flex-start}}
    @media print{body{background:#fff}.cover{min-height:auto;padding:34px;background:#0b5653;-webkit-print-color-adjust:exact;print-color-adjust:exact}.layout{display:block;max-width:none;padding:0}.toc{display:none}.core-chapter,.appendix,.intro{box-shadow:none;border:0;border-radius:0;padding:18px 0;margin:0;break-before:page}figure{break-inside:avoid;page-break-inside:avoid}.shot-stage:hover .shot-marker{animation:none}.chapter-close{break-inside:avoid}}
    '''

    toc = "".join(f'<a href="#chapter-{i}">{i:02d} · {title}</a>' for i, title in [
        (1,"登录与角色"),(2,"项目与本体 v1"),(3,"建模并发布 v2"),(4,"知识写入与批次处理"),(5,"脑图与三类台账"),
        (6,"发布本体 v3"),(7,"受控重分类"),(8,"单独修改张三"),(9,"修订、删除与恢复"),(10,"三种版本关系"),
        (11,"问答原文与运行验收"),(12,"身份、任务与索引核查")])
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>知序 Knowledge Service · 傻瓜级操作手册（服务 v1.1.0）</title><style>{css}</style></head>
    <body><header class="cover"><div class="kicker">知识从结构到可核验事实</div><h1>知序 Knowledge Service<br>傻瓜级操作手册</h1><p class="subtitle">服务 v1.1.0 · 一个案例走完全流程：建项目 → 改本体 → 写知识 → 看脑图与台账 → 迁移 → 修订与恢复。</p><div class="cover-meta"><span>离线单文件</span><span>33 张真实界面截图</span><span>每图 1～3 个编号框</span><span>演示数据：张三 / 产品部</span></div><div class="route"><span>① 登录</span><span>② 项目 + 本体 v1</span><span>③ 发布 <i>v2</i></span><span>④ 写入知识</span><span>⑤ 发布 <i>v3</i></span><span>⑥ 迁移 / 修订</span><span>⑦ 历史 / 恢复</span></div></header>
    <div class="layout"><nav class="toc"><b>从头走到尾</b>{toc}<a href="#troubleshooting">13 · 排错</a></nav><main class="content"><section class="intro"><h2>怎么读这本手册</h2><p>每一步只做一件事。先看“操作”，再对照截图中的红框编号，最后确认“完成标志”。界面截图取自真实服务 v1.1.0 的隔离演示项目；截图里的随机项目后缀不需要照抄。</p><div class="legend"><span><i>1</i>编号框：这一步看这里</span><span><b>本体</b>＝项目结构</span><span><b>知识</b>＝实体 / 关系 / 属性实例</span><span><b>修订</b>＝某一条记录的历史</span></div></section>{body}</main></div>
    <footer class="foot"><strong>知序 Knowledge Service · 服务 v1.1.0</strong><br>本手册不依赖网络资源；保存此 HTML 即可离线查看和打印。</footer></body></html>'''


def main() -> None:
    OUTPUT.write_text(render_manual(), encoding="utf-8")
    print(f"built {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
