# 上传输入验证样本

本目录包含用户提供的原始资料和人工合成的功能样本。原始文件保留原名、原字节；涉及实际业务的 PDF/Word 仅用于本机验证，不应随普通代码提交自动公开。`cases/` 是可复用的合成样本。

## 已有资料的检查结果

| 文件 | 实际类型 | 检查结果 | 当前产品状态 |
| --- | --- | --- | --- |
| `技术交底书模板.doc` | Word 97–2003 OLE 文件 | 是真实 `.doc`，不是改后缀的 DOCX | 当前不支持；需要转换成 DOCX 或部署受控 Office 转换器；不可直接改后缀 |
| `d93666a3-7122-42df-ba64-446df1ddd29c.pdf` | 30 页混合 PDF | 可读取文本；第 24–30 页几乎无可抽取文字且含图像 | 需要逐页/逐区域检测 OCR，不能整份当成纯文字 PDF |
| `附件3-全家享礼包基础版及承诺抵消-业务协议.pdf` | 19 页以图像正文为主的 PDF | 每页有少量可提取文字，主要为重复内容；19 页均需 OCR 检查 | 不能用“有文字”作为跳过 OCR 的依据 |

这些结果是结构检查及 OCR 候选诊断，不代表完整 OCR、知识抽取或问答验收通过。`validation-report.json` 保存哈希、页数和各页诊断，不保存正文内容。

2026-11-16 的有界真实管线试验：30 页 PDF 仅处理第一页，Docling 自动 OCR 配置下成功导出 1718 字符，模型初始化及单页处理共约 85.61 秒；19 页扫描协议的第一页未在 90 秒窗口内完成。两项都没有处理全文。冷启动耗时不能线性乘以页数；超时也不等于文件损坏。这说明原件预览应绕过全文解析，正式解析需要后台阶段、耗时和任务恢复机制。

另将扫描协议第一页的预览图交给本地 RapidOCR 单独识别，约 12.95 秒得到 964 字符、51 个文本块，平均置信度约 0.896。此结果证明该页可以被本地 OCR 识别，不代表全文或逐字准确率验收；也不等于上述 Docling 完整流程已完成。

## 合成案例

| 文件 | 用途 | 预期 |
| --- | --- | --- |
| `cases/plain-text.txt` | UTF-8 文本 | 可直接解析 |
| `cases/markdown-with-image.md` | Markdown 主文档 | 单独上传只包含正文；配套图片应通过下方 ZIP 一起上传 |
| `cases/html-with-image.html` | HTML 主文档 | 单独上传不能携带本机相对资源；配套图片通过 ZIP 上传 |
| `cases/assets/page-01.png`、`page-02.png` | 两张中文规则说明图片 | 可预览；OCR 在解析任务中执行 |
| `cases/spoken-video.mp4` | 8 秒、含合成英文语音的视频 | ffprobe 可读取且有音轨；转写准确率尚未验收 |
| `cases/md-assets-no-manifest.zip` | 一个 MD 主文档及其图片 | 不含 manifest；识别一个主文档、一个本地资源 |
| `cases/html-assets-no-manifest.zip` | 一个 HTML 主文档及其图片 | 不含 manifest；表格和图片随主文档预览 |
| `cases/md-video-assets-no-manifest.zip` | 一个 MD 主文档引用一段视频 | 不含 manifest；视频作为主文档的资源，转写在任务中执行 |
| `cases/md-assets-outer-folder.zip` | 资料有一个统一的外层目录 | 外层目录归一化后按同样规则处理 |
| `cases/invalid/two-main-documents.zip` | 两个独立 MD 主文档 | 应拒绝，提示分别上传 |
| `cases/invalid/fake.pdf` | 文本伪装的 PDF | 应在文件校验阶段拒绝 |

视频语音由本机语音引擎合成，仅用于流程测试；不代表真实长视频的转写质量或容量结论。

## ZIP 的约定

普通资源包只需要以下结构，不需要用户编写 JSON：

```text
资料.zip
├── 正文.md                 # 或一个 index.html
└── assets/
    ├── page-01.png
    └── spoken-video.mp4
```

主文档按相对路径引用资源，例如 `![说明](assets/page-01.png)` 或 `<video src="assets/spoken-video.mp4"></video>`。其他 PDF、Word、视频资料分别上传，不放进此 ZIP 作为第二份主文档。

`manifest.json` 是机器可读清单，可用于旧包兼容或有歧义的显式描述；以上正常输入不需要它。页面只应展示一个主文档和其资源的整体预览，不应提供 ZIP 内独立资料切换器。

## 重新验证

在仓库根目录使用现有环境：

```powershell
D:/anaconda/envs/llm_model/python.exe tests/docs/verify_inputs.py
```

验证器只读文件，不上传、不创建知识记录、不运行完整 PDF OCR/视频转写，也不构建索引。当前环境的 PDF 结构检查使用 PyMuPDF。验证结果中 `expected_rejection` 表示反例被正确拒绝，或者真实旧版 Word 命中了当前已知的格式支持缺口。
