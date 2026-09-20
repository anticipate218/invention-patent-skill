#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把「技术交底书」一键转成申请文件 Markdown 初稿（纯标准库）。

用法::

    python scripts/gen_draft.py --template > 交底书.md    # 先拿一份填空模板
    python scripts/gen_draft.py 交底书.md -o 申请文件.md   # 生成初稿
    python scripts/gen_draft.py 交底书.md --strict         # 连「需要复核」也当失败

输入是一份 Markdown 技术交底书（用 `##` 分节，节名见 `SECTION_ALIASES`）；
输出是本技能约定的申请文件草稿结构——`# 发明名称：…` + 说明书摘要 / 权利要求书 /
说明书（技术领域→背景技术→发明内容→附图说明→具体实施方式）/ 说明书附图，
也就是 `check_patent.py` 能直接自检的那种结构。

三条设计原则（与 `SKILL.md` 的「三条铁律」对齐）：

1. **不编造技术内容**。生成器只做搬运、编号、套措辞模板和标点归一化；
   交底书里没有的东西一律留成**显式缺口**（`（交底书未给出名称）`、
   `…（请补：…）`），并在报告里逐条列出，绝不静默补一个看起来像样的说法。
2. **生成即可自检**。生成完立刻把结果交给 `check_patent.run_checks`，
   把机械校验的发现原样打印出来；本脚本不另立一套合规规则。
3. **不掩盖缺口**。缺字段、附图标记没给名称、实施例没写——这些都会让退出码变成 1，
   除非显式加 `--allow-gaps` 表示"我知道有洞，先这么用"。

本脚本只处理**发明专利**。实用新型必须有附图、外观设计不写权利要求书，
两者的撰写范式与检查项都不同，不在本脚本的覆盖范围内。

参考:
    `references/paradigm.md`（通用生成范式）、`references/claims.md`（权利要求）、
    `references/specification.md`（说明书）、`references/format.md`（版式与摘要）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_patent  # noqa: E402  （同目录脚本，CI 的白名单允许同目录 import）

VERSION = "1.0.0"

# 交底书的 `##` 节名 → 内部键。左右两侧都可以在标题里加「（可选）」之类的尾巴。
SECTION_ALIASES: dict[str, str] = {
    "发明名称": "name",
    "名称": "name",
    "发明创造的名称": "name",
    "技术领域": "field",
    "所属技术领域": "field",
    "背景技术": "background",
    "现有技术": "background",
    "技术问题": "problem",
    "要解决的技术问题": "problem",
    "现有技术的缺陷": "problem",
    "独权前序": "preamble",
    "前序部分": "preamble",
    "必要技术特征": "features",
    "区别技术特征": "features",
    "技术特征": "features",
    "特征部分引导语": "lead",
    "引导语": "lead",
    "附加技术特征": "dependent",
    "从属权利要求": "dependent",
    "有益效果": "benefits",
    "效果": "benefits",
    "摘要": "abstract",
    "说明书摘要": "abstract",
    "附图": "figures",
    "附图清单": "figures",
    "附图标记": "signs",
    "附图标记说明": "signs",
    "实施例": "embodiment",
    "具体实施方式": "embodiment",
    "用途": "use",
    "应用领域": "use",
}

# 生成一份能通过机械自检的初稿，至少需要这些字段。缺一个就拒绝生成——
# 硬凑出来的申请文件比报错更危险。
REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
    ("name", "发明名称"),
    ("field", "技术领域"),
    ("problem", "技术问题"),
    ("preamble", "独权前序"),
    ("features", "必要技术特征"),
    ("benefits", "有益效果"),
)

FIELD_LABELS: dict[str, str] = {
    "name": "发明名称", "field": "技术领域", "background": "背景技术",
    "problem": "技术问题", "preamble": "独权前序", "features": "必要技术特征",
    "lead": "特征部分引导语", "dependent": "附加技术特征", "benefits": "有益效果",
    "abstract": "摘要", "figures": "附图", "signs": "附图标记",
    "embodiment": "实施例", "use": "用途",
}

# 名称和主题名称里要剥掉的前缀：从属权利要求的引用部分不写「一种」。
SUBJECT_PREFIX = re.compile(r"^(?:一种|一类|一款|一项|一组)")

# 独权前序里已经自带前序/特征分界时用这个判断。
RE_SPLIT_MARK = re.compile(r"其特征(?:在于|是)")

# 方法类发明默认用「包括以下步骤：」引导特征部分，其余用「包括：」。
RE_METHOD_NAME = re.compile(r"(方法|工艺|流程|制法|用法|用途|制备|合成|控制|检测|识别|"
                            r"预测|优化|处理|设计|测量|评价|筛选|构建|训练|调度)$")

# 附图标记：正文里写作「名称（数字）」，与 check_patent.RE_SIGN_INLINE 同源。
RE_SIGN = check_patent.RE_SIGN_INLINE

ABSTRACT_TARGET = 262      # 自动拼接摘要的目标长度：check_patent 在 270 字起提醒、300 字起不合规
CLOSING = ("以上所述仅为本发明的实施例，并非因此限制本发明的范围，"
           "凡在本发明的构思之内所作的修改，均属于本发明的保护范围。")

SELF_TEST_TOTAL = 48


# ---------------------------------------------------------------------------
# 交底书解析
# ---------------------------------------------------------------------------


def _canon_title(title: str) -> str:
    """把 `##` 标题规整成可查表的形式。

    参数:
        title: 标题原文，如 `技术领域（可选）`、``附图标记：`。

    返回:
        去掉尾部括号说明、去空白、去尾部冒号后的标题。

    算法:
        先去掉尾部冒号，再剥掉尾部成对括号（中英文都认），最后删掉所有空白字符。
        冒号要先去掉：`技术领域（可选）：` 的括号不在末尾，反过来就剥不掉。

    复杂度:
        O(len(title))。

    陷阱:
        括号只剥**尾部**的：`权利要求书（草案）` 会变成 `权利要求书`，
        但 `一种（改进的）装置` 这种标题里的括号不会被误剥——节名本来也不长这样。
    """
    t = title.strip()
    t = t.rstrip("：:")
    t = re.sub(r"\s*[（(][^（()）]*[)）]\s*$", "", t)
    t = re.sub(r"\s+", "", t)
    return t.rstrip("：:")


