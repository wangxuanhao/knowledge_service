#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把各页 CSS 的硬编码浅色值迁到深色令牌（一次性迁移，只跑一次）。

为什么需要它：`style.css` 早就定义了 `--ks-*` 令牌，但各页 CSS 一个都没引用，
底色/边框/文字全是硬编码的浅色十六进制（#fff / #dce5df / #14312a…）。逐个人工改
既慢又漏，所以按"属性上下文"批量替换——同一个色值在不同属性下含义不同，必须分开：

  · background*  → 换底色（#fff 白底 → 面/纸/下沉底）
  · border*/outline → 换描边
  · color/fill/stroke → 换字色（注意：color:#fff 在深色主题下是"对的"，保持不动）

替换后各页只引用令牌，颜色由 theme.css 一处决定。
dry-run 默认只打印统计与样例，`--apply` 才落盘。
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

WEB = Path(r'D:\workspace\knowledge_service\knowledge_service\web')
TARGETS = ['workspace.css', 'runtime.css', 'projects.css']

# 背景：白/近白 → 面；浅绿灰 → 下沉；浅黄 → 琥珀弱底；浅红 → 危险弱底
BG = {
    '#fff': 'var(--ks-surface)', '#ffffff': 'var(--ks-surface)',
    'white': 'var(--ks-surface)',               # 颜色关键字（.inspector-rail 用的就是它）
    'whitesmoke': 'var(--ks-sunken)',
    'snow': 'var(--ks-surface)',
    'ivory': 'var(--ks-surface)',
    'azure': 'var(--ks-surface)',
    'honeydew': 'var(--ks-surface)',
    'mintcream': 'var(--ks-surface)',
    'ghostwhite': 'var(--ks-surface)',
    '#fbfdfb': 'var(--ks-surface)', '#fbfcfa': 'var(--ks-paper)',
    '#fafcf8': 'var(--ks-surface)', '#f9fcfa': 'var(--ks-surface)',
    '#fbfdfa': 'var(--ks-surface)', '#f8fbf8': 'var(--ks-surface)',
    '#fafcf9': 'var(--ks-surface)', '#f8faf7': 'var(--ks-surface)',
    '#f7faf6': 'var(--ks-surface)', '#fafcfb': 'var(--ks-surface)',
    '#f7faf7': 'var(--ks-sunken)', '#f6faf7': 'var(--ks-sunken)',
    '#edf2ee': 'var(--ks-sunken)', '#e5ebe5': 'var(--ks-sunken)',
    '#e2eae4': 'var(--ks-sunken)', '#edf3ef': 'var(--ks-sunken)',
    '#f0f7f3': 'var(--ks-sunken)', '#ecf4ee': 'var(--ks-accent-soft)',
    '#e9f4ee': 'var(--ks-accent-soft)',
    '#fffaf0': 'var(--ks-amber-soft)', '#fdf9f1': 'var(--ks-amber-soft)',
    '#fff0d0': 'var(--ks-amber-soft)', '#fdf6e7': 'var(--ks-amber-soft)',
    '#fdf0ed': 'var(--ks-danger-soft)',
    '#e8f5eb': 'var(--ks-ok-soft)',
}

# 描边
BORDER = {
    '#dce5df': 'var(--ks-line)', '#dae4de': 'var(--ks-line)',
    '#e2eae4': 'var(--ks-line)', '#d7e3db': 'var(--ks-line-strong)',
    '#dce8e0': 'var(--ks-line-strong)', '#d7e2d4': 'var(--ks-line-strong)',
    '#dce5d9': 'var(--ks-line-strong)',
    '#bfd8cb': 'var(--ks-accent-line)', '#cfe4d8': 'var(--ks-accent-line)',
    '#e7cfa4': 'var(--ks-amber-line)', '#ecd9b4': 'var(--ks-amber-line)',
    '#eecfc7': 'var(--ks-danger-line)', '#e6c9c0': 'var(--ks-danger-line)',
}

