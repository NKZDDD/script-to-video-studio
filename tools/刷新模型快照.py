# -*- coding: utf-8 -*-
"""用本机配置实拉全部服务商的模型清单，输出**不含凭据**的打包快照。

从 respect_batch/tools/refresh_personal_models.py 照搬过来（两边同一套口径）。

为什么打包要带一份快照：目标机器第一次启动时还没配 Key，拉不到清单 ——
没有快照的话，页面上的模型下拉只有代码里写死的那份，而那份**换得比什么都快**。
这几天实拉的账：无限画布 24 小时内从 5 个变 11 个、我头一天写进去的 3 个已经
下线；鹤一次下线 9 个（其中 `sd2-720p` 是它的默认模型和代码兜底 ——
「没改过模型就点开始」必然失败）；`sd2-1080p` 前一天说没有、后一天又回来了。

快照里**只有模型字段**（id / type / ratios / durations 这些），
凭据一个都不进 —— fetch() 那边就把响应里别的东西剔掉了。

用法：
    py tools/刷新模型快照.py --config config.json --output build/model-snapshot.json
"""
import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import model_catalog, providers          # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", type=Path, action="append", default=[],
                    help="本机配置（可给多份，后面的覆盖前面的）")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

    cfg = {}
    for p in args.config:
        if not p.is_file():
            print(f"跳过（不存在）：{p}")
            continue
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        cfg.update((data.get("config", data) or {}).get("providers", {}) or {})
    if not cfg:
        print("一份配置都没读到 —— 没有 Key 就拉不到清单，快照会是空的。")

    def pull(pid):
        r = model_catalog.fetch(pid, cfg.get(pid, {}))
        n = len(r.get("models") or [])
        print(f"  {'✓' if r['ok'] else '✗'} {pid:<13}"
              f"{(str(n) + ' 个') if r['ok'] else r.get('msg', '')}")
        return pid, r

    print("实拉各家的 /v1/models（只读，不花钱）：")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = dict(pool.map(pull, list(providers.REGISTRY)))

    ok = {k: v for k, v in results.items() if v.get("ok")}
    # **只写拉到的。** 失败的不写空壳进去 —— 写了的话
    # `capabilities()` 会拿一个 ok=False 的记录当快照用，
    # 而它什么都没有，等于把清单清空。
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(ok, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    # 凭据不许进快照 —— 这一条要当场验，不是靠 fetch 那边「应该」剔干净了
    blob = args.output.read_text(encoding="utf-8")
    leaked = [pid for pid, pc in cfg.items()
              for v in pc.values()
              if isinstance(v, str) and len(v) > 12 and v in blob]
    if leaked:
        args.output.unlink()
        print(f"\n✗ 快照里出现了凭据（{'、'.join(sorted(set(leaked)))}）—— 已删除，不打包。")
        return 1
    print(f"\n写好 → {args.output}（{len(ok)}/{len(results)} 家拉到，"
          f"{sum(len(v.get('models') or []) for v in ok.values())} 个模型）")
    print("已核对：快照里没有任何凭据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