def _lookup_field(title: str) -> str | None:
    """按节名找内部键，支持前缀匹配。

    参数:
        title: `##` 标题原文。

    返回:
        内部键名；认不出来返回 None。

    算法:
        先精确查 `SECTION_ALIASES`；查不到就按别名长度从长到短做前缀匹配
        （`技术领域和背景` 这类写法可以容忍），最长优先以免「技术问题」被
        「技术特征」抢走。

    复杂度:
        O(别名数)；别名数是常数。
    """
    key = SECTION_ALIASES.get(_canon_title(title))
    if key:
        return key
    canon = _canon_title(title)
    for alias in sorted(SECTION_ALIASES, key=len, reverse=True):
        if canon.startswith(alias):
            return SECTION_ALIASES[alias]
    return None


def _split_h2(text: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """把交底书按 `##` 切成块。

    参数:
        text: 交底书全文。

    返回:
        `(前言行, [(标题, 正文行), ...])`；`###` 及更深标题留在正文行里。

    算法:
        逐行扫描，遇到 `^##\\s+…` 就开新块；其余行归当前块。

    复杂度:
        O(n)。

    陷阱:
        只认 `##` 两级。`#` 一级标题是文档大标题（前言），`###` 是节内小标题
        （`附加技术特征` 的每一个 `### 引用 N` 就靠这个），两者都不能当分节符。
    """
    head: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    title: str | None = None
    body: list[str] = []
    for raw in text.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*#*\s*$", raw)
        if m:
            if title is not None:
                blocks.append((title, body))
            title = m.group(1)
            body = []
            continue
        if title is None:
            head.append(raw)
        else:
            body.append(raw)
    if title is not None:
        blocks.append((title, body))
    return head, blocks


def _oneline(text: str) -> str:
    """把多行文本压成一行（中文软换行不产生空格）。"""
    return re.sub(r"\s*\n\s*", "", text).strip()


def _rstrip_punct(text: str) -> str:
    """去掉句末标点，便于重新拼接。"""
    return text.strip().rstrip("。．.；;，,、：:")


def _items(body: str) -> list[str]:
    """把一节正文拆成条目列表。

    参数:
        body: 小节正文（可能带 `-`/`1.` 列表前缀，也可能是整段散文）。

    返回:
        条目字符串列表；续行并入上一条。

    算法:
        优先认列表标记（`-`、`*`、`+`、`1.`、`1、`）；没有列表标记时把整段当一条，
        若这一条里含 `；` 或 `。` 再按句读切开——交底书里"一行写完所有特征"很常见。

    复杂度:
        O(len(body))。

    陷阱:
        续行直接拼接而不是加空格：中文列表项折行时加空格会在正文里留下多余空隙，
        而西文折行本来就该有空格——这里按中文优先，与 `make_docx._is_cjk_joinable`
        是同一套取舍。
    """
    out: list[str] = []
    cur: str | None = None
    for raw in body.split("\n"):
        line = raw.rstrip()
        m = re.match(r"^\s*(?:[-*+]|\d+\s*[.)、．])\s+(.*)$", line)
        if m:
            if cur is not None:
                out.append(cur)
            cur = m.group(1).strip()
        elif not line.strip():
            continue
        elif cur is not None:
            cur += line.strip()
        else:
            cur = line.strip()
    if cur is not None:
        out.append(cur)
    out = [x for x in out if x]
    if len(out) == 1 and re.search(r"[；;。]", out[0]):
        out = [p for p in re.split(r"[；;。]", out[0]) if p.strip()]
        out = [p.strip() for p in out]
    return out


def _clean_ref(text: str) -> str:
    """把「引用表达式」归一化成权利要求书里的标准写法。

    参数:
        text: 交底书里写的引用，如 `引用 1或2`、`根据权利要求 2-4`、`1和2`。

    返回:
        归一化后的表达式，如 `1 或 2`、`2 至 4`。

    算法:
        依次剥掉「引用/根据权利要求/权利要求」前缀和「所述…」尾巴，把区间连接号
        （`-`、`—`、`~` 等）统一成「至」，把并列连接词（`、`、`和`、`与`）统一成「或」，
        再规范数字与连接词之间的空格。

    复杂度:
        O(len(text))。

    陷阱:
        `1和2` 会被改成 `1 或 2`。这不是格式洁癖：细则第 25 条第 2 款要求多项从属
        **只能以择一方式**引用在前的权利要求，并列引用本身就不合规。改写会记进
        报告的「生成说明」里，不静默改。
    """
    t = text.strip().rstrip("：:")
    t = re.sub(r"^#{1,6}\s*", "", t)
    t = re.sub(r"^(?:引用|根据权利要求|权利要求)\s*", "", t)
    t = re.sub(r"所述.*$", "", t)
    t = re.sub(r"(?:中|的)\s*$", "", t)
    for dash in ("－", "—", "–", "~", "～", "-", "到"):
        t = t.replace(dash, "至")
    t = re.sub(r"[、，,]|和|与|及", "或", t)
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"(\d)\s*(或|至)\s*(\d)", r"\1 \2 \3", t)
    return t


def _dependent_blocks(body: str) -> tuple[list[tuple[str, str]], list[str]]:
    """解析「附加技术特征」一节。

    参数:
        body: 该节正文。

    返回:
        `([(引用表达式, 限定部分正文), ...], [无法归属的行, ...])`。

    算法:
        主形式是 `### 引用 1 或 2` + 后续段落；同时兼容单行列表写法
        `- 引用 1：正文`。其余落在任何 `###` 之前的非空行收进第二个返回值，
        由调用方报成缺口——**不能静默丢弃**。

    复杂度:
        O(len(body))。

    陷阱:
        返回的正文保留换行：从属权利要求正文里通常没有换行，但交底书里可能折行，
        后面统一用 `_oneline` 压平。
    """
    blocks: list[tuple[str, str]] = []
    stray: list[str] = []
    ref: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if ref is not None:
            blocks.append((ref, "\n".join(buf).strip()))

    for raw in body.split("\n"):
        line = raw.rstrip()
        m = re.match(r"^\s*#{3,6}\s*(.+?)\s*$", line)
        if m:
            flush()
            ref = _clean_ref(m.group(1))
            buf = []
            continue
        m2 = re.match(r"^\s*[-*+]?\s*(?:引用\s*)?"
                      r"((?:\d+\s*(?:或|和|与|及|、|,|，|至|~|～|-|—|–|到)\s*)*\d+)"
                      r"\s*[：:|｜]\s*(.+)$", line)
        if m2 and ref is None:
            blocks.append((_clean_ref(m2.group(1)), m2.group(2).strip()))
            continue
        if not line.strip():
            continue
        if ref is None:
            stray.append(line.strip())
        else:
            buf.append(line.strip())
    flush()
    return blocks, stray


