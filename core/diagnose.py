# -*- coding: utf-8 -*-
"""错误诊断中枢：把任意异常翻译成「出了什么事 / 去哪改 / 改完怎么接着跑」。

写文案的规矩（很重要，改这个文件时请照做）：
  · 说人话。不写「熔断」「鉴权」「上游」「schema」「轮询」这类词，
    读的人可能第一次接触这些概念，看不懂就等于没提示。
  · 「去哪改」要能照着点：写页面上真实存在的按钮名、真实的文件夹名。
  · 「怎么改」是有先后顺序的动作，一步一句，不是并列的建议。
  · 「怎么接着跑」必须回答「我改完了，然后呢」，并说清会不会重复花钱。

另外两条机制性的要求：
  1. 失败记录写到文件里（07_检查与记录/failures.json），关掉程序也不丢
  2. 每条都标明还能不能接着跑（resumable）、影响范围多大（scope）
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Optional

from .store import LOCK, read_json, write_json

# ---------------------------------------------------------------- 错误目录
# title 出了什么事  why 为什么会这样  where 去哪改  fix 怎么改  resume 改完怎么接着跑
# level: error=没做出来  warn=东西做出来了但可能不对，不挡后面的流程
CATALOG = {
    "QUOTA_EXHAUSTED": {
        "title": "这家服务商的账户没钱了",
        "why": "服务商说你的余额或套餐额度不够了。程序已经自己停下来，"
               "后面还没做的任务一个都没发出去，不会继续扣钱。",
        "where": "先去服务商网站充值，再回「设置 → 服务商」看一眼 key 没被改过",
        "fix": ["打开这家服务商的网站，登录进去看余额还剩多少",
                "充值；不想充的话，去「生产」页把这批任务改成用另一家来做",
                "如果用的是别人分给你的 key，可能是这个 key 自己的额度用完了，换一个 key"],
        "resume": "充完钱回「生产」页，点这一行的「开始」。"
                  "已经做好的会自动跳过，只补没做完的，不会重复花钱。",
        "resumable": True, "scope": "batch", "level": "error",
    },
    "AUTH_INVALID": {
        "title": "API Key 不对，服务商不认",
        "why": "服务商拒收了你的 key。可能是填错了、过期了，也可能这个 key 是另一家的。"
               "这批任务已经全部停下。",
        "where": "设置 → 服务商 → 这一家的「API Key」输入框",
        "fix": ["把 key 重新复制粘贴一遍，注意前后别带空格、换行或引号",
                "确认这个 key 是「Base URL」填的那家网站发给你的——不同家的 key 不通用",
                "还不行就去服务商网站重新生成一个，粘过来保存",
                "保存后点「运行自检」，正常的话这家会显示出有多少个模型能用"],
        "resume": "自检能看到模型数量了，就回「生产」页重新点「开始」",
        "resumable": True, "scope": "batch", "level": "error",
    },
    "ACCOUNT_BANNED": {
        "title": "账号用不了，或者这个模型暂时没货",
        "why": "服务商那边说你的账号有问题；也可能是这个模型现在没有可用的线路。",
        "where": "设置 → 服务商；或者「生产」页这一行的模型下拉框",
        "fix": ["找服务商的客服问一下账号状态",
                "换一个模型试试——同一家往往接了好几条线路，这条不通换一条就好",
                "都不行就整体换一家服务商"],
        "resume": "换好之后回「生产」页点「开始」",
        "resumable": True, "scope": "batch", "level": "error",
    },
    "RATE_LIMITED": {
        "title": "发得太快，被服务商拦了",
        "why": "短时间里发的请求太多，超过了这家允许的速度。"
               "程序已经自动等了一会儿又重试过几次，还是没过去。",
        "where": "设置 → 「同时最多跑几个」那一块",
        "fix": ["把这家服务商的上限调小，比如从 6 改成 2",
                "「总上限（所有项目合计）」也跟着调小一点",
                "「生产」页这一行的「并发」改成 1 或 2",
                "然后等几分钟再开始——这种限制一般过几分钟自己就解除了"],
        "resume": "调小之后点「开始」，只会补没做完的那些",
        "resumable": True, "scope": "task", "level": "error",
    },
    "CONTENT_REJECTED": {
        "title": "提示词被平台的内容审核挡下了",
        "why": "这段提示词里写了平台不允许的内容。只有这一条失败，其它的照常在跑。",
        "where": "这一条对应的提示词文件，在项目文件夹的 03_提示词/ 里面",
        "fix": ["打开失败这一条对应的提示词文件（上面「影响范围」里写了是哪一条）",
                "按三步改，哪一步过了就停："
                "① 先只把露骨的词换成中性说法；"
                "② 还不过，就把动作过程整段删掉，只写「动作要开始的样子」和「动作已经结束的样子」，中间直接切；"
                "③ 再不过，就一点动作都不写，改成写造成的结果和旁人的反应",
                "有一条底线不能破：谁做的、对谁做的、造成了什么后果、前后因果——"
                "这四样必须原样保留，只许改「怎么描述」，不许改「发生了什么」",
                "改完保存文件就行，程序直接读硬盘上的文件，不用重新导入"],
        "resume": "改完保存，回「生产」页点「开始」，只会重跑这一条，别的不动",
        "resumable": True, "scope": "task", "level": "error",
    },
    "PROMPT_INVALID": {
        "title": "这次发过去的内容，服务商不收",
        "why": "可能是提示词太长，也可能是尺寸、时长、画面比例填了这个模型不支持的值。",
        "where": "对应的提示词文件；或者「生产」页上的模型和参数",
        "fix": ["看看提示词是不是太长了——有些模型对字数有上限",
                "看看画面比例、时长、分辨率是不是这个模型支持的"
                "（「设置」页每家服务商下面都写了支持哪些）",
                "换一个模型试试"],
        "resume": "改完点「开始」，补这一条",
        "resumable": True, "scope": "task", "level": "error",
    },
    "MODEL_NOT_FOUND": {
        "title": "找不到这个模型",
        "why": "这家服务商没有这个模型，或者你的账号还没开通它。",
        "where": "「生产」页 → 这一行的「模型」下拉框",
        "fix": ["去「设置」页点「运行自检」，能看到这家实际有哪些模型",
                "从查出来的清单里挑一个换上",
                "下拉框里的是程序内置的默认清单，可能已经过时了——以自检查出来的为准"],
        "resume": "换完模型点「开始」",
        "resumable": True, "scope": "batch", "level": "error",
    },
    "REF_MISSING": {
        "title": "要当参考的那张图还没生成",
        "why": "这一步要拿前面做好的资产图或故事板当参考，但那个文件现在不在。"
               "可能是还没做，也可能是被删了。",
        "where": "「生产」页的执行顺序",
        "fix": ["按顺序来：先做资产图，再做故事板，最后做视频",
                "去「产物」页看看缺的是哪一张，把那一步先补上",
                "如果是不小心删掉了，把对应那一步重跑一遍就有了"],
        "resume": "前面的补齐之后，回来点「开始」",
        "resumable": True, "scope": "task", "level": "error",
    },
    "EPISODE_REQUIRED": {
        "title": "得先选一集，这个环节是按集跑的",
        "why": "除了环节1，其它环节都是一集一集处理的。这部剧有好几集，"
               "程序不知道你要跑哪一集，所以停下来问你。",
        "where": "「流程」页最上面「当前操作的集」那个下拉框",
        "fix": ["在「流程」页顶部的下拉框里选一集，再点这个环节的「执行」",
                "想把所有集都跑一遍的话，点这个环节旁边的「全部集依次跑」——"
                "它会从第1集开始挨个跑，已经跑过的自动跳过",
                "如果下拉框里是空的，说明环节1 还没跑；先跑环节1，它会判断这部剧有几集"],
        "resume": "选好集（或点「全部集依次跑」）之后，重新点这个环节的「执行」",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "EPISODE_SPLIT_FAILED": {
        "title": "集没切出来，或者某一集是空的",
        "why": "环节1 要给出每一集正文的第一行原文，程序拿它去剧本里定位、切分。"
               "现在有的行在剧本里找不到，所以那几集切不出内容来。"
               "常见原因：模型没照原文抄，自己改写或翻译了。",
        "where": "「流程」页最上面的「这部剧有几集」，那里列出了具体是哪几集有问题",
        "fix": ["先看「这部剧有几集」里的黄色提示，它写了具体哪一集、什么原因",
                "重跑一次环节1——同一个模型换一次通常就好了",
                "还不行就换个更强的模型再跑环节1（设置 → 分析引擎）",
                "剧本里如果集与集之间完全没有可辨认的分隔（连标题都没有），"
                "那就手动把剧本拆成一集一个文件，在「项目」页分别上传"],
        "resume": "重跑环节1，切出来之后再往下跑",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "REF_URL_ONLY": {
        "title": "这个模型只收公网图片链接，本机图没传上去",
        "why": "有些接口的参考素材必须是能公开访问的 https 链接，不接受把图片本身发过去。"
               "程序会自动把本机的资产图/故事板传上去换成链接，但现在没传成功——"
               "要么还没配上传用的对象存储，要么上传本身失败了。",
        "where": "设置 → 参考图上传；或者「生产」页这一行的「模型」",
        "fix": ["最省事：换一个能直接吃本地图的模型（报错里列了同一家可用的那几个），"
                "或者整行换一家服务商——鹤、灵感鸭都能直接收本地图",
                "想用这个模型：去「设置 → 参考图上传」填一个 S3 兼容的对象存储，"
                "R2 / 阿里云 OSS / 腾讯云 COS / MinIO 都是这一套",
                "填完记得先装依赖：pip install boto3，然后重启程序",
                "已经配了还失败的，看下面原始报错——常见是桶不是公开可读、"
                "或者「公开访问域名」没填导致拼出来的链接打不开"],
        "resume": "配好上传、或换完模型，回「生产」页点「开始」，只会重跑没做成的",
        "resumable": True, "scope": "batch", "level": "error",
    },
    "MODEL_NEEDS_REF": {
        "title": "这个模型必须给参考图，但这一条一张都没有",
        "why": "有些模型只能做图生视频，不能凭一句话从零生成，所以没有参考图就直接拒了。",
        "where": "先看环节6「段落资产绑定」的产物；或者「生产」页这一行的模型",
        "fix": ["去「流程」页看环节6 的结果，这一段是不是真的没绑上任何参考图",
                "正常流程里视频是拿故事板当参考的——确认环节9 的故事板出来了没",
                "换一个能纯文字生成的模型（同一家一般都有）",
                "如果这一段本来就打算纯文字生成，那就必须换模型，这个模型做不到"],
        "resume": "补齐参考图或换完模型，点「开始」",
        "resumable": True, "scope": "task", "level": "error",
    },
    "PREREQ_MISSING": {
        "title": "前面的环节还没跑完",
        "why": "这个环节要用前面环节做出来的文件，但那些文件还不存在。",
        "where": "「流程」页",
        "fix": ["环节有先后顺序，从小号往大号一个一个跑",
                "具体缺哪个环节，上面的报错里已经写了名字"],
        "resume": "把缺的环节跑完，再回来跑这一个",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "LLM_SCHEMA_FAIL": {
        "title": "拆剧本的模型没按格式回答",
        "why": "程序要求模型按固定格式回答，好让后面的步骤能读。它连着几次都没照做。"
               "一般是这个模型能力不太够，或者一次喂给它的剧本太长了。",
        "where": "设置 → 分析引擎",
        "fix": ["换一个更强的模型（比如 claude-sonnet-5、claude-opus-4-8、gpt-5.5）",
                "剧本特别长的话，先拆成一集一集分开处理",
                "老是失败的话，看一眼日志里模型到底回了什么——"
                "有时候是内容被审核挡了，它回的其实是一句拒绝的话"],
        "resume": "换完模型回「流程」页，重跑这个环节",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "LLM_TRUNCATED": {
        "title": "拆剧本的模型话没说完就被截断了",
        "why": "模型的输出有个长度上限，这次要写的内容超过了上限，被硬切断在中间，"
               "所以后面的步骤读不出完整结果。整部剧的解析输出最长——"
               "全剧人物、场景、道具、伏笔，再加每一集的边界，很容易超。",
        "where": "设置 → 分析引擎 → 「单次输出上限 tokens」",
        "fix": ["把「单次输出上限」调大，比如 16000 改成 32000 或 64000",
                "改完保存，回「流程」页重跑这个环节",
                "调到很大还截断的话，说明这部剧一次装不下 —— "
                "把剧本按集拆成几个文件，在「项目」页分别建项目跑",
                "注意：这一条不会自动重试。同一个提示词重试必然同样被截断，"
                "只是把钱花三倍，所以程序直接停下来等你调参数"],
        "resume": "调大上限后回「流程」页重跑这个环节；已经做好的环节不受影响",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "SEG_COUNT_OFF": {
        "title": "这一集的段数和环节1 定的对不上",
        "why": "每集切几段是环节1 看完全篇后按剧情事件数定的，环节2 只该照着划。"
               "对不上就意味着这一集的成片时长和别的集不一致 —— "
               "40 集里有几集 3 分钟、有几集 1 分钟，交付时会很难看。"
               "段落表本身是可用的，所以流程没停，但这条得处理。",
        "where": "「产物 → 按段看」核对段数；或「流程」页重跑环节2",
        "fix": ["先看这一集的正文是不是本来就装不下那么多段 —— "
                "环节2 的 segments_note 里可能写了原因",
                "确实是环节1 定错了（比如把两集并成一集）→ 改集边界，重跑环节1",
                "只是模型没听话 → 重跑环节2（删掉这一集的 s2_segments.json 再点开始）",
                "接受现状也行：成片能出，只是这一集时长和别的集不一样"],
        "resume": "重跑环节2 只影响这一集；后面的环节会跟着新段落表重做",
        "resumable": True, "scope": "stage", "level": "warn",
    },
    "SEG_PARTIAL": {
        "title": "这一集有几段没做成，其余的都好了",
        "why": "环节7、环节8 是一段一次调用的，所以一段失败只影响那一段。"
               "做成的都已经存盘，不用重做。上面那条 target 带段号的记录才是真正的原因，"
               "先看那条。",
        "where": "看同一批里带段号的那条失败记录（比如 EP01-SEG07）",
        "fix": ["先照那条带段号的记录处理 —— 常见是空回复（降并发）或内容被拒（改剧本那段）",
                "然后回「一键跑到底」再点一次「开始」：**只会补没做成的那几段**，"
                "做好的一段都不重跑",
                "剩下的段照样往下走：没分镜的那段不会编提示词、不会出故事板和视频，"
                "其余段一路到成片都正常"],
        "resume": "再点一次「开始」，只补失败的那几段",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "LLM_EMPTY": {
        "title": "拆剧本的模型什么都没回",
        "why": "模型返回了一片空白，而且已经自动重试过几次还是空的。"
               "最常见的原因是同时开的活太多、被对方限流了 —— 很多家限流时不报错，"
               "只回一个空回复。其次才是剧本内容被拒，或者一次给的字太多。",
        "where": "「一键跑到底」的三个分析并发数；或者剧本内容本身",
        "fix": ["**先把「分析·总上限」调小**（比如 6 → 3），这是最常见的原因；"
                "「分析·集并发」也一起降到 2",
                "只有这一两集空、别的集都正常 → 基本可以确定是限流，不是内容问题",
                "所有集都空 → 看看剧本里是不是有大段敏感内容，导致整篇被拒；"
                "或者换个模型试一次",
                "剧本很长的话，把「分析这几集」填上，一次少跑几集"],
        "resume": "回「流程」页重跑这个环节",
        "resumable": True, "scope": "stage", "level": "error",
    },
    "NETWORK": {
        "title": "连不上服务商",
        "why": "请求没能发出去，或者发到一半断了。程序已经自动重试过几次。",
        "where": "设置 → 服务商 → Base URL / 代理",
        "fix": ["先看本机能不能正常上网；用了 VPN 的话检查一下 VPN",
                "确认 Base URL 没写错，复制到浏览器里能打开",
                "如果这家要走代理才能访问，在这家的配置里把 proxy 填上"],
        "resume": "网络恢复之后点「开始」，只补没做完的",
        "resumable": True, "scope": "batch", "level": "error",
    },
    "TIMEOUT": {
        "title": "等太久了还没出结果",
        "why": "程序一直在问服务商「好了没」，问到设定的最长等待时间还是没结果。"
               "这个任务可能还在服务商那边排队。",
        "where": "设置 → 分析引擎 / 服务商 的超时设置",
        "fix": ["视频高峰期确实会很慢，先等几分钟再重试一次",
                "如果经常超时，把等待上限调大（config.json 里的 video_poll_timeout）",
                "或者换一个出得快一些的模型"],
        "resume": "点「开始」重试这一条",
        "resumable": True, "scope": "task", "level": "error",
    },
    "DISK": {
        "title": "文件存不下来，或者读不出来",
        "why": "往硬盘写文件或者读文件的时候出错了。",
        "where": "项目文件夹",
        "fix": ["看看硬盘还有没有剩余空间",
                "看看这个文件是不是正被别的程序占着——最常见的是你正在播放器里预览它",
                "如果项目放在 OneDrive、坚果云这类同步盘里，同步过程也会占用文件，先暂停同步"],
        "resume": "解决之后点「开始」",
        "resumable": True, "scope": "task", "level": "error",
    },
    "GHOST_REF": {
        "title": "参考图里有资产表里根本没有的东西",
        "why": "环节8 排参考图顺序时写了一个资产表里查不到的 ID，最常见的是把"
               "「本段故事板」自己写了进去 —— 那是环节10 视频的约定"
               "（视频以故事板为参考），不该出现在故事板的参考图里。"
               "这种 ID 指不到任何文件。以前程序会悄悄跳过、照样出图，"
               "出来的脸和场景全靠模型自己编，还标成做好了；现在到这一步会停下。",
        "where": "「任务明细」里这一段的「参考图」那一栏，缺的会显示成「缺」",
        "fix": ["先看「任务明细」确认少的是哪几张",
                "只影响一两段：直接在页面上改这一段的故事板提示词，"
                "把参考图角色映射改成真实资产 ID",
                "整集都是这个毛病：重跑环节8（模板已经写明 reference_order "
                "只能填真实资产 ID、禁止填本段故事板）",
                "如果那个资产本来就该出图却还没出，先去把它出出来"],
        "resume": "改好之后在「生产」页点「补失败」，只重做这几条。",
        "resumable": True, "scope": "task", "level": "warn",
    },
    "WRONG_RATIO": {
        "title": "做出来的画面比例，跟你要的不一样",
        "why": "文件已经出来了，服务商也没报错，但程序量了一下实际尺寸，"
               "跟你选的比例对不上。多半是这家没按你要求的比例出，用了它自己的默认值"
               "（最常见的是：你要竖屏，它给了横屏）。",
        "where": "「生产」页这一行的「画面比例」；或者干脆换一家服务商",
        "fix": ["先去「产物」页点开看一眼，确认是不是真的躺倒了",
                "确实不对的话：在「产物」页把这个文件删掉，换一家服务商重做这一条",
                "如果这一批全都是这个毛病，说明这家不认程序发过去的比例设置，整批换一家",
                "如果你看了觉得这样也能用，那就不用管这条，不影响后面拼接"],
        "resume": "想重做的话，得先在「产物」页把这个文件删掉再点「开始」——"
                  "文件还在的话，程序会认为这条已经做好了，直接跳过。",
        "resumable": True, "scope": "task", "level": "warn",
    },
    "UNKNOWN": {
        "title": "没见过的错误",
        "why": "这条报错没对上任何已知的类型。具体内容看下面的「原始报错」。",
        "where": "—",
        "fix": ["先看下面的「原始报错」，服务商返回的错误里一般会直接写原因",
                "去「设置」页点「运行自检」，先排除掉配置填错的可能",
                "如果看不懂，把「原始报错」整段复制出来——"
                "这类没认出来的报错需要补进识别规则里，下次才能给出准确指引"],
        "resume": "查清楚之后点「开始」重试",
        "resumable": True, "scope": "task", "level": "error",
    },
}

# 原始报错 → 错误码（按顺序匹配，先命中先用）
_PATTERNS = [
    ("QUOTA_EXHAUSTED", r"insufficient|quota|余额|额度|欠费|balance|billing|payment|credit|arrears"),
    ("AUTH_INVALID", r"invalid[_ ]api[_ ]key|incorrect api key|unauthorized|authentication|令牌|token 不正确|无效的?密钥"),
    ("ACCOUNT_BANNED", r"banned|封禁|禁用|账户异常|无可用渠道|no available channel|suspend"),
    ("MODEL_NOT_FOUND", r"model.*not (found|exist)|不存在的?模型|unsupported model|无此模型"),
    ("RATE_LIMITED", r"429|rate limit|too many request|限流|请求过于频繁"),
    ("CONTENT_REJECTED", r"content policy|safety|violat|prohibited|sensitive|审核|违规|敏感"),
    ("PROMPT_INVALID", r"prompt too long|too long|invalid (prompt|param|size|request)|参数错误|不支持的?(尺寸|时长|比例)"),
    # 这两条要排在 REF_MISSING 前面：都跟参考图有关，但原因和改法完全不同
    ("REF_URL_ONLY", r"只收公网|只收 ?HTTPS|must be a (public )?url|不接受本地图片"
                     r"|传不上去|没能传上去|上传到对象存储失败|还没配对象存储"
                     r"|对象存储没填|先装 boto3"),
    ("MODEL_NEEDS_REF", r"必须给至少 ?\d* ?张参考图|必须提供 ?\d* ?张参考图|need_image"),
    ("REF_MISSING", r"参考图文件不存在|固定故事板不存在|no such file|filenotfound"),
    # 这两条要排在 PREREQ_MISSING 前面：它们的文案里也带「请先跑」，
    # 但给的指引更具体（去哪个下拉框选集），别被通用的前置缺失兜走
    ("EPISODE_REQUIRED", r"得指定跑哪一集|没有 EP\d+ 这一集|还没切集"),
    ("EPISODE_SPLIT_FAILED", r"的正文是空的|一集都没切出来|找不到这一行"),
    ("PREREQ_MISSING", r"缺少前置产物|请先跑"),
    # 「有 N 段没做成」是聚合结论，真正的原因在带段号的那条记录里。
    # 排在前面：它的文案里也带「失败」，会被后面的规则抢走。
    ("SEG_PARTIAL", r"段分镜失败|段编译失败|段没做成"),
    # 截断要排在 SCHEMA_FAIL 前面：它的表现也是 JSON 不完整，但修法完全不同
    ("LLM_TRUNCATED", r"被长度上限截断|finish_reason.{0,4}length|max_tokens"),
    ("LLM_SCHEMA_FAIL", r"JSON 输出校验失败|输出缺少必需字段|未找到可解析的 JSON"),
    ("LLM_EMPTY", r"回复内容为空|无 choices"),
    ("TIMEOUT", r"超时|timed? ?out"),
    ("NETWORK", r"网络错误|connection|dns|ssl|proxy|unreachable"),
    ("DISK", r"permission denied|being used by another process|no space|disk"),
]


def code_of(message: str, status: int = 0) -> str:
    low = (message or "").lower()
    if status in (401,):
        return "AUTH_INVALID"
    if status in (402,):
        return "QUOTA_EXHAUSTED"
    if status in (403,):
        return "ACCOUNT_BANNED"
    for code, pat in _PATTERNS:
        if re.search(pat, low, re.I):
            return code
    if status == 429:
        return "RATE_LIMITED"
    if status in (400, 422):
        return "PROMPT_INVALID"
    return "UNKNOWN"


def _entry(code: str, *, stage: str, target: str, provider: str, model: str,
           status: int, raw: str, extra_fix: Optional[list] = None) -> dict:
    c = CATALOG.get(code, CATALOG["UNKNOWN"])
    return {
        "code": code,
        "title": c["title"],
        "why": c["why"],
        "where": c["where"],
        "fix": list(c["fix"]) + list(extra_fix or []),
        "resume": c["resume"],
        "resumable": c["resumable"],
        "scope": c["scope"],
        "level": c.get("level", "error"),
        "stage": stage,
        "target": target,
        "provider": provider,
        "model": model,
        "status": status,
        "raw": raw[:800],
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def build(exc: Any, *, stage: str = "", target: str = "", provider: str = "",
          model: str = "", extra_fix: Optional[list] = None) -> dict:
    """异常 → 结构化诊断。这是给人看的那一份。

    服务商可以在抛异常时挂 `exc.extra_fix = [...]`，把只有它自己知道的
    「该查什么」补进「怎么改」里。有些家的 400 只回一句笼统话，
    通用错误码给不出具体指引，只能靠各家自己逐条自检。
    """
    msg = str(exc)
    status = getattr(exc, "status", 0) or 0
    extra = list(extra_fix or []) + list(getattr(exc, "extra_fix", None) or [])
    return _entry(code_of(msg, status), stage=stage, target=target, provider=provider,
                  model=model, status=status, raw=msg, extra_fix=extra)


def warn(code: str, raw: str, *, stage: str = "", target: str = "", provider: str = "",
         model: str = "", extra_fix: Optional[list] = None) -> dict:
    """提醒级：东西做出来了，但可能不对。不算失败，不挡后面的流程。"""
    return _entry(code, stage=stage, target=target, provider=provider, model=model,
                  status=0, raw=raw, extra_fix=extra_fix)


# 换一家服务商能解决的问题 —— 都是「这家不行」而不是「这个活有问题」
FAILOVER_CODES = {
    "QUOTA_EXHAUSTED",     # 这家没钱了，别家有
    "AUTH_INVALID",         # 这家 key 不对
    "ACCOUNT_BANNED",       # 这家账号/线路不可用
    "MODEL_NOT_FOUND",      # 这家没这个模型
    "RATE_LIMITED",         # 这家在限流，换一家能继续
    "NETWORK",              # 这家连不上
    "TIMEOUT",              # 这家太慢
    "REF_URL_ONLY",         # 这家只收链接而我们没配存储 → 换能吃本地图的
}

# 换家也一样的 —— 得改内容或改流程，自动切换只会把同一个错误重复一遍
NO_FAILOVER_CODES = {
    "CONTENT_REJECTED",     # 提示词本身要改。换家碰运气有可能过，但那是在赌，
                            # 而且各家审核尺度不同会导致同一部剧风格不一致
    "PROMPT_INVALID",       # 参数不合法
    "REF_MISSING",          # 参考图还没生成，是流程顺序问题
    "PREREQ_MISSING", "EPISODE_REQUIRED", "EPISODE_SPLIT_FAILED",
    "LLM_SCHEMA_FAIL", "LLM_EMPTY", "SEG_PARTIAL", "SEG_COUNT_OFF",
    "DISK", "WRONG_RATIO",
    "GHOST_REF",            # 换一家也一样缺那张图，是活儿本身的问题
}


def should_failover(diag: Optional[dict]) -> bool:
    """这个错误值不值得换下一家再试。"""
    if not diag:
        return False
    code = diag.get("code", "")
    if code in NO_FAILOVER_CODES:
        return False
    # 没见过的错误也给一次换家的机会：多半是某家自己的毛病
    return code in FAILOVER_CODES or code == "UNKNOWN"


def one_line(d: dict) -> str:
    """给日志和任务卡用的一行摘要。不带错误码——那是给我排查用的，不是给用户看的。"""
    where = d.get("where") or ""
    tail = f"｜去哪改：{where}" if where and where != "—" else ""
    return f"{d['title']}{tail}"


# ---------------------------------------------------------------- 落盘
# 内存里的 job 重启就没了；失败必须落盘，否则「停了之后怎么继续」无从谈起。

def failures_path(project_root: str) -> str:
    return os.path.join(project_root, "07_检查与记录", "failures.json")


def record(project_root: str, diag: dict) -> None:
    """记一条失败。同 stage+target 的旧记录会被覆盖（只保留最新一次）。

    读-改-写整段加锁：多线程同时失败时必须串行，否则会互相覆盖。
    """
    if not project_root:
        return
    with LOCK:
        p = failures_path(project_root)
        items = read_json(p, []) or []
        key = (diag.get("stage"), diag.get("target"))
        items = [x for x in items if (x.get("stage"), x.get("target")) != key]
        items.append(diag)
        write_json(p, items[-300:])


def clear(project_root: str, stage: str = "", target: str = "") -> int:
    """任务成功后清掉对应的失败记录。返回清掉几条。"""
    if not project_root:
        return 0
    with LOCK:
        p = failures_path(project_root)
        if not os.path.isfile(p):
            return 0
        items = read_json(p, []) or []
        before = len(items)
        if not stage and not target:
            items = []
        else:
            items = [x for x in items
                     if not ((not stage or x.get("stage") == stage)
                             and (not target or x.get("target") == target))]
        if len(items) != before:
            write_json(p, items)
        return before - len(items)


def load(project_root: str) -> list:
    return read_json(failures_path(project_root), []) or []


def summary(project_root: str) -> dict:
    """按错误码聚合，给「续跑面板」用。同一个毛病影响 20 条时，只显示一张卡。

    报错先排、提醒后排——真正卡住流程的要先看到。
    """
    items = load(project_root)
    by_code: dict = {}
    for it in items:
        g = by_code.setdefault(it["code"], {
            "code": it["code"], "title": it["title"], "why": it.get("why", ""),
            "where": it["where"], "fix": it["fix"], "resume": it["resume"],
            "scope": it["scope"], "level": it.get("level", "error"),
            "raw": it.get("raw", ""), "targets": [],
        })
        g["targets"].append(it.get("target") or it.get("stage"))
    groups = sorted(by_code.values(), key=lambda g: (g["level"] != "error", g["code"]))
    errors = sum(len(g["targets"]) for g in groups if g["level"] == "error")
    return {"total": len(items), "errors": errors, "warns": len(items) - errors,
            "groups": groups}
