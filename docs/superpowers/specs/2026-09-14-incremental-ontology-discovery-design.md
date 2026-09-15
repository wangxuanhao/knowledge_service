# 持续开放发现与受控本体发布设计

日期：2026-09-14

## 目标

允许用户在本体冷启动阶段连续上传多批文档并累计开放发现候选，反复预览累计草案；只有经过审核的本体才进入正式版本。发布本体与候选实例入图解耦，避免第二次发布因历史候选 ID 冲突失败，也避免不同本体版本产生平行实体和关系。

## 产品流程

1. 文档以 `discovery` 模式解析，候选继续保存在原文版本中并保留证据。
2. 本体发现页汇总全部当前候选，并显示 `pending / included_in_draft / approved / materialized` 状态计数。
3. 用户可以多次生成累计草案。草案以当前已发布本体为基线，保留已有术语 IRI，并给出新增、保留和移除差异。
4. 发布操作只保存新的不可变本体版本并记录父版本、差异及来源草案，不直接写正式实体或关系。
5. 发布后，界面明确提示用户切换到“使用项目本体”重新解析历史或后续文档。正式图谱只来自受控提取。
6. 新的开放候选继续进入下一轮增量草案；已发布候选不会被误报为尚未审核。

## 数据与身份

- 候选出现 ID 继续由文档、切片及局部抽取 ID 组成，用于保留每次出现和来源。
- 草案保存完整候选 ID 快照，不覆盖原始候选。
- 候选状态由草案生命周期推导：未进入草案为 `pending`，进入未发布草案为 `included_in_draft`，进入已发布草案为 `approved`；只有正式记录明确引用候选 ID 时才是 `materialized`。
- 本体版本拥有独立 UUID；本体元数据记录 `parent_version_id`、`source_draft_id` 和 `diff`。
- 类、关系和属性使用不含版本号的项目级稳定 IRI。候选名称命中现有术语标签、local name 或 IRI 时复用该 IRI。

## 本体归纳与差异

`_induce` 接收可选基线 Turtle。生成图首先载入基线，再加入候选推导的新术语和约束。已有术语不得因为新版本而重新分配 IRI。

草案差异按术语 IRI 比较三类资源：class、relation、attribute。API 返回每类的 `added / retained / removed`；累计草案正常情况下 `removed` 应为空，除非以后增加显式编辑能力。

创建草案时记录当时的 `parent_ontology_id`。发布时若项目最新本体已经变化，则返回版本冲突，要求重新生成草案。

## API 行为

- `GET /api/projects/{p}/ontology-discovery`：保留现有汇总，新增 `candidate_status_counts`、`pending_candidate_count`、`approved_candidate_count` 和发布后重提取提示。
- `POST /api/projects/{p}/ontology-discovery/drafts`：生成基于当前本体的累计草案，保存父版本和 diff。
- `POST /api/projects/{p}/ontology-discovery/drafts/{draft_id}/publish`：只发布本体，返回 `mapped_entities = mapped_relations = mapped_attributes = 0` 作为兼容字段，并返回 `requires_controlled_reingest = true`。

旧版已经把候选物化成正式图谱的项目不自动删除数据。新逻辑从启用后阻止继续直接物化；管理员可通过现有快照、记录历史和软删除能力处理试验数据。

## 界面

本体发现页将“发布本体并映射候选”改为“发布本体版本”。发布成功后展示父版本、差异和“请使用当前本体重新解析历史文档”的明确提示。图谱和脑图提示不再声称发布草案会自动入图。

## 失败与一致性

- 没有候选时不能创建草案。
- 草案只能发布一次。
- 父版本过期时拒绝发布。
- Turtle 无法解析或归纳失败时不保存草案。
- 本体保存成功和草案状态变更需要在服务锁内执行；若草案状态保存失败，后续重复发布由父版本冲突阻止，并可根据 `source_draft_id` 恢复显示。

## 验证

- 多批开放发现累计候选并生成一个草案。
- 已有 V0 时，新草案保留 V0 术语 IRI并记录 parent/diff。
- 发布不创建实体或关系。
- 第二次发布同一草案返回冲突。
- 草案创建后本体被并发更新，发布返回冲突。
- 候选状态计数随草案创建和发布变化。
- 全部服务测试及 Web UI 合约测试通过。
