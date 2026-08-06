# -*- coding: utf-8 -*-
"""程序目录和数据目录分开。

为什么要分：程序是从仓库拉/拷过来的，换机器、升级版本时整个目录会被覆盖。
配置（各家 API key、R2 凭证、优先级链、计价表）和产物（几十 GB 的图和视频）
绝不能跟着一起被覆盖掉。

  程序目录  script-to-video-studio/        ← 覆盖它 = 更新程序，随便覆盖
  数据目录  config.json + 默认 projects/   ← 只属于这台机器，程序更新碰不到

数据目录按这个顺序定，先命中的算：
  1. 启动参数 --data D:\\stv-data
  2. 环境变量 STV_DATA_DIR
  3. 程序目录里已经有 config.json —— 老装法，原地不动别乱搬
     （只是每次启动会提醒一句：这个位置覆盖程序就丢）
  4. %LOCALAPPDATA%\\script-to-video-studio（Windows）/ ~/.script-to-video-studio

项目目录（projects/）另外还能在设置页单独改，因为它体积大，常要放到别的盘。
"""

from __future__ import annotations

import os
import sys

APP_NAME = "script-to-video-studio"

# 打包成 exe 之后有两个「目录」，混用会出各种找不到文件的怪事：
#   PROGRAM_DIR  exe 自己在哪 —— 用来判断「配置是不是放在程序旁边」（绿色版）
#   BUNDLE_DIR   打包进去的只读资源（web/、prompts/）解压到哪 ——
#                onefile 模式下是每次运行新建的临时目录，别往里写东西
# 没打包时两者相同，都是仓库根目录。
FROZEN = bool(getattr(sys, "frozen", False))
if FROZEN:
    PROGRAM_DIR = os.path.dirname(os.path.abspath(sys.executable))
    BUNDLE_DIR = getattr(sys, "_MEIPASS", PROGRAM_DIR)
else:
    PROGRAM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    BUNDLE_DIR = PROGRAM_DIR


def res(*parts: str) -> str:
    """打包进去的只读资源（页面、提示词模板）。只读，别往里写。"""
    return os.path.join(BUNDLE_DIR, *parts)

# 启动时由 run.py 用 --data 填进来（命令行优先于环境变量）
_forced: dict = {"data": ""}


def set_data_dir(path: str) -> None:
    _forced["data"] = os.path.abspath(os.path.expanduser(path)) if path else ""


def legacy_config() -> str:
    """老装法的配置位置：程序目录里。"""
    return os.path.join(PROGRAM_DIR, "config.json")


def default_data_dir() -> str:
    base = (os.environ.get("LOCALAPPDATA")
            or os.environ.get("XDG_CONFIG_HOME")
            or os.path.expanduser("~"))
    name = APP_NAME if base != os.path.expanduser("~") else "." + APP_NAME
    return os.path.join(base, name)


def data_dir() -> str:
    if _forced["data"]:
        return _forced["data"]
    env = os.environ.get("STV_DATA_DIR", "").strip()
    if env:
        return os.path.abspath(os.path.expanduser(env))
    # 老装法：配置已经在程序目录里，就继续用那儿 —— 悄悄换位置会让人以为配置丢了
    if os.path.isfile(legacy_config()):
        return PROGRAM_DIR
    return default_data_dir()


def config_path() -> str:
    return os.path.join(data_dir(), "config.json")


def default_projects_dir() -> str:
    """默认产物目录。

    源码装法且数据目录 = 程序目录时沿用历史位置 程序目录/../projects
    （那时程序目录是仓库根，产物放仓库里会被 git 和「覆盖更新」波及）。
    exe 绿色版和其它情况都放数据目录下的 projects/。
    """
    d = data_dir()
    if not FROZEN and os.path.abspath(d) == os.path.abspath(PROGRAM_DIR):
        return os.path.abspath(os.path.join(PROGRAM_DIR, "..", "projects"))
    return os.path.join(d, "projects")


def at_program_dir() -> bool:
    """数据目录是不是就在程序旁边。**是不是有风险是另一回事**，见 config_at_risk。"""
    return os.path.abspath(data_dir()) == os.path.abspath(PROGRAM_DIR)


def config_at_risk() -> bool:
    """配置会不会被「更新程序」这个动作弄丢。

    源码装法：更新 = 覆盖整个程序目录，配置在里面就会丢 → 有风险。
    exe：更新 = 换掉那一个 exe 文件，旁边的 config.json 不受影响 ——
    这就是绿色版的正常用法，不该报警。
    """
    return (not FROZEN) and at_program_dir()


def snapshot() -> dict:
    """给启动横幅和设置页用。"""
    cfg = config_path()
    return {
        "frozen": FROZEN,
        "bundle_dir": BUNDLE_DIR if FROZEN else "",
        "program_dir": PROGRAM_DIR,
        "data_dir": data_dir(),
        "config_path": cfg,
        "config_exists": os.path.isfile(cfg),
        "default_projects_dir": default_projects_dir(),
        "config_at_risk": config_at_risk(),
        "source": ("--data 参数" if _forced["data"]
                   else "STV_DATA_DIR 环境变量" if os.environ.get("STV_DATA_DIR", "").strip()
                   else ("exe 旁边（绿色版，配置已在这里）" if FROZEN
                         else "程序目录（老装法，配置已在这里）") if at_program_dir()
                   else "系统用户数据目录（默认）"),
    }


def migrate_config(dest_dir: str = "") -> dict:
    """把程序目录里的 config.json 搬到数据目录。

    只复制 + 把原件改名成 config.json.已搬走，**不删任何东西** ——
    万一搬错地方，原件还在。
    """
    src = legacy_config()
    if not os.path.isfile(src):
        raise FileNotFoundError(f"程序目录里没有 config.json：{src}")
    dst_dir = os.path.abspath(os.path.expanduser(dest_dir)) if dest_dir \
        else default_data_dir()
    if os.path.abspath(dst_dir) == os.path.abspath(PROGRAM_DIR):
        raise ValueError("目标就是程序目录，搬了等于没搬 —— 换一个程序目录之外的路径")
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, "config.json")
    if os.path.isfile(dst):
        raise FileExistsError(f"目标已经有 config.json 了，先自己看一眼哪份是要的：{dst}")
    import shutil
    shutil.copy2(src, dst)
    kept = src + ".已搬走"
    if os.path.isfile(kept):
        os.remove(kept)
    os.replace(src, kept)
    set_data_dir(dst_dir)
    return {"from": src, "to": dst, "old_kept_as": kept, "data_dir": dst_dir}
