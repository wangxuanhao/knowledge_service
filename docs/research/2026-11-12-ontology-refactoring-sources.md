# 开源本体重构：ROBOT 与 Protégé／OWLAPI 源码核对

核对日期：2026-11-12。仅核对官方文档和公开源码，未在本机安装、运行这些 Java 工具。这里的“entity”是 OWL 术语，既可指 class/property，也可指 individual；不能把工具文档中的 entity merge 一律理解为业务实体实例合并。

## 结论

- **Protégé 官方源码确有合并术语的操作**，不是只有合并本体文件：`MergeEntitiesAction` 调用 `MergeEntitiesChangeListGenerator`，将源术语 IRI 的引用改为目标术语 IRI，再处理标签和标识符。[源码 P1、P2]
- **ROBOT 的 `rename` 明确支持多旧 IRI 映射至同一新 IRI**，官方文档称这些 OWL entities 将被合并。其 `merge` 命令则主要汇集本体公理／导入文件，两者不同。[R1、R2、R3]
- **停用不等于迁移**。ROBOT 将弃用术语的替代引用修复作为独立 `repair` 操作，且默认不迁移原术语的注释公理。[R4、R5]
- 这些操作只处理调用者交给它的 OWL 本体对象／导入闭包，不会自动连接并修改外部 PostgreSQL 实体表。独立存储的实例必须有自己的数据迁移计划。[P2、R3、A1；最后一句为架构推论]

## Protégé 的真实行为与边界

核对提交：`bf03cccc65ea664e139d6829bba6d209ef58ee55`（master，提交时间 2026-09-23）。

`MergeEntitiesAction` 取得 active ontology 和源、目标术语，明确选择 `MergeStrategy.DELETE_SOURCE_ENTITY`，生成整组 OWL changes 后交给 model manager 应用。[P1]

`MergeEntitiesChangeListGenerator` 的处理顺序是：

1. `replaceUsage`：对每个源术语创建 `OWLEntityRenamer`，处理范围是 `rootOntology.getImportsClosure()`，调用 `changeIRI(sourceIRI, targetIRI)`。
2. `replaceLabels`：把源 `rdfs:label` 和 `skos:prefLabel` 迁至目标的 `skos:altLabel`，保留语言标签；目标为 OBO IRI 时使用 `oboInOwl:hasRelatedSynonym`。
3. OBO 源 id 迁作目标 alternative id。
4. 若策略不是 DELETE，则在 root ontology 上为源加 `owl:deprecated`；当前上述 UI action 走的是 DELETE 策略。[P2]

注意：源码枚举第二项命名为 `DEPRECATE_TARGET_ENTITY`，但生成器实际给 **sourceEntities** 加 deprecated。描述行为应以实现为准，不能只读枚举名。[P2、P3]

官方单元测试直接断言源 class 的 `SubClassOf` 公理消失、目标对应公理存在、comment 随源迁至目标，标签变为 alt label。[P4] 本次没有找到该测试对 class assertions 的直接断言；“已经加载的实例类型断言也会随 class IRI 改写”来自 OWLAPI 对 referencing axioms 通用替换的源码推导，不是本次运行验证结果。[A1]

导入范围也需精确表述：生成器会对内存中 root 的整个 imports closure 生成 changes，并不是只生成 root 变化；本次未验证 Protégé 对每个导入文件的保存、只读权限或远程回写策略。因此不能据此承诺能够写回外部发布方本体或外部数据库。[P2]

## ROBOT 的真实行为与边界

核对提交：`37d514d2ac8970b6ce7b9f5cfe83ba08dc97e9d8`（master，提交时间 2026-10-01）。

### rename

官方文档明确写道：

> If two or more old IRIs are mapped to the same new IRI, these two entities will be merged.

`RenameOperation.renameFull` 使用 `OWLEntityRenamer(manager, Sets.newHashSet(ontology))`，逐映射调用 `manager.applyChanges(entityRenamer.changeIRI(oldIRI, newIRI))`。因此直接 rename 的处理范围是传入的一个 ontology 对象；不能把它泛化为自动改写所有导入源或远端实例库。[R1、R3]

### repair

