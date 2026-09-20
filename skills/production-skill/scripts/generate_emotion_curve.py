#!/usr/bin/env python3
"""Plot user-supplied story beats; no fixed count or automatic project data.

CLI: python generate_emotion_curve.py output.png --input beats.json
JSON: {"scope": "前90秒", "beats": [{"label": "宣判", "score": 8}]}
Explicit example only: python generate_emotion_curve.py example.png --demo
Requires matplotlib for rendering; validation and --help work without it.
"""
import argparse
import json
import math
import os
from numbers import Real
from pathlib import Path
import textwrap


def validate_beats(beats):
    if not isinstance(beats, (list, tuple)) or not beats:
        raise ValueError('请提供至少一个实际剧情节拍；不会自动补入示例数据。')
    result = []
    for i, beat in enumerate(beats, 1):
        if isinstance(beat, dict):
            name, score = beat.get('label'), beat.get('score')
        elif isinstance(beat, (list, tuple)) and len(beat) == 2:
            name, score = beat
        else:
            raise ValueError(f'第{i}项须为label/score对象或名称与分值二元组。')
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f'第{i}项缺少节拍名称。')
        if isinstance(score, bool) or not isinstance(score, Real) or not math.isfinite(score) or not 0 <= score <= 10:
            raise ValueError(f'第{i}项分值须为0至10之间的有限数值。')
        result.append((name.strip(), float(score)))
    return result


def load_project(path):
    data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or not isinstance(data.get('scope'), str) or not data['scope'].strip():
        raise ValueError('输入JSON须明确scope分析范围，例如第一集、前90秒或指定场次。')
    return data['scope'].strip(), validate_beats(data.get('beats'))


def _setup_cjk_font(matplotlib):
    from matplotlib import font_manager
    candidates = [
        os.path.join(os.environ.get('WINDIR', 'C:/Windows'), 'Fonts', 'msyh.ttc'),
        os.path.join(os.environ.get('WINDIR', 'C:/Windows'), 'Fonts', 'simhei.ttf'),
        '/System/Library/Fonts/Hiragino Sans GB.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
    ]
    for path in candidates:
        if os.path.isfile(path):
            try:
                font_manager.fontManager.addfont(path)
                matplotlib.rcParams['font.family'] = font_manager.FontProperties(fname=path).get_name()
                matplotlib.rcParams['axes.unicode_minus'] = False
                return
            except (OSError, RuntimeError):
                continue
    print('提示：未找到预置中文字体，请检查生成图的中文字形。')


def plot_emotion_curve(beats=None, output_png='emotion_beat_curve.png', *, scope=None, demo=False):
    beats = validate_beats(beats)
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError('绘图前须明确scope分析范围，不自动把片段标成全剧。')
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise RuntimeError('绘图需要matplotlib，请在运行此脚本的Python环境安装后重试。') from exc
    _setup_cjk_font(matplotlib)
    count = len(beats)
    fig, ax = plt.subplots(figsize=(max(8, min(24, count * 1.25)), 5.5), dpi=150)
    try:
        x = list(range(1, count + 1))
        ax.plot(x, [b[1] for b in beats], marker='o', color='#C23B22', linewidth=2)
        for i, (_, score) in enumerate(beats, 1):
            ax.annotate(f'{score:g}', (i, score), xytext=(0, 9), textcoords='offset points', ha='center')
        labels = [f'{i}. '+ '\n'.join(textwrap.wrap(name, width=10)) for i, (name, _) in enumerate(beats, 1)]
        ax.set_xticks(x, labels, rotation=35 if count > 10 else 0, ha='right' if count > 10 else 'center')
        ax.set_xlim(0.5, count + 0.5)
        ax.set_ylim(0, 11)
        prefix = '示例数据 · 非项目分析｜' if demo else ''
        title = f'{prefix}{scope.strip()}｜{count}个剧情节拍'
        ax.set_title(title, pad=18)
        ax.set_xlabel('按剧情先后排列的节点（不是镜头数或SEG数量）')
        ax.set_ylabel('主观戏剧张力（0–10，仅辅助分析）')
        ax.grid(True, linestyle='--', alpha=0.35)
        fig.tight_layout()
        fig.savefig(output_png, metadata={'Title': title})
    finally:
        plt.close(fig)
    print(f'[+] 已绘制{count}个节拍：{output_png}')
    return Path(output_png)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', nargs='?', default='emotion_beat_curve.png')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--input', type=Path, help='含scope和实际beats的JSON文件')
    group.add_argument('--demo', action='store_true', help='显式绘制示例，图中标注非项目分析')
    args = parser.parse_args(argv)
    try:
        if args.demo:
            scope = '虚构片段演示'
            beats = [('发现异常', 3), ('试探', 5), ('暂时缓和', 4), ('真相揭示', 8), ('作出选择', 6)]
        else:
            scope, beats = load_project(args.input)
        plot_emotion_curve(beats, args.output, scope=scope, demo=args.demo)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
