"""受控重分类（C2）· 迁移入口的**静态契约**（不跑浏览器，只钉住"入口在哪、约束在不在"）。

为什么单独一个文件：这条契约钉的是"迁移入口属于本体建模层"这件事本身。
老的迁移面板随旧审核台被删之后，后端三个接口一直健在
（GET /reclassify、POST /reclassify/preview、POST /reclassify），但**没有任何界面调用它们**——
功能是断的。台账那一页只读计划、只显示"还有 N 条挂在旧版本上"，迁移动作归本体版本，
所以入口必须在建模层。这条契约就是防止它再被删掉或被搬回台账。

行为层面（预演门禁、勾选作废、撤销通道）由 test_reclassify_migration_ui.py 用真浏览器钉住。
"""
from pathlib import Path

import re

WEB = Path(__file__).resolve().parents[2] / 'knowledge_service' / 'web'


def read(name):
    return (WEB / name).read_text(encoding='utf-8')


def test_reclassify_migration_assets_are_loaded_with_cache_versions():
    """两个新资源必须被 index.html 加载且带 ?v=（否则浏览器用缓存旧文件，"改了没生效"）。"""
    html = read('index.html')
    assert re.search(r'/assets/ontology-model-reclassify\.js\?v=[\w.-]+', html), \
        'ontology-model-reclassify.js 没有被加载或缺少缓存版本号'
    assert re.search(r'/assets/ontology-model-reclassify\.css\?v=[\w.-]+', html), \
        'ontology-model-reclassify.css 没有被加载或缺少缓存版本号'


def test_reclassify_migration_styles_are_scoped_to_the_model_tab():
    """样式必须作用域在 .ontology-model 之下：随页签隐藏，不污染别的页。"""
    css = read('ontology-model-reclassify.css')
    assert '.ontology-model .om-rc' in css, '迁移样式没有作用域在 .ontology-model 下'


def test_reclassify_migration_uses_the_three_real_endpoints():
    """看（只读）/ 预演（干跑）/ 迁移（写）+ 整批撤销，一条都不能少。"""
    js = read('ontology-model-reclassify.js')
    assert "'/reclassify'" in js, '缺少只读计划接口（GET /reclassify）'
    assert "'/reclassify/preview'" in js, '缺少预演（干跑）接口'
    assert "'/operations/'" in js and "'/undo'" in js, '缺少整批撤销通道'


def test_reclassify_migration_states_the_three_hard_constraints():
    """三条硬约束必须写在界面上（不是只活在后端注释里）——用户看得到才算数。"""
    js = read('ontology-model-reclassify.js')
    assert '默认不跑' in js, '没写"默认不跑"'
    assert '新版本' in js and '不原地改' in js, '没写"不原地改（每条产生新版本）"'
    assert '撤销' in js, '没写"可整批撤销"'


def test_reclassify_migration_never_offers_a_one_click_migrate_all():
    """硬约束①的界面表现：没勾选就不许预演/迁移；「没有去处」的组不可勾。"""
    js = read('ontology-model-reclassify.js')
    assert 'state.selected.size' in js and 'disabled = !hasSelection' in js, \
        '确认迁移没有"必须先勾选"的门禁'
    assert 'check.disabled = !group.migratable' in js, '「没有去处」的组居然可勾选'
    assert '不会被迁移' in js, '「没有去处」的组缺少"不会被迁移"的说明'


def test_reclassify_migration_refuses_to_invent_numbers_when_unreadable():
    """读不到服务端判定时不许编数字（本仓库的通用铁律：读不到就说读不到）。"""
    js = read('ontology-model-reclassify.js')
    assert '不显示任何数字' in js, '读不到计划时在编数字'