`RepairOperation.repairInvalidReferences` 使用 imports closure 查找弃用资源的 OBO `IAO_0100001`（term replaced by）映射，再用 `OWLEntityRenamer` 改写引用。它会先保存原资源的声明、部分定义公理和默认不迁移的 annotation assertions，替换后再放回。因此这是“保留弃用资源档案并修复使用者引用”的流程，不应概括为把弃用资源所有内容直接吞进目标。[R4、R5]

ROBOT 使用的替代属性为 `http://purl.obolibrary.org/obo/IAO_0100001`。本项目目前使用 `dcterms:isReplacedBy`；若直接集成 ROBOT repair，需要明确适配，不能假设它自动识别本项目映射。[R5；本项目属性由父任务核对]

### merge

`robot merge` 把多个 ontology／导入公理组合到一个输出本体，不负责自动判断两个相近概念是否等价。[R2]

## OWLAPI 的通用引用替换

核对 version5 提交：`dee53400f3da0fe009bd50af1285b28095fa5178`（2026-11-02）。`OWLEntityRenamer` 的构造参数明确接收 ontology collection；`changeIRI` 遍历其中的 referencing axioms，通过 `OWLObjectDuplicator` 生成旧公理删除＋新公理添加，亦处理 ontology annotations。[A1]

因此，对同一被处理本体中支持的 OWL 公理，class 出现于父类、domain/range、class expression 或 individual class assertion 等位置时可以被通用结构替换。这个结论限于被加载并交给 renamer 的 OWL 公理；不涵盖未加载数据、字符串中恰好包含的 IRI、任意 RDF 存储、查询模板或业务代码。

## 对本项目的含义（建议，不是工具事实）

1. 区分标签修改、IRI 重命名、类等价、类型合并替代和实例身份合并。
2. 多个类型归并到一个目标类型时，已有不同实体仍是不同实体；只迁移分类和受影响引用，不能顺便去重实例。
3. 将父类、domain/range、SHACL 引用、实例类型和待审核候选映射放在同一个影响预览中；遇到冲突先解决，再发布／迁移。
4. 文件本体编辑器与有历史、审计、外部存储的服务不是同一执行模型：应保留稳定实体 ID、旧版本和可追溯迁移记录，不直接照搬文件内全局替换。

## 官方来源

- R1：[ROBOT rename](http://robot.obolibrary.org/rename.html)
- R2：[ROBOT merge](https://robot.obolibrary.org/merge)
- R3：[RenameOperation.java（固定提交）](https://github.com/ontodev/robot/blob/37d514d2ac8970b6ce7b9f5cfe83ba08dc97e9d8/robot-core/src/main/java/org/obolibrary/robot/RenameOperation.java)
- R4：[ROBOT repair](https://robot.obolibrary.org/repair)
- R5：[RepairOperation.java（固定提交）](https://github.com/ontodev/robot/blob/37d514d2ac8970b6ce7b9f5cfe83ba08dc97e9d8/robot-core/src/main/java/org/obolibrary/robot/RepairOperation.java)
- P1：[MergeEntitiesAction.java（固定提交）](https://github.com/protegeproject/protege/blob/bf03cccc65ea664e139d6829bba6d209ef58ee55/protege-editor-owl/src/main/java/org/protege/editor/owl/ui/merge/MergeEntitiesAction.java)
- P2：[MergeEntitiesChangeListGenerator.java（固定提交）](https://github.com/protegeproject/protege/blob/bf03cccc65ea664e139d6829bba6d209ef58ee55/protege-editor-owl/src/main/java/org/protege/editor/owl/model/merge/MergeEntitiesChangeListGenerator.java)
- P3：[MergeStrategy.java（固定提交）](https://github.com/protegeproject/protege/blob/bf03cccc65ea664e139d6829bba6d209ef58ee55/protege-editor-owl/src/main/java/org/protege/editor/owl/ui/merge/MergeStrategy.java)
- P4：[MergeEntitiesChangeListGenerator_TestCase.java（固定提交）](https://github.com/protegeproject/protege/blob/bf03cccc65ea664e139d6829bba6d209ef58ee55/protege-editor-owl/src/test/java/org/protege/editor/owl/model/merge/MergeEntitiesChangeListGenerator_TestCase.java)
- A1：[OWLEntityRenamer.java（固定提交）](https://github.com/owlcs/owlapi/blob/dee53400f3da0fe009bd50af1285b28095fa5178/api/src/main/java/org/semanticweb/owlapi/util/OWLEntityRenamer.java)