# 字色：墨绿强调 → accent；墨绿正文 → ink；灰绿 → muted；琥珀 → amber；砖红 → danger
COLOR = {
    # 正文/标题墨绿 → 正文色
    '#14312a': 'var(--ks-ink)', '#203c35': 'var(--ks-ink)',
    '#264f3e': 'var(--ks-ink)', '#294d3e': 'var(--ks-ink)',
    '#244f3d': 'var(--ks-ink)', '#1d3b31': 'var(--ks-ink)',
    # 强调墨绿 → 青
    '#28634d': 'var(--ks-accent)', '#216952': 'var(--ks-accent)',
    '#245d47': 'var(--ks-accent)', '#15624b': 'var(--ks-accent)',
    '#34674f': 'var(--ks-accent)', '#19654e': 'var(--ks-accent)',
    # 灰绿次要 → muted / faint
    '#6a8274': 'var(--ks-muted)', '#829087': 'var(--ks-muted)',
    '#6e8175': 'var(--ks-muted)', '#718078': 'var(--ks-muted)',
    '#6b7f75': 'var(--ks-muted)', '#7b8e84': 'var(--ks-muted)',
    '#617268': 'var(--ks-muted)', '#4c6357': 'var(--ks-muted)',
    '#6b8177': 'var(--ks-muted)', '#6f8177': 'var(--ks-muted)',
    '#789086': 'var(--ks-faint)', '#8ba095': 'var(--ks-faint)',
    '#839489': 'var(--ks-faint)',
    # 琥珀 / 砖红
    '#8a5a12': 'var(--ks-amber)', '#a96a0d': 'var(--ks-amber)',
    '#d99a40': 'var(--ks-amber)',
    '#a04b2d': 'var(--ks-danger)', '#9d3a2b': 'var(--ks-danger)',
}

HEX = re.compile(r'#[0-9a-fA-F]{3,6}\b')
# 十六进制 + 颜色关键字（white 这类关键字以前漏掉了，.inspector-rail 就是死在这里）
VALUE = re.compile(
    r'#[0-9a-fA-F]{3,6}\b'
    r'|\b(?:white|whitesmoke|snow|ivory|azure|honeydew|mintcream|ghostwhite|seashell|linen)\b',
    re.I)
# 抓 "属性名: 值（到 ; 或 } 为止）"
DECL = re.compile(r'([a-zA-Z-]+)\s*:\s*([^;{}]+)([;}])')


def classify(prop: str) -> dict[str, str] | None:
    p = prop.lower()
    if p.startswith('background'):
        return BG
    if p.startswith('border') or p in ('outline', 'outline-color', 'box-shadow'):
        return BORDER
    if p in ('color', 'fill', 'stroke'):
        return COLOR
    return None


def fallback(prop: str, raw: str) -> str | None:
    """未登记的颜色兜底归类。

    浅色值太碎（#eef6ef / #f1f6f1 / #fff5e8 …同一色系几十个近似值），逐个登记不可维护。
    这里按亮度+色相规则化：只有**背景/描边**上的**浅色**才替换（浅色→深色方向必然正确），
    深色一律不动——避免把已经正确的深色或文字色改坏。
    """
    m = re.fullmatch(r'#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})', raw)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    if lum < 0.55:                          # 深色：不动
        return None
    p = prop.lower()
    if p.startswith('background'):
        if r - g > 14 and r - b > 20:       # 偏红浅底 → 危险弱底
            return 'var(--ks-danger-soft)'
        if r - b > 26 and g - b > 10:       # 偏黄浅底 → 待决策弱底
            return 'var(--ks-amber-soft)'
        return 'var(--ks-surface)' if lum > 0.965 else 'var(--ks-sunken)'
    if p.startswith('border') or p.startswith('outline'):
        if r - b > 26 and g - b > 10:
            return 'var(--ks-amber-line)'
        return 'var(--ks-line)'
    return None


def convert(text: str, hits: Counter) -> str:
    def one_decl(m: re.Match) -> str:
        prop, value, tail = m.group(1), m.group(2), m.group(3)
        table = classify(prop)
        if not table:
            return m.group(0)

        def swap(mm: re.Match) -> str:
            raw = mm.group(0)
            key = raw.lower()
            if len(key) == 4 and key.startswith('#'):    # #abc -> #aabbcc
                key = '#' + ''.join(c * 2 for c in key[1:])
            if key in table:
                hits[f'{prop}:{raw}'] += 1
                return table[key]
            fb = fallback(prop, raw)                     # 未登记 → 规则兜底
            if fb:
                hits[f'{prop}:{raw}~'] += 1
                return fb
            return raw

        return f'{prop}:{VALUE.sub(swap, value)}{tail}'

    return DECL.sub(one_decl, text)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='真正落盘（默认 dry-run）')
    args = ap.parse_args()

    total = Counter()
    changed_files = 0
    for name in TARGETS:
        path = WEB / name
        src = path.read_text(encoding='utf-8')
        hits: Counter = Counter()
        out = convert(src, hits)
        total.update(hits)
        if out != src:
            changed_files += 1
            if args.apply:
                path.write_text(out, encoding='utf-8')
            print(f'{"已改" if args.apply else "将改"} {name}: {sum(hits.values())} 处')
        else:
            print(f'     {name}: 无需改')

    print(f'\n合计 {sum(total.values())} 处，涉及 {changed_files} 个文件')
    print('替换明细（属性:原值 -> 次数）：')
    for k, v in total.most_common(40):
        print(f'  {k}  ×{v}')
    if not args.apply:
        print('\n这是 dry-run。确认无误后加 --apply 落盘。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
