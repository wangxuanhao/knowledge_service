"""文档契约：README 里指向的文档必须真实存在，且操作手册必须在索引里。

防止的情况和前端静态契约一样：改了文档/挪了文件，索引还指向旧路径，
读者点进去 404 —— 这种"看起来没问题"的坏链最费时间。
"""
from pathlib import Path

import re

ROOT = Path(__file__).resolve().parents[2]
README = ROOT / 'README.md'
MANUAL = ROOT / 'docs' / '2026-10-01-本体工作台操作手册.md'


def test_readme_links_point_to_existing_files():
    text = README.read_text(encoding='utf-8')
    # 只校验仓库内的相对链接，外链（http/https、mailto、锚点）交给读者。
    targets = re.findall(r'\]\((?!https?://|mailto:|#)([^)\s]+)\)', text)
    assert targets, 'README 里一个仓库内链接都没有，检查是不是链接格式变了'
    missing = [target for target in targets if not (ROOT / target).exists()]
    assert not missing, f'README 指向了不存在的文件：{missing}'


def test_workbench_manual_is_indexed_and_keeps_key_sections():
    """操作手册必须被 README 索引，并保留用户真正会查的那几节。"""
    readme = README.read_text(encoding='utf-8')
    assert 'docs/2026-10-01-本体工作台操作手册.md' in readme

    manual = MANUAL.read_text(encoding='utf-8')
    # A2-c：审核台不再是"四站流水线"，章节名随之改（设计归编辑台、发布是收下的结果）。
    for section in ('## 0. 三个页面各做什么、不做什么', '## 1. 审核台的两个视图：收件箱 + 逐项收下',
                    '## 2. 改结构：去「本体编辑台」', '## 3. 逐项收下（审核台的主场）',
                    '## 4. 校验与发布（发布是"收下"的结果）',
                    '## 5. 本体编辑台', '## 6. 本体档案',
                    '## 7. 常见问题', '## 8. 自测与验收命令', '## 9. 本轮修复对照'):
        assert section in manual, f'操作手册缺少章节：{section}'
    # 三页各自的"不做什么"必须写在手册里——用户反复追问的就是边界。
    for page in ('本体审核台', '本体编辑台', '本体档案'):
        assert page in manual, f'操作手册没有提到：{page}'
    # "提交并自审"跳过了他人复核，这个边界必须写明，不能只当一个按钮。
    assert '提交并自审' in manual and '跳过他人复核' in manual
    # 一键批准的边界必须写清楚"覆盖什么、不覆盖什么"——这是用户反复追问的点。
    assert '覆盖' in manual and '不覆盖' in manual
