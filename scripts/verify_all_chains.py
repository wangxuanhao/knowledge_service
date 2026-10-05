# -*- coding: utf-8 -*-
"""一键跑通所有「能力链路」真机复验 —— 分能力链路测试的**唯一汇总入口**。

为什么要有这一份：每一条链路各自有 `verify_*.py`（各自持有那条链路的验收口径、自建/自删
验证项目）。但散着 6 个脚本、记不住先跑哪个；这里把它们串起来一次跑完，最后出一张总表。

本脚本不重新发明任何验收口径 —— 口径仍由各 `verify_*.py` 持有，这里只负责：
  1) 先探健康检查（服务没起就别一串都白跑）；
  2) 按链路顺序逐个跑，统一传 `--url`；
  3) 汇总每个链路的 PASS / FAIL，任一失败则整体非零退出。

跑法：
    python scripts/verify_all_chains.py                  # 默认 http://127.0.0.1:8100
    python scripts/verify_all_chains.py --url http://127.0.0.1:8100
    python scripts/verify_all_chains.py --only reclassify   # 只跑某一条（按中文名/脚本名匹配）

安全口径：默认一律用各脚本的**只读/自清理**模式（不进带 --write 的链路），
跑完真库不留下验证项目。要额外写库验证，另用各脚本自己的 --write / --keep。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent

# (链路名, 脚本名, 追加参数). 顺序 = 业务链路顺序（本体生命周期 → 属性治理 → 消费）。
# 说明：原「本体生命周期 · 审核台收件箱」(verify_workbench_inbox.py) 守的是**已退役**的
# 「本体审核台」页（P0 移除页签、P1 并成单画布）。审核能力没消失、换了组织方式：
# 收件箱三格 → 单画布底栏「校验并审核」+ 右栏候选逐条收下。故由 verify_ontology_model.py 取代。
SKIP_EXIT = 3   # 子脚本用 exit 3 表达"本轮条件不满足、没东西可验"（见 run_one 说明）

CHAINS = [
    ("本体生命周期 · 阶段门禁",        "verify_ontology_gate.py",     []),
    ("本体生命周期 · 单画布写路径",    "verify_ontology_model.py",    []),
    ("本体生命周期 · 停用与影响面",    "verify_ontology_retire.py",   []),
    ("本体生命周期 · 标注/结构计量",   "verify_ontology_version.py",  []),
    # 写入链路会**真调 LLM**（各 ~2–4 分钟），所以排在最后，失败也不影响前面纯逻辑链路。
    ("写入 · 上传→抽取→入库",          "verify_ingest_end_to_end.py", []),
    ("写入 · 待审候选受理决策",        "verify_review_decision.py",   []),
    ("属性治理 · 结构待定",            "verify_structure_pending.py", ["--no-browser"]),
    ("消费 · 知识台账（台账⇄图谱）",   "verify_knowledge_ledger.py",  []),
    ("本体生命周期 · 受控重分类",      "verify_reclassify.py",        []),
    ("运行层 · 项目与运行（四页合并）", "verify_runtime_page.py",      []),
    # 认证是横切能力：谁进来、能读能写。它不进"链路"故事线，但每次改动路由/守卫都该跑一遍。
    ("横切 · 登录与权限（RBAC）",       "verify_auth_rbac.py",         []),
]


def health_ok(url: str) -> bool:
    """探健康检查：服务没起，就在跑每条链路前给出明确提示，而不是一串超时报错。"""
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def kill_edge() -> None:
    """每条链路前清一次残留的无头 Edge。

    为什么必须做：无头 Edge 偶尔不会随 `context.close()` 立刻退出，下一条链路启动时
    会撞上残留实例（表现为 launch 卡死或直接报错、exit != 0），而**单独跑这条却是绿的** ——
    这正是最难查的那种"假失败"。串联运行才是常态，所以在汇总入口统一收口。
    """
    if os.name == 'nt':
        subprocess.run(['powershell', '-Command',
                        'Stop-Process -Name msedge -Force -ErrorAction SilentlyContinue'],
                       capture_output=True)


def run_one(name: str, script: str, extra: list[str], url: str) -> bool | None:
    """返回 True=通过 / False=失败 / None=跳过（exit 3）。

    为什么要有"跳过"这一档：带 LLM 的链路（如 `verify_review_decision.py`）会遇到
    "本轮抽取没产出待审候选"这种**条件不满足**的情况 —— 它既不是功能坏了，也不能算通过。
    用 exit 3 表达"本轮没东西可验"，汇总时单列，避免把环境波动读成功能回归。
    """
    path = SCRIPTS_DIR / script
    if not path.exists():
        print(f"[漏] {name}: 找不到 {script}")
        return False
    print(f"\n{'=' * 64}\n▶ {name}  ({script})\n{'=' * 64}", flush=True)
    kill_edge()
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(path), "--url", url, *extra],
        cwd=str(SCRIPTS_DIR.parent),
    )
    elapsed = time.monotonic() - started
    verdict = True if result.returncode == 0 else (None if result.returncode == SKIP_EXIT else False)
    label = {True: "✔ PASS", False: "✘ FAIL", None: "⚠ SKIP"}[verdict]
    print(f"{label}  {name}  ({elapsed:.1f}s, exit={result.returncode})", flush=True)
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser(description="一键跑通所有能力链路真机复验")
    parser.add_argument("--url", default="http://127.0.0.1:8100", help="服务地址")
    parser.add_argument("--only", default=None, help="只跑名称/脚本名包含该字符串的那条链路")
    args = parser.parse_args()

    if not health_ok(args.url):
        print(f"✘ 服务没起（{args.url}/api/health 不通）。先起服务："
              f"python -u -m knowledge_service --port 8100")
        return 2

    selected = [c for c in CHAINS if not args.only or args.only in c[0] or args.only in c[1]]
    if not selected:
        print(f"✘ --only={args.only!r} 没匹配到任何链路；可选：")
        for name, script, _ in CHAINS:
            print(f"    {name}  ({script})")
        return 2

    results: list[tuple[str, bool | None]] = []
    for name, script, extra in selected:
        results.append((name, run_one(name, script, extra, args.url)))

    print(f"\n{'=' * 64}\n汇总（{len(results)} 条链路）\n{'=' * 64}")
    mark = {True: '✔', False: '✘', None: '⚠'}
    for name, verdict in results:
        print(f"  {mark[verdict]}  {name}")
    failed = [name for name, verdict in results if verdict is False]
    skipped = [name for name, verdict in results if verdict is None]
    summary = f"\n通过 {sum(1 for _, v in results if v is True)} / {len(results)}"
    if skipped:
        summary += f"；跳过 {len(skipped)}（条件不满足，非失败）：{', '.join(skipped)}"
    summary += f"；失败：{', '.join(failed)}" if failed else "；全部通过 ✔"
    print(summary)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())