def _figures(body: str) -> list[tuple[int, str]]:
    """解析「附图」一节，得到 `图号 → 图名`。

    参数:
        body: 该节正文。

    返回:
        按图号升序的 `[(图号, 图名), ...]`。

    算法:
        每行（以及行内 `；` 分隔的片段）认 `图 N [是/为/：] 图名`；
        一个都没认出来时，退化成"每个非空行按顺序就是图 1、图 2……"。

    复杂度:
        O(len(body))。

    陷阱:
        退化路径会把「图名」当成整行文本。交底书里如果只是随手列了几行说明，
        退化结果是可用的；但如果那一节写的是别的东西，生成结果就会跑偏——所以
        `--template` 里的写法要照抄。
    """
    out: list[tuple[int, str]] = []
    for chunk in re.split(r"[；;\n]", body):
        s = chunk.strip().lstrip("-*+ ").strip()
        if not s:
            continue
        m = re.match(r"^图\s*(\d+)\s*[是为：:、,，.]?\s*(.*)$", s)
        if m and m.group(2).strip():
            out.append((int(m.group(1)), m.group(2).strip()))
    if not out:
        lines = [ln.strip().lstrip("-*+ ").strip() for ln in body.split("\n")]
        lines = [ln for ln in lines if ln]
        out = [(i, ln) for i, ln in enumerate(lines, start=1)]
    return sorted(out, key=lambda x: x[0])


def _signs(body: str) -> list[tuple[str, str]]:
    """解析「附图标记」一节，得到 `标记 → 名称`。

    参数:
        body: 该节正文。

    返回:
        `[(标记, 名称), ...]`，标记形如 `1`、`12a`。

    算法:
        按 `；`、换行、`，`、`、` 切分，每段依次尝试三种写法：
        `编号—名称`（法定写法）、`编号 名称`、`名称 编号`、`编号名称`。

    复杂度:
        O(len(body))。

    陷阱:
        第二种写法（名称在前）是为了吃下 `壳体 1` 这种口语写法。它可能把
        `图 1` 误认成标记 `1` 名称 `图`——真发生了会体现在生成结果里，
        属于交底书写法问题而不是脚本的静默行为。
    """
    out: list[tuple[str, str]] = []
    for tok in re.split(r"[；;\n]", body):
        for piece in re.split(r"[，,、]", tok):
            s = piece.strip().lstrip("-*+ ").strip()
            if not s:
                continue
            m = re.match(r"^(\d+[a-zA-Z]?)\s*(?:[—\-－–:：|｜.]|\s)\s*(\S.*)$", s)
            if m:
                out.append((m.group(1), m.group(2).strip()))
                continue
            m = re.match(r"^(\S.*?)\s*[—\-－–:：|｜]?\s*(\d+[a-zA-Z]?)$", s)
            if m:
                out.append((m.group(2), m.group(1).strip()))
                continue
            m = re.match(r"^(\d+[a-zA-Z]?)(\D.*)$", s)
            if m:
                out.append((m.group(1), m.group(2).strip()))
    return out


def parse_disclosure(text: str) -> dict:
    """把交底书解析成结构化字典。

    参数:
        text: 交底书全文。

    返回:
        {
          "name"/"field"/"background"/"problem"/"preamble"/"lead"/"abstract"/
          "embodiment"/"use": 字符串（保留段落，注释已剥掉）,
          "features"/"benefits": [字符串, ...],
          "dependent": [(引用表达式, 正文), ...],
          "figures": [(图号, 图名), ...],
          "signs": [(标记, 名称), ...],
          "_seen": [识别到的内部键, ...],
          "_unknown": [没认出来的 `##` 标题, ...],
          "_stray": [附加技术特征里无法归属的行, ...],
        }

    算法:
        先按 `##` 切块，再按节名查表；标量字段保留整段文本，列表字段按各自的
        小语法拆。所有 HTML 注释在最后统一剥掉——模板里的填写提示不属于正文，
        留着会把摘要撑过 300 字，也会让"填了没有"判断失真。

    复杂度:
        O(n)，n 为交底书字符数。

    陷阱:
        发明名称的兜底只用一级标题，且**跳过占位内容**：模板的
        `# 技术交底书：（一句话写清要申请的发明）` 不能被当成发明名称。
    """
    disc: dict = {k: "" for k in FIELD_LABELS if k not in ("features", "dependent",
                                                           "figures", "signs", "benefits")}
    disc.update({"features": [], "benefits": [], "dependent": [], "figures": [], "signs": [],
                 "_seen": [], "_unknown": [], "_stray": []})

    head, blocks = _split_h2(text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", ""))

    for title, body in blocks:
        key = _lookup_field(title)
        if key is None:
            disc["_unknown"].append(title.strip())
            continue
        if key not in disc["_seen"]:
            disc["_seen"].append(key)
        raw = "\n".join(body).strip()
        if key == "features" or key == "benefits":
            disc[key].extend(_items(raw))
        elif key == "dependent":
            deps, stray = _dependent_blocks(raw)
            disc["dependent"].extend(deps)
            disc["_stray"].extend(stray)
        elif key == "figures":
            disc["figures"].extend(_figures(raw))
        elif key == "signs":
            disc["signs"].extend(_signs(raw))
        else:
            disc[key] = (disc[key] + "\n\n" + raw).strip() if disc[key] else raw

    # 一级标题兜底当发明名称
    if not disc["name"]:
        for raw in head:
            m = re.match(r"^#\s+(.+?)\s*#*\s*$", raw)
            if not m:
                continue
            t = re.sub(r"^(?:技术交底书|交底书|交底材料)\s*[：:]\s*", "", m.group(1).strip())
            if t.startswith(("（", "(")) or not t:
                continue
            disc["name"] = t.strip()
            break

    # 剥注释 + 去空条目
    for key in FIELD_LABELS:
        if key in ("features", "benefits"):
            disc[key] = [x for x in (check_patent.strip_comments(v).strip()
                                     for v in disc[key]) if x]
        elif key == "dependent":
            disc[key] = [(r, check_patent.strip_comments(b).strip()) for r, b in disc[key]
                         if check_patent.strip_comments(b).strip()]
        elif key in ("figures", "signs"):
            continue
        else:
            disc[key] = check_patent.strip_comments(disc[key]).strip()
    disc["_stray"] = [check_patent.strip_comments(x).strip()
                      for x in disc["_stray"] if check_patent.strip_comments(x).strip()]
    return disc


# ---------------------------------------------------------------------------
# 初稿组装
# ---------------------------------------------------------------------------


def _subject_name(name: str) -> str:
    """从属权利要求引用部分用的主题名称（剥掉「一种」）。"""
    return SUBJECT_PREFIX.sub("", name).strip() or name


def _default_lead(name: str) -> str:
    """按名称猜特征部分引导语。"""
    return "包括以下步骤：" if RE_METHOD_NAME.search(_rstrip_punct(name)) else "包括："


def _clip(text: str, limit: int) -> str:
    """把文本截到 limit 字以内，尽量断在句读处。

    参数:
        text: 原文。
        limit: 上限字数。

    返回:
        截断后的文本（不在末尾补标点）。

    算法:
        超出时在窗口内从后往前找 `；，、。` 或空格，找到且位置不低于窗口一半就断在
        标点后；否则硬截。

    复杂度:
        O(len(text))。

    陷阱:
        「不低于窗口一半」这个下限是为了避免在第一个逗号处就断掉，把句子切成碎片；
        代价是长句可能被硬截，所以调用方（自动摘要）还会再兜一次长度。
    """
    text = text.strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    for sep in ("；", "，", "、", "。", " "):
        idx = window.rfind(sep)
        if idx >= limit // 2:
            return window[: idx + 1].strip()
    return window.strip()


def _sign_key(num: str) -> tuple[int, str]:
    """附图标记排序键：先按数字，再按字母后缀。"""
    m = re.match(r"(\d+)([a-zA-Z]?)", num)
    if not m:
        return (10 ** 9, num)
    return (int(m.group(1)), m.group(2))


def _figure_line(num: int, desc: str) -> str:
    """把图号 + 图名渲染成附图说明里的一行。"""
    d = re.sub(r"^(?:是|为|：|:)\s*", "", desc).strip()
    if not re.match(r"^(?:本发明|本实用新型)", d):
        d = "本发明实施例的" + d
    return "图 %d 是%s" % (num, d)


def _auto_abstract(disc: dict, name: str, field: str, scheme: str) -> str:
    """拼一段摘要（仅在交底书没给 `## 摘要` 时使用）。

    参数:
        disc: 解析结果。
        name: 发明名称。
        field: 技术领域（已去掉尾部的「技术领域」）。
        scheme: 独权正文去掉「其特征在于」后的文字。

    返回:
        摘要文字，不含「摘要附图」那一行。

    算法:
        固定句式「本发明公开了…，属于…技术领域。{针对现有技术中…的问题，}
        本发明采用如下技术方案：…。{有益效果首句}{本发明主要用于…。}」，
        逐段用 `_clip` 按剩余预算截断，先保内容再保长度。

    复杂度:
        O(len)。

    陷阱:
        这是**机械拼接**，中文不一定顺；所以生成报告里会明写「摘要系自动拼接」，
        建议在交底书里直接给一节 `## 摘要`。摘要超 300 字直接不合规，
        所以这里宁可截断也不放任。
    """
    head = "本发明公开了%s，属于%s技术领域。" % (name, field)
    prob = _rstrip_punct(_oneline(disc["problem"]))
    if re.match(r"^(?:现有技术|目前|当前|传统|已有|已有技术)", prob):
        mid = prob + "，"
    else:
        mid = "针对现有技术中%s的问题，" % _clip(prob, 70)
    lead = "本发明采用如下技术方案："
    ben = _rstrip_punct(_clip(_oneline(disc["benefits"][0]), 70)) if disc["benefits"] else ""
    use = _rstrip_punct(_clip(_oneline(disc["use"]), 40))
    tails = []
    tail_full = (ben + "。" if ben else "") + ("本发明主要用于%s。" % use if use else "")
    tails.append(tail_full)
    if ben:
        tails.append("本发明主要用于%s。" % use if use else "")
    if use:
        tails.append(ben + "。" if ben else "")
    tails.append("")

    body = _rstrip_punct(_oneline(scheme))
    for tail in tails:
        room = ABSTRACT_TARGET - len(head) - len(mid) - len(lead) - 1 - len(tail)
        if room < 20:
            continue
        out = head + mid + lead + _rstrip_punct(_clip(body, room)) + "。" + tail
        if len(out) <= check_patent.ABSTRACT_MAX:
            return out
    # 理论上到不了这里；真到了就保底砍中段，宁可短也不能超限。
    out = head + lead + _rstrip_punct(_clip(body, 80)) + "。"
    return out[: check_patent.ABSTRACT_MAX]


def build_draft(disc: dict) -> tuple[str, list[str], list[str]]:
    """把解析结果组装成申请文件草稿。

    参数:
        disc: `parse_disclosure` 的输出。

    返回:
        `(草稿全文, 缺口列表, 说明列表)`。缺口 = 交底书里没有、生成器拒绝编造的东西；
        说明 = 归一化动作（改标点、改引用写法、丢弃未使用的标记等）。

    算法:
        独权 = 前序 + 「，其特征在于，」+ 引导语 + 特征（`；` 连接）；
        从权 = 「N. 根据权利要求 X 所述的{主题名称}，其特征在于，{限定部分}。」；
        发明内容套三段固定句式；附图说明由 `附图` + 自动收集的 `附图标记说明` 组成；
        具体实施方式的正文来自交底书，缺失时退化成技术方案原文并记缺口。

    复杂度:
        O(交底书长度)。

    陷阱:
        生成器**不检查**新颖性、创造性、保护范围，也不判断技术特征写得对不对；
        它只保证结构、编号、标点和法定句式。`SKILL.md` 里那条「机械检查 ≠ 合规」
        在这里同样成立——过检的初稿仍须由人复核技术内容。
    """
    gaps: list[str] = []
    notes: list[str] = []

    name = _oneline(disc["name"])
    if re.search(r"(实用新型|外观设计)", name):
        gaps.append("发明名称里出现「%s」——本技能只覆盖发明专利，"
                    "实用新型必须有附图、外观设计不写权利要求书，请改用对应的范式"
                    % ("实用新型" if "实用新型" in name else "外观设计"))

    # ---- 独立权利要求 ----
    pre = _rstrip_punct(_oneline(disc["preamble"]))
    feats = [_rstrip_punct(x).replace("。", "；") for x in disc["features"]]
    feats = [f for f in feats if f]
    if not feats:
        gaps.append("「必要技术特征」一节是空的——独立权利要求的特征部分无法生成")
    joined = "；".join(feats)
    lead = _oneline(disc["lead"])
    if RE_SPLIT_MARK.search(pre):
        tail_char = pre[-1:]
        if tail_char in "：:，,、；;":
            claim1_source = pre + joined
        elif re.search(r"(?:包括|包含|具有|由|为|是)$", pre):
            claim1_source = pre + "：" + joined
        else:
            claim1_source = pre + "，" + joined
    else:
        if not lead:
            lead = _default_lead(name)
        claim1_source = pre + "，其特征在于，" + lead + joined
    claim1 = claim1_source + "。"

    # ---- 从属权利要求 ----
    subject = _subject_name(name)
    dep_bodies: list[str] = []
    multi: set[int] = set()
    ref_of: dict[int, list[int]] = {}
    for i, (ref, body) in enumerate(disc["dependent"], start=2):
        body = _oneline(body)
        if not body:
            gaps.append("第 %d 项附加技术特征只有引用、没有限定部分" % i)
            continue
        if "。" in body:
            notes.append("权利要求 %d 的限定部分里的句号已改成「；」"
                         "（每一项权利要求只允许在结尾用一个句号）" % i)
            body = body.replace("。", "；")
        text = "根据权利要求 %s 所述的%s，其特征在于，%s。" % (ref, subject, _rstrip_punct(body))
        nums = check_patent.referenced_claims(text)
        ref_of[i] = nums
        if not nums:
            gaps.append("权利要求 %d 的引用表达式「%s」解析不出权项号" % (i, ref))
        if any(n >= i for n in nums):
            gaps.append("权利要求 %d 引用了在后的权利要求（%s）——从属权利要求只能引用在前的"
                        % (i, "、".join(str(n) for n in nums if n >= i)))
        if len(nums) > 1:
            multi.add(i)
        dep_bodies.append((i, text))
    for i, nums in ref_of.items():
        bad = [n for n in nums if n in multi]
        if len(nums) > 1 and bad:
            gaps.append("权利要求 %d 是多项从属，却引用了另一项多项从属（%s）"
                        "——多项从属不得作为另一项多项从属的基础"
                        % (i, "、".join(str(n) for n in bad)))

    claims_lines = ["1. " + claim1] + ["%d. %s" % (i, t) for i, t in dep_bodies]

    # ---- 说明书各节 ----
    field = _rstrip_punct(_oneline(disc["field"]))
    field = re.sub(r"(?:技术领域|领域)$", "", field)
    tech_field = "本发明涉及%s技术领域，具体涉及%s。" % (field, name)

    background = disc["background"].strip()
    if not background:
        background = ("<!-- 待补：写下最接近的现有技术是怎么做的、它有什么不足。\n"
                      "     背景技术只写客观现状，不要写宣传语，也不要用"
                      "「如权利要求…所述」这类引用语。 -->")
        gaps.append("「背景技术」一节是空的——背景技术是说明书的法定部分")

    scheme = re.sub(r"，?\s*其特征(?:在于|是)\s*[，,]?", "，", claim1)
    scheme = re.sub(r"^1\.\s*", "", scheme)
    problem = disc["problem"].strip() or "（交底书未给出技术问题）"
    if not disc["problem"].strip():
        gaps.append("「技术问题」一节是空的")
    benefits = [_rstrip_punct(x) for x in disc["benefits"]]
    benefits = [x for x in benefits if x]
    benefit_text = "".join(_rstrip_punct(b) + "。" for b in benefits)
    invention_content = (
        "本发明所要解决的技术问题是：%s\n\n"
        "为解决上述技术问题，本发明采用如下技术方案：%s\n\n"
        "本发明的有益效果是：%s"
        % (_rstrip_punct(problem) + "。", scheme, benefit_text or
           "（交底书未给出有益效果）。")
    )
    if not benefits:
        gaps.append("「有益效果」一节是空的——这是发明内容部分的法定内容之一")

    # ---- 具体实施方式 ----
    embodiment = disc["embodiment"].strip()
    if not embodiment:
        embodiment = (scheme + "\n\n"
                      "<!-- 待补：把上面的技术方案展开成可实施的细节——具体结构/参数/"
                      "材料/条件、操作步骤、以及验证数据。只把权利要求书重抄一遍，"
                      "可能被认定为公开不充分（专利法第 26 条第 3 款）。 -->")
        gaps.append("「实施例」一节是空的——具体实施方式只写了技术方案，没有实施细节")

    # ---- 附图说明 + 附图标记说明 ----
    figs = []
    seen_fig: set[int] = set()
    for num, desc in disc["figures"]:
        if num in seen_fig:
            notes.append("附图里图 %d 出现了两次，已只保留第一处" % num)
            continue
        seen_fig.add(num)
        figs.append((num, desc))
    figs.sort(key=lambda x: x[0])

    sign_map = {}
    for num, nm in disc["signs"]:
        if num not in sign_map:
            sign_map[num] = nm
        elif sign_map[num] != nm:
            notes.append("附图标记 %s 给了两个名称（「%s」/「%s」），已取前者"
                         % (num, sign_map[num], nm))

    used: list[str] = []
    scan_sources = [pre, "；".join(feats)] + [b for _, b in disc["dependent"]]
    scan_sources.append(embodiment)
    for src in scan_sources:
        for m in RE_SIGN.finditer(src or ""):
            if m.group(1) not in used:
                used.append(m.group(1))
    used.sort(key=_sign_key)

    for u in used:
        if u not in sign_map:
            sign_map[u] = "（交底书未给出名称）"
            gaps.append("附图标记 %s 在正文里用了，但「附图标记」一节没给名称" % u)

    # 只按「括号形式」收集会把说明书文字部分按惯例写作「算力单元 4」的标记漏掉，
    # 那类标记照旧留在附图标记说明里更接近代理师的写法。所以这里复用
    # check_patent.check_signs 的抑制规则：只要它在发明内容/具体实施方式的散文里
    # 以「中文 + 数字」的裸标记出现过，就不算"列了没用"。
    prose = invention_content + "\n" + embodiment
    for n in sorted([x for x in sign_map if x not in used], key=_sign_key):
        if re.search(r"[\u4e00-\u9fff]\s?" + re.escape(n) + r"(?![0-9A-Za-z.%℃°～~])", prose):
            notes.append("附图标记 %s（%s）没有写成「名称（%s）」的形式，但它在具体实施方式"
                         "里以「名称 %s」出现，已保留在附图标记说明里"
                         % (n, sign_map[n], n, n))
            continue
        notes.append("附图标记 %s（%s）在正文里没用到，已从附图标记说明里略去——"
                     "附图中未出现的标记不得在说明书文字部分提及；若确实要用，"
                     "请在正文里以「名称（%s）」的形式引用" % (n, sign_map[n], n))
        sign_map.pop(n)

    fig_text = ""
    if figs:
        lines = [_figure_line(n, d) for n, d in figs]
        fig_text = "；\n".join(lines) + "。"
    if sign_map:
        listed = "；".join("%s—%s" % (n, sign_map[n]) for n in sorted(sign_map, key=_sign_key))
        fig_text = (fig_text + "\n\n" if fig_text else "") + "附图标记说明：" + listed + "。"
    if not fig_text:
        fig_text = ("<!-- 交底书没有给出附图。确定无附图时，本节连同「说明书附图」"
                    "整节删除；补了附图就要回到这里补图号与图名。 -->")

    # ---- 摘要 ----
    use = _oneline(disc["use"])
    if disc["abstract"].strip():
        # 交底书里常见的「摘要附图：图 1」不是摘要文字部分，去掉；
        # 需要时由下面的拼装环节按附图清单重新写一行。
        abstract = "\n".join(
            ln for ln in disc["abstract"].strip().split("\n")
            if not re.match(r"^\s*摘要附图\s*[：:]", ln)).strip()
        if not abstract:
            abstract = _auto_abstract(disc, name, field, scheme)
            notes.append("交底书的「## 摘要」一节只有「摘要附图」行，已按三段式自动拼接")
    else:
        abstract = _auto_abstract(disc, name, field, scheme)
        notes.append("交底书没给「## 摘要」，已按三段式自动拼接，请人工润色"
                     "（或直接在交底书里补一节 `## 摘要`）")
    if len(abstract) > check_patent.ABSTRACT_MAX:
        gaps.append("摘要 %d 字，超过 %d 字上限" % (len(abstract), check_patent.ABSTRACT_MAX))

    # ---- 拼装 ----
    parts = []
    parts.append("# 发明名称：%s\n" % name)
    parts.append("## 说明书摘要\n\n%s\n" % abstract)
    if figs:
        parts.append("摘要附图：图 %d\n" % figs[0][0])
    parts.append("## 权利要求书\n\n%s\n" % "\n\n".join(claims_lines))
    parts.append("## 说明书\n")
    parts.append("### 技术领域\n\n%s\n" % tech_field)
    parts.append("### 背景技术\n\n%s\n" % background)
    parts.append("### 发明内容\n\n%s\n" % invention_content)
    parts.append("### 附图说明\n\n%s\n" % fig_text)
    parts.append("### 具体实施方式\n\n下面结合附图和实施例对本发明作进一步说明。\n\n"
                 "**实施例 1**\n\n%s\n\n%s\n" % (embodiment, CLOSING))
    if figs:
        fig_rows = "\n\n".join("图 %d　%s" % (n, re.sub(r"^(?:是|为|：|:)\s*", "", d).strip())
                              for n, d in figs)
        parts.append("## 说明书附图\n\n%s\n" % fig_rows)
    text = "\n".join(parts)
    text = re.sub(r"\n{4,}", "\n\n\n", text)

    if disc["_unknown"]:
        notes.append("交底书里的 %s 没被识别，已忽略——检查节名是否写错"
                     % "、".join("「%s」" % x for x in disc["_unknown"]))
    if disc["_stray"]:
        gaps.append("「附加技术特征」里有 %d 行没有归到任何 `### 引用 N` 下：%s"
                    % (len(disc["_stray"]), " / ".join(disc["_stray"][:3])))
    return text, gaps, notes


# ---------------------------------------------------------------------------
# 填空模板
# ---------------------------------------------------------------------------

TEMPLATE = """# 技术交底书：（一句话写清要申请的发明）

<!-- 用法
     1. 把每个 `##` 小节填上（方括号是填写提示，填完删掉）。
     2. python scripts/gen_draft.py 本文件.md -o 申请文件.md
     3. 脚本会用 check_patent.py 把生成的初稿自检一遍，把缺口和不合规项列出来。
     4. 交底书里没有的东西，脚本不会替你编——它会留成显式缺口并在报告里点名。
-->

## 发明名称

[主题名称，一般不超过 25 字；前面不要冠「发明名称」四个字]

## 技术领域

[一到两个技术领域，例如：工业视觉检测]

## 背景技术

[最接近的现有技术是怎么做的；它有什么不足。只写客观现状。]

## 技术问题

[一句话说清本发明要解决的问题]

## 独权前序

[主题名称 + 与最接近现有技术共有的必要技术特征，写到「其特征在于」之前。
 例：一种传送带异物视觉检测方法，包括由图像采集单元（2）采集传送带（1）输送面图像的步骤。
 想自己把握分界的话，可以把「，其特征在于，包括以下步骤：」也一并写进来。]

## 必要技术特征

- [区别技术特征 1。附图标记写成「名称（数字）」]
- [区别技术特征 2]

## 特征部分引导语

[可整节删除。默认：方法类写「包括以下步骤：」，其余写「包括：」。]

## 附加技术特征

<!-- 每一项从属权利要求一个 `###`，标题里写引用表达式：1、1 或 2、2 至 4。
     正文就是限定部分，不要写「根据权利要求…」前缀。 -->

### 引用 1

[附加技术特征]

### 引用 1 或 2

[附加技术特征]

## 有益效果

- [由于……，所以……。]
- [又由于……，所以……。]

## 附图

- 图 1 [图名，例如：检测系统组成示意图]

## 附图标记

- 1—[名称]
- 2—[名称]

## 实施例

[把技术方案展开成可实施的细节：结构、参数、材料、条件、步骤、验证数据。]

## 用途

[主要用途，一句话]

## 摘要

[可整节删除。删了脚本会按「名称+领域+问题+方案+效果+用途」自动拼一段，
 并在报告里标注「自动拼接」。]
"""


# ---------------------------------------------------------------------------
# 固件测试
# ---------------------------------------------------------------------------

_MINI = """# 交底书：一种示例分拣装置

## 发明名称

一种示例分拣装置

## 技术领域

物流分拣

## 背景技术

现有分拣装置靠人工识别。

## 技术问题

现有分拣装置的分拣速度慢。

## 独权前序

一种示例分拣装置，包括机架（1）和输送带（2）

## 必要技术特征

- 所述机架（1）上设有视觉单元（3），所述视觉单元（3）采集所述输送带（2）上的物品图像
- 控制单元（4）按所述物品图像控制分拣臂（5）动作

## 附加技术特征

### 引用 1

所述视觉单元（3）为线阵相机

### 引用 1 或 2

所述控制单元（4）输出的分拣信号带时间戳

## 有益效果

- 由于用视觉单元（3）替代人工识别，分拣速度提高
- 又由于分拣信号带时间戳，误分拣减少

## 附图

- 图 1 分拣装置组成示意图

## 附图标记

- 1—机架
- 2—输送带
- 3—视觉单元
- 4—控制单元
- 5—分拣臂

## 实施例

机架 1 用铝型材搭成，输送带 2 采用皮带，视觉单元 3 装在其上方 300 毫米处。

## 用途

快递包裹的自动分拣
"""


def _self_test(failures: list[str]) -> None:
    """不读任何外部文件也能跑的固件测试。"""
    checks: list[tuple[str, bool]] = []

    def check(label: str, cond: bool) -> None:
        checks.append((label, bool(cond)))

    # ---- 标题规整与查表 ----
    check("节名规整：剥尾部括号与冒号", _canon_title("技术领域（可选）：") == "技术领域")
    check("节名查表：别名命中", _lookup_field("所属技术领域") == "field")
    check("节名查表：前缀回退", _lookup_field("必要技术特征及说明") == "features")
    check("节名查表：认不出来返回 None", _lookup_field("随便写的节") is None)

    # ---- 解析 ----
    d = parse_disclosure(_MINI)
    check("解析：发明名称", d["name"] == "一种示例分拣装置")
    check("解析：技术领域", d["field"] == "物流分拣")
    check("解析：必要技术特征取到 2 条", len(d["features"]) == 2)
    check("解析：有益效果取到 2 条", len(d["benefits"]) == 2)
    check("解析：从属取到 2 项", len(d["dependent"]) == 2)
    check("解析：从属引用归一化", [r for r, _ in d["dependent"]] == ["1", "1 或 2"])
    check("解析：附图", d["figures"] == [(1, "分拣装置组成示意图")])
    check("解析：附图标记取到 5 个", len(d["signs"]) == 5)
    check("解析：识别到的节", "_seen" in d and "field" in d["_seen"])
    check("解析：没有认不出的节", d["_unknown"] == [])

    d2 = parse_disclosure("# 技术交底书：（一句话写清要申请的发明）\n\n## 技术领域\n\nx\n")
    check("解析：模板占位标题不当发明名称", d2["name"] == "")

    d3 = parse_disclosure("# 一种兜底名称\n\n## 技术领域\n\nx\n")
    check("解析：一级标题兜底", d3["name"] == "一种兜底名称")

    d4 = parse_disclosure("## 必要技术特征\n\n- 甲；乙；丙\n")
    check("解析：单条特征按句读拆开", len(d4["features"]) == 3)

    d5 = parse_disclosure("## 附图标记\n\n壳体 1\n2—轴\n3 座\n4轴承\n")
    check("解析：名称在前也认", d5["signs"][0] == ("1", "壳体"))
    check("解析：四种标记写法都认", [n for n, _ in d5["signs"]] == ["1", "2", "3", "4"])

    d6 = parse_disclosure("## 附加技术特征\n\n### 引用 1\n\n甲\n\n### 引用 2-4\n\n乙\n")
    check("解析：区间引用归一化", [r for r, _ in d6["dependent"]] == ["1", "2 至 4"])

    # ---- 引用表达式归一化 ----
    check("引用：剥前缀", _clean_ref("根据权利要求 1") == "1")
    check("引用：并列改择一", _clean_ref("1和2") == "1 或 2")
    check("引用：破折号区间", _clean_ref("1-3") == "1 至 3")
    check("引用：中文顿号", _clean_ref("引用 1、2 所述装置") == "1 或 2")

    # ---- 引导语与主题名称 ----
    check("引导语：方法类", _default_lead("一种示例检测方法") == "包括以下步骤：")
    check("引导语：装置类", _default_lead("一种示例分拣装置") == "包括：")
    check("主题名称剥「一种」", _subject_name("一种示例分拣装置") == "示例分拣装置")

    # ---- 组装 ----
    text, gaps, notes = build_draft(d)
    check("组装：无缺口", gaps == [])
    check("组装：独权句式", "1. 一种示例分拣装置，包括机架（1）和输送带（2），其特征在于，包括："
          "所述机架（1）上设有视觉单元（3）" in text)
    check("组装：从权句式", "2. 根据权利要求 1 所述的示例分拣装置，其特征在于，所述视觉单元（3）"
          "为线阵相机。" in text)
    check("组装：多项从属句式", "3. 根据权利要求 1 或 2 所述的示例分拣装置，其特征在于，" in text)
    check("组装：发明内容去掉了「其特征在于」", "为解决上述技术问题，本发明采用如下技术方案："
          "一种示例分拣装置，包括机架（1）" in text)
    check("组装：附图标记说明按号排序", "附图标记说明：1—机架；2—输送带；3—视觉单元；"
          "4—控制单元；5—分拣臂。" in text)
    check("组装：摘要附图", "摘要附图：图 1" in text)
    check("组装：结尾句", CLOSING in text)
    auto = _auto_abstract(d, "一种示例分拣装置", "物流分拣", "一种示例分拣装置，包括机架（1）。")
    check("组装：自动摘要不超 300 字上限", len(auto) <= 300)
    check("组装：自动摘要在 270 字提醒线以内", len(auto) <= check_patent.ABSTRACT_WARN)

    # 缺口必须报出来，不能静默
    d7 = parse_disclosure(_MINI.replace("## 实施例\n\n机架 1 用铝型材搭成，输送带 2 采用皮带，"
                                        "视觉单元 3 装在其上方 300 毫米处。\n", ""))
    _, g7, _ = build_draft(d7)
    check("缺口：实施例缺失被点名", any("实施例" in g for g in g7))

    d8 = parse_disclosure(_MINI.replace("- 5—分拣臂\n", "")
                          .replace("（5）", "（6）"))
    _, g8, _ = build_draft(d8)
    check("缺口：用了没给名称的标记被点名", any("6" in g for g in g8))

    d9 = parse_disclosure(_MINI.replace("一种示例分拣装置，包括机架（1）和输送带（2）",
                                        "一种示例分拣装置，其特征在于，包括："))
    t9, g9, _ = build_draft(d9)
    check("组装：前序自带分界时不重复插引导语", "包括：所述机架（1）上设有视觉单元（3）" in t9)
    check("组装：自带分界时不报缺口", g9 == [])

    d10 = parse_disclosure(_MINI.replace("一种示例分拣装置\n\n## 技术领域",
                                         "一种示例分拣装置\n\n## 技术领域", 1))
    d10["name"] = "一种示例分拣实用新型"
    _, g10, _ = build_draft(d10)
    check("拒绝：实用新型被点名", any("实用新型" in g for g in g10))

    # ---- 与 check_patent 的端到端 ----
    data, findings = check_patent.run_checks(text)
    fails_only = [f for f in findings if f.level == "fail"]
    check("端到端：生成稿无 fail 级问题", not fails_only)
    check("端到端：生成稿通过 --strict", not [f for f in findings if f.level == "warn"])

    # ---- 模板 ----
    tpl = parse_disclosure(TEMPLATE)
    check("模板：能被解析且节名全部认识", tpl["_unknown"] == [])
    check("模板：字段齐全", set(tpl["_seen"]) >= {k for k, _ in REQUIRED_FIELDS})
    blob = "\n".join([v for v in tpl.values() if isinstance(v, str)]
                     + list(tpl["features"]) + list(tpl["benefits"])
                     + [b for _, b in tpl["dependent"]])
    check("模板：填写提示（HTML 注释）不出现在字段里", "<!--" not in blob)
    d11 = parse_disclosure("## 背景技术\n\n<!-- 只有一段注释 -->\n")
    check("解析：只有注释的小节算空", d11["background"] == "")

    total = SELF_TEST_TOTAL
    if len(checks) != total:
        failures.append("自测项数量核对（登记 %d 项，实际 %d 项）" % (total, len(checks)))
    for label, ok in checks:
        if not ok:
            failures.append(label)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    参数:
        argv: 参数列表；None 时取 `sys.argv[1:]`。

    返回:
        进程退出码：0 成功，1 有缺口或自检不通过，2 用法错误。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    parser = argparse.ArgumentParser(
        prog="gen_draft.py",
        description="把技术交底书一键转成申请文件 Markdown 初稿（纯标准库）。",
        epilog="例：python scripts/gen_draft.py --template > 交底书.md；"
               "python scripts/gen_draft.py 交底书.md -o 申请文件.md --strict")
    parser.add_argument("disclosure", nargs="?", help="技术交底书 Markdown 路径")
    parser.add_argument("-o", "--out", help="输出的初稿路径（默认 <交底书>.draft.md）")
    parser.add_argument("--template", action="store_true", help="打印交底书填空模板后退出")
    parser.add_argument("--strict", action="store_true", help="把「需要复核」也当失败")
    parser.add_argument("--allow-gaps", action="store_true",
                        help="交底书缺口只警告、不影响退出码")
    parser.add_argument("--json", action="store_true", help="只输出 JSON 报告")
    parser.add_argument("-q", "--quiet", action="store_true", help="少打印")
    parser.add_argument("--self-test", action="store_true", help="跑内置固件测试")
    args = parser.parse_args(argv)

    if args.self_test:
        failures: list[str] = []
        _self_test(failures)
        print("\n固件测试：%d/%d 通过" % (SELF_TEST_TOTAL - len(failures), SELF_TEST_TOTAL))
        for label in failures:
            print("  [FAIL] " + label)
        return 1 if failures else 0

    if args.template:
        sys.stdout.write(TEMPLATE)
        return 0

    if not args.disclosure:
        parser.print_usage(sys.stderr)
        print("错误：请给一个交底书路径，或用 --template / --self-test", file=sys.stderr)
        return 2

    src = Path(args.disclosure)
    if not src.is_file():
        print("错误：找不到 %s" % src, file=sys.stderr)
        return 2

    disc = parse_disclosure(src.read_text(encoding="utf-8"))
    missing = [(k, label) for k, label in REQUIRED_FIELDS if not disc[k]]
    if missing:
        print("错误：交底书缺以下小节，无法生成初稿：", file=sys.stderr)
        for _, label in missing:
            print("  · ## %s" % label, file=sys.stderr)
        print("提示：python scripts/gen_draft.py --template 可以拿到填空模板。",
              file=sys.stderr)
        return 2

    text, gaps, notes = build_draft(disc)
    out = Path(args.out) if args.out else src.with_suffix(".draft.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    existed = out.exists()
    out.write_text(text, encoding="utf-8", newline="\n")

    data, findings = check_patent.run_checks(text)
    fails = [f for f in findings if f.level == "fail"]
    warns = [f for f in findings if f.level == "warn"]

    if args.json:
        check_rc = check_patent.report(data, findings, as_json=False, strict=args.strict)
        rc = 1 if (gaps and not args.allow_gaps) else check_rc
        print(json.dumps({
            "source": str(src),
            "out": str(out),
            "name": data["name"],
            "claims": len(data["claims"]),
            "abstract_chars": len(check_patent.abstract_text(data)),
            "gaps": gaps,
            "notes": notes,
            "fails": [f.as_dict() for f in fails],
            "warns": [f.as_dict() for f in warns],
            "ok": rc == 0,
        }, ensure_ascii=False, indent=2))
        return rc

    print("=" * 72)
    print("交底书 → 初稿")
    print("=" * 72)
    print("交底书：%s" % src)
    print("初稿：%s%s" % (out, "（已覆盖同名文件）" if existed else ""))
    print("发明名称：%s" % (data["name"] or "（未找到）"))
    print("权利要求：%d 项（独权 1 项 + 从属 %d 项）"
          % (len(data["claims"]), max(0, len(data["claims"]) - 1)))
    print("摘要：%d 字" % len(check_patent.abstract_text(data)))
    print()

    if notes and not args.quiet:
        print("【生成说明】")
        for n in notes:
            print("  · " + n)
        print()
    if gaps:
        print("【交底书缺口（必须人工补）】")
        for g in gaps:
            print("  · " + g)
        print()

    check_rc = check_patent.report(data, findings, as_json=False, strict=args.strict)
    rc = 1 if (gaps and not args.allow_gaps) else check_rc
    print()
    print("提示：初稿只是形式合格，技术内容仍须人工复核；"
          "机械检查不评价新颖性、创造性和保护范围。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
