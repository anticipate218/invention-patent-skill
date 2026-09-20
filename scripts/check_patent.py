#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""发明专利申请文件草稿的机械自检器：把一份 Markdown 稿子按法条逐项过一遍。

为什么需要这个脚本:
    写完权利要求书和说明书之后，最容易出错的不是"技术方案好不好"——那是人判断
    的事——而是一堆**机械但致命**的形式问题：权利要求编号跳号、从属权利要求引
    用了在后的权项、一项权利要求里写了两个句号、摘要超了 300 字、附图标记在正文
    里用了却没在附图说明里列出、说明书少了"有益效果"……这些问题的法条依据都很
    明确，本可以用机器查，却常常要到形式审查甚至实审阶段才被发现。

    这个脚本就是干这件事的。它**只做机械校验**，不评价技术方案的新颖性、创造性，
    也不判断权利要求保护范围写得剽不剽悍——那些请看 references/ 里的方法论文档，
    并交给执业代理师复核。

法条依据放在哪儿:
    每一条检查的判据都**不在本文件里硬编码条号**，而是指向 `references/` 下对应的
    文档小节（例如"依据：references/claims.md §2"）。这样法条原文只有一处，改了
    法条只改 references/，脚本的输出不会和文档互相矛盾。

输入格式（Markdown 草稿）:
    # 发明名称：一种示例装置

    ## 说明书摘要
    本发明公开了一种示例装置……

    ## 权利要求书
    1. 一种示例装置，其特征在于，包括壳体（1）。
    2. 根据权利要求 1 所述的示例装置，其特征在于……

    ## 说明书
    ### 技术领域
    ### 背景技术
    ### 发明内容
    ### 附图说明
    ### 具体实施方式

    ## 说明书附图
    图1 是示例装置的整体结构示意图。

    用 `python scripts/check_patent.py --init draft.md` 可以生成一份带占位提示的
    骨架，照着填就行。

用法:
    python scripts/check_patent.py draft.md           # 检查
    python scripts/check_patent.py draft.md --json    # 输出 JSON（给 CI/工具用）
    python scripts/check_patent.py --init draft.md    # 生成说明书骨架
    python scripts/check_patent.py draft.md --strict  # 把警告也当失败
    python scripts/check_patent.py --self-test        # 只测解析逻辑

退出码:
    0 = 没有失败项；1 = 有失败项（或 --strict 下有警告）。

只依赖标准库。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
REFERENCES = REPO_ROOT / "references"

# ---------------------------------------------------------------- 判据常量
# 说明书五部分的法定顺序（细则第 20 条第 1 款；第 2 款要求写明标题）
SPEC_SECTIONS = ("技术领域", "背景技术", "发明内容", "附图说明", "具体实施方式")
# 无附图时「附图说明」可以整体省略（指南第一部分第一章 4.2）
OPTIONAL_SECTIONS = ("附图说明",)

PART_ABSTRACT = "说明书摘要"
PART_CLAIMS = "权利要求书"
PART_SPEC = "说明书"
PART_FIGURES = "说明书附图"

# 发明名称字数（指南第二部分第二章 2.2.1）：一般 ≤25，必要时 ≤60
NAME_SOFT = 25
NAME_HARD = 60

# 摘要字数（指南第二部分第二章 2.4 / 第一部分第一章 4.5.1，含标点）
ABSTRACT_MAX = 300
ABSTRACT_WARN = 270

# 法定「引用语」（细则第 20 条第 3 款、第 22 条第 3 款）
RE_BANNED_IN_SPEC = re.compile(r"如权利要求[^。；]{0,40}所述")
RE_BANNED_IN_CLAIMS = (re.compile(r"如说明书[^。；]{0,40}所述"),
                       re.compile(r"如图[^。；]{0,20}所示"))

# 商业性宣传用语（细则第 20 条第 3 款、第 26 条第 2 款）。**启发式清单，非穷尽。**
ADVERTISING = ("世界领先", "国际领先", "国内首创", "填补空白", "独家", "国家级",
               "国际先进", "国内领先", "史无前例", "完美")

# 权利要求中会让保护范围不清楚的含糊词（指南第二部分第二章 3.2.2「清楚」）。
# 只收录边界清楚的几个；「约」「大约」在特定领域可以有确定含义，所以列为警告。
VAGUE_WORDS_HARD = ("等等", "之类", "或其他类似", "或类似物", "类似物",
                    "最好", "最佳", "尤其是", "例如", "必要时")
VAGUE_WORDS_SOFT = ("约", "大约", "左右", "适当", "合适的", "较佳地", "尽可能",
                    "接近", "厚", "薄", "强", "弱", "高温", "低温")

# 单字「等」是最常见的开放式列举用语（指南 3.2.2 不允许），但它也是「等于」「等效」
# 「等间距」「等高」等固定词的构字成分，所以用负向断言排除这些组合，避免误报。
RE_VAGUE_DENG = re.compile(
    r"等(?!于|式|效|价|级|同|号|差|比|量|值|温|压|厚|长|宽|高|重|径|距|间|分|离|势|能)")
VAGUE_WORDS_SOFT = ("约", "大约", "左右", "适当", "合适的", "较佳地", "尽可能",
                    "厚", "薄", "强", "弱", "高温", "低温")

# 权利要求里不允许出现插图（细则第 22 条第 3 款）
RE_IMAGE_IN_CLAIMS = re.compile(r"!\[[^\]]*\]\([^)]*\)|<img\b|<figure\b")

# 附图标记：正文里写作「名称（数字）」；附图说明里写作「数字—名称」或「数字 名称」
RE_SIGN_INLINE = re.compile(r"[（(]\s*(\d+[a-zA-Z]?)\s*[)）]")

# 序列表（细则第 20 条第 4 款）：涉及核苷酸/氨基酸序列时说明书应当包括序列表
RE_SEQUENCE_HINT = re.compile(r"(核苷酸序列|氨基酸序列|SEQ\s*ID|序列表)")

# 「有益效果」是发明内容部分的法定内容之一（细则第 20 条第 1 款第（三）项）
RE_BENEFIT = re.compile(r"(有益效果|优点|优势|相比(?:于)?(?:现有技术|背景技术))")


# ------------------------------------------------------------------ Markdown 解析
def strip_md_inline(text: str, keep_images: bool = False) -> str:
    """去掉行内 Markdown 标记，只留可见文字（用于计字数与查关键词）。

    keep_images=True 时保留 `![](path)` 原样——权利要求的正文要留着它，
    否则「权利要求中不得有插图」这条检查永远看不到图片。
    """
    out = text
    if not keep_images:
        out = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", out)     # 图片
    out = re.sub(r"(?<!!)\[([^\]]*)\]\([^)]*\)", r"\1", out)  # 链接（不含图片）
    out = re.sub(r"`([^`]*)`", r"\1", out)                 # 行内代码
    out = re.sub(r"\*\*([^*]*)\*\*", r"\1", out)
    out = re.sub(r"\*([^*]*)\*", r"\1", out)
    out = re.sub(r"^\s*(?:[-*+]|\d+[.、．])\s+", "", out)   # 列表符号
    return out.strip()


def parse_draft(text: str) -> dict:
    """把 Markdown 草稿拆成结构化字典。

    返回:
        {
          "name": 发明名称或 None,
          "parts": {部件名: 正文文本},
          "spec_sections": {小节名: 正文文本},
          "spec_order": [出现顺序],
          "claims": [(编号, 正文)],
          "signs_listed": [附图说明里列出的标记],
          "raw": 原始文本,
        }

    解析规则刻意做得很宽容：标题用 `#`/`##`/`###` 都行，`发明名称：` 前缀可有可无。
    宽容是有意的——草稿格式千奇百怪，这里卡得太死只会让人放弃用脚本。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    data = {
        "name": None,
        "parts": {},
        "spec_sections": {},
        "spec_order": [],
        "claims": [],
        "signs_listed": [],
        "raw": text,
    }

    def heading_level(line: str) -> tuple[int, str] | None:
        m = re.match(r"^(#{1,6})\s*(.+?)\s*#*\s*$", line)
        if not m:
            return None
        return len(m.group(1)), strip_md_inline(m.group(2))

    # 先找发明名称：第一行 `发明名称：xxx`，或第一个一级标题
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        m = re.match(r"^(?:#+\s*)?发明名称[：:]\s*(.+?)\s*$", stripped)
        if m:
            data["name"] = strip_md_inline(m.group(1))
            break
        h = heading_level(stripped)
        if h and h[0] == 1:
            title = h[1]
            for prefix in ("发明名称：", "发明名称:", "名称：", "名称:"):
                if title.startswith(prefix):
                    title = title[len(prefix):]
                    break
            data["name"] = title
            break

    # 再按标题切块
    current_part: str | None = None
    current_section: str | None = None
    buf_part: list[str] = []
    buf_section: list[str] = []

    def flush_section() -> None:
        if current_section is not None:
            body = "\n".join(buf_section).strip()
            data["spec_sections"][current_section] = body
            # 小节的正文同时并入所属部件的正文：标记一致性、措辞、序列表这些
            # 检查看的是「说明书」整篇文字，而不是某一个小节。
            if body and current_part == PART_SPEC:
                buf_part.append(body)

    def flush_part() -> None:
        if current_part is not None:
            data["parts"][current_part] = "\n".join(buf_part).strip()

    for line in lines:
        h = heading_level(line)
        if h:
            level, title = h
            if level <= 2 and title in (PART_ABSTRACT, PART_CLAIMS, PART_SPEC, PART_FIGURES):
                flush_section()
                current_section = None
                buf_section = []
                flush_part()
                current_part = title
                buf_part = []
                continue
            if level == 2 and title.startswith("发明名称"):
                continue
            if level >= 2 and title in SPEC_SECTIONS:
                if current_part is None:
                    current_part = PART_SPEC
                flush_section()
                current_section = title
                data["spec_order"].append(title)
                buf_section = []
                continue
            # 其它标题：当成正文的一部分（草稿里常见「【技术领域】」这类自由标题）
        if current_section is not None:
            buf_section.append(line)
        elif current_part is not None:
            buf_part.append(line)
    flush_section()
    flush_part()

    # 权利要求书里按「编号. 正文」切
    claims_text = data["parts"].get(PART_CLAIMS, "")
    claims: list[tuple[int, str]] = []
    for line in claims_text.split("\n"):
        m = re.match(r"^\s*(\d+)\s*[.、．)）\]]\s*(.*)$", line)
        if m:
            claims.append((int(m.group(1)), strip_md_inline(m.group(2), keep_images=True)))
        elif claims and line.strip():
            num, body = claims[-1]
            claims[-1] = (num, (body + " " + strip_md_inline(line)).strip())
    data["claims"] = claims

    # 附图标记说明里列出的标记：形如「1—壳体」「1-壳体」「1 壳体」
    spec_text = data["parts"].get(PART_SPEC, "")
    m = re.search(r"附图标记说明[：:]\s*([^\n]*(?:\n(?!\s*\n)[^\n]*)*)", spec_text)
    if m:
        for token in re.split(r"[；;，,、\n]", m.group(1)):
            token = token.strip()
            hit = re.match(r"^(\d+[a-zA-Z]?)\s*[—\-－–]?\s*\S", token)
            if hit:
                data["signs_listed"].append(hit.group(1))
    return data


def abstract_text(data: dict) -> str:
    """取摘要正文（去掉「摘要附图：」那一行，它不是摘要文字部分）。"""
    raw = data["parts"].get(PART_ABSTRACT, "")
    kept = [ln for ln in raw.split("\n")
            if not re.match(r"^\s*摘要附图\s*[：:]", ln)]
    return strip_md_inline("\n".join(kept)).replace(" ", "").replace("\n", "")


def referenced_claims(body: str) -> list[int]:
    """取出权利要求正文里引用的权项号（含「1 至 3」「1 或 2」这类写法）。"""
    nums: list[int] = []
    range_sep = r"(?:至|~|-|—|－)"
    for m in re.finditer(r"权利要求\s*(\d+)\s*%s\s*(\d+)" % range_sep, body):
        lo, hi = int(m.group(1)), int(m.group(2))
        nums.extend(range(min(lo, hi), max(lo, hi) + 1))
    collapsed = re.sub(r"(\d+)\s*%s\s*(\d+)" % range_sep, r"\1", body)
    for m in re.finditer(r"权利要求\s*((?:\d+\s*(?:或|、|,|，)\s*)*\d+)", collapsed):
        nums.extend(int(x) for x in re.findall(r"\d+", m.group(1)))
    return sorted(set(nums))


# ------------------------------------------------------------------ 检查项
class Finding:
    """一条检查结果。level: "fail"（不合规）/ "warn"（建议复核）/ "info"。"""

    __slots__ = ("check", "level", "message", "basis")

    def __init__(self, check: str, level: str, message: str, basis: str) -> None:
        self.check = check
        self.level = level
        self.message = message
        self.basis = basis

    def as_dict(self) -> dict:
        return {"check": self.check, "level": self.level,
                "message": self.message, "basis": self.basis}

    def __str__(self) -> str:
        mark = {"fail": "✗", "warn": "!", "info": "·"}[self.level]
        return "%s [%s] %s（依据：%s）" % (mark, self.check, self.message, self.basis)


def check_presence(data: dict) -> list[Finding]:
    """检查必备部件是否齐全。"""
    out: list[Finding] = []
    basis = "references/claims.md §1、references/format.md §2"
    if not data["name"]:
        out.append(Finding("发明名称", "fail", "找不到发明名称（写一行「# 发明名称：…」）", basis))
    for part in (PART_ABSTRACT, PART_CLAIMS, PART_SPEC):
        if not data["parts"].get(part, "").strip():
            out.append(Finding("部件齐全", "fail", "缺少「%s」部分" % part, basis))
    if not data["parts"].get(PART_FIGURES, "").strip() and not data["parts"].get(PART_SPEC):
        out.append(Finding("部件齐全", "warn", "没有「说明书附图」部分——若确实无附图可忽略",
                           "references/format.md §4"))
    return out


def check_name(data: dict) -> list[Finding]:
    """检查发明名称字数与写法。"""
    out: list[Finding] = []
    basis = "references/format.md §1（名称一般 ≤25 字，必要时 ≤60 字）"
    name = data["name"]
    if not name:
        return out
    n = len(name)
    if n > NAME_HARD:
        out.append(Finding("发明名称", "fail",
                           "名称 %d 字，超过 %d 字的上限" % (n, NAME_HARD), basis))
    elif n > NAME_SOFT:
        out.append(Finding("发明名称", "warn",
                           "名称 %d 字，超过一般上限 %d 字——只有必要时才可放宽到 %d 字"
                           % (n, NAME_SOFT, NAME_HARD), basis))
    if re.match(r"^(发明名称|名称)[：:]", name):
        out.append(Finding("发明名称", "fail",
                           "名称前面不得冠以「发明名称」或「名称」等字样",
                           "references/format.md §1"))
    if re.search(r"[。；;，,]$", name):
        out.append(Finding("发明名称", "warn", "名称以标点结尾，通常不需要", basis))
    return out


def check_spec_sections(data: dict) -> list[Finding]:
    """检查说明书五部分的标题与顺序。"""
    out: list[Finding] = []
    basis = "references/specification.md §1（细则第 20 条第 1、2 款）"
    present = data["spec_order"]
    if not present:
        return [Finding("说明书结构", "fail",
                        "说明书里找不到 {技术领域/背景技术/…} 这类小节标题", basis)]
    missing = [s for s in SPEC_SECTIONS
               if s not in present and s not in OPTIONAL_SECTIONS]
    optional_missing = [s for s in OPTIONAL_SECTIONS if s not in present]
    if missing:
        out.append(Finding("说明书结构", "fail",
                           "缺少法定部分标题：%s（每部分前必须写明标题）" % "、".join(missing),
                           basis))
    if optional_missing:
        out.append(Finding("说明书结构", "warn",
                           "没有「%s」——说明书无附图时可以省略，有附图则必须有"
                           % "、".join(optional_missing), "references/format.md §4"))

    known = [s for s in present if s in SPEC_SECTIONS]
    expected = [s for s in SPEC_SECTIONS if s in known]
    if known != expected:
        out.append(Finding("说明书结构", "fail",
                           "五部分顺序不对：实际 %s，法定顺序 %s"
                           % (" → ".join(known), " → ".join(SPEC_SECTIONS)), basis))

    # 发明内容里必须有「有益效果」（细则第 20 条第 1 款第（三）项）
    content = data["spec_sections"].get("发明内容", "")
    if content and not RE_BENEFIT.search(content):
        out.append(Finding("有益效果", "warn",
                           "「发明内容」里没有找到有益效果/优点的表述——这是该部分的"
                           "法定内容之一", "references/specification.md §4"))
    return out


def check_abstract(data: dict) -> list[Finding]:
    """检查摘要。"""
    out: list[Finding] = []
    basis = "references/format.md §5（细则第 26 条；指南第二部分第二章 2.4）"
    body = abstract_text(data)
    if not body:
        return [Finding("摘要", "fail", "摘要部分没有正文", basis)]
    n = len(body)
    if n > ABSTRACT_MAX:
        out.append(Finding("摘要字数", "fail",
                           "摘要 %d 字（含标点），超过 %d 字上限" % (n, ABSTRACT_MAX), basis))
    elif n > ABSTRACT_WARN:
        out.append(Finding("摘要字数", "warn",
                           "摘要 %d 字，接近 %d 字上限" % (n, ABSTRACT_MAX), basis))
    if len(body) < 40:
        out.append(Finding("摘要字数", "warn", "摘要只有 %d 字，可能没写完" % n, basis))
    if "#" in data["parts"].get(PART_ABSTRACT, ""):
        out.append(Finding("摘要标题", "fail", "摘要文字部分不得使用标题", basis))
    if not re.search(r"(本发明|本实用新型|一种)", body):
        out.append(Finding("摘要内容", "warn",
                           "摘要里没看到发明名称或所属技术领域的表述", basis))
    return out


def check_claims(data: dict) -> list[Finding]:
    """检查权利要求书（本脚本的核心）。"""
    out: list[Finding] = []
    basis = "references/claims.md §2（细则第 22、23、24、25 条）"
    claims = data["claims"]
    if not claims:
        return [Finding("权利要求", "fail",
                        "找不到任何权利要求（写成「1. 一种…」这样的编号段落）", basis)]

    numbers = [n for n, _ in claims]
    if numbers != list(range(1, len(numbers) + 1)):
        out.append(Finding("权利要求编号", "fail",
                           "编号不是从 1 起连续：实际 %s" % numbers, basis))

    refd = {n: referenced_claims(b) for n, b in claims}
    independent = [n for n, _ in claims if not refd[n]]
    if not independent:
        out.append(Finding("独立权利要求", "fail", "没有任何独立权利要求", basis))
    elif numbers and numbers[0] not in independent:
        out.append(Finding("独立权利要求", "fail",
                           "第一项权利要求（%d）不是独立权利要求——独权必须写在同一"
                           "发明的从属权利要求之前" % numbers[0], basis))
    for n, _ in claims:
        for ref in refd[n]:
            if ref not in numbers:
                out.append(Finding("权利要求引用", "fail",
                                   "权利要求 %d 引用了不存在的权利要求 %d" % (n, ref), basis))
            elif ref >= n:
                out.append(Finding("权利要求引用", "fail",
                                   "权利要求 %d 引用了在后的权利要求 %d——从属权利要求"
                                   "只能引用在前的权利要求" % (n, ref), basis))

    # 多项从属不得作为另一项多项从属的基础（细则第 25 条第 2 款）
    multi = {n for n in numbers if len(refd[n]) > 1}
    for n in numbers:
        if len(refd[n]) > 1 and any(r in multi for r in refd[n]):
            out.append(Finding("多项从属", "fail",
                               "权利要求 %d 是多项从属，却引用了另一项多项从属"
                               "（%s）——多项从属不得作为另一项多项从属的基础"
                               % (n, "、".join(str(r) for r in refd[n] if r in multi)),
                               "references/claims.md §4（细则第 25 条第 2 款）"))

    for n, body in claims:
        if body.count("。") > 1:
            out.append(Finding("权利要求标点", "fail",
                               "权利要求 %d 有 %d 个句号——每一项只允许在结尾处用句号"
                               % (n, body.count("。" )), "references/claims.md §2"))
        elif body and not body.rstrip().endswith(("。", "）")):
            out.append(Finding("权利要求标点", "fail",
                               "权利要求 %d 结尾没有句号" % n, "references/claims.md §2"))
        for pat in RE_BANNED_IN_CLAIMS:
            m = pat.search(body)
            if m:
                out.append(Finding("权利要求引用语", "fail",
                                   "权利要求 %d 出现「%s」——除绝对必要外不得使用"
                                   % (n, m.group(0)), "references/claims.md §3"))
        if RE_IMAGE_IN_CLAIMS.search(body):
            out.append(Finding("权利要求插图", "fail",
                               "权利要求 %d 里出现了图片——权利要求中不得有插图" % n,
                               "references/claims.md §2"))
        for word in VAGUE_WORDS_HARD:
            if word in body:
                out.append(Finding("权利要求清楚", "fail",
                                   "权利要求 %d 出现「%s」，会使保护范围不清楚"
                                   % (n, word), "references/claims.md §5"))
        m = RE_VAGUE_DENG.search(body)
        if m:
            out.append(Finding("权利要求清楚", "fail",
                               "权利要求 %d 出现开放式列举用语「%s」，会使保护范围"
                               "不清楚——改用封闭式列举或上位概念"
                               % (n, m.group(0)), "references/claims.md §5"))
        for word in VAGUE_WORDS_SOFT:
            if word in body:
                out.append(Finding("权利要求清楚", "warn",
                                   "权利要求 %d 出现「%s」，请确认在该领域是否有确定"
                                   "含义（否则范围不清楚）" % (n, word),
                                   "references/claims.md §5"))
        if n in refd and refd[n] and "所述" not in body:
            out.append(Finding("从属权利要求", "warn",
                               "权利要求 %d 是从属权利要求，但正文里没有「所述」——"
                               "引用部分后应重述被引用权项的主题名称，限定部分通常用"
                               "「所述…」回指" % n, "references/claims.md §4"))

    # 独权应有前序部分 + 「其特征在于」特征部分（细则第 24 条）
    for n in independent:
        body = dict(claims)[n]
        if "其特征是" not in body and "其特征在于" not in body:
            out.append(Finding("独立权利要求", "warn",
                               "独立权利要求 %d 里没有「其特征是……」，无法一眼看出"
                               "前序部分与特征部分的分界。性质不适于这样表达的可以"
                               "用其他方式撰写，请确认属于哪种" % n,
                               "references/claims.md §3"))
    return out


def check_signs(data: dict) -> list[Finding]:
    """检查附图标记在正文与附图说明之间的一致性。"""
    out: list[Finding] = []
    basis = "references/drawings.md §2（细则第 21 条）"
    spec = data["parts"].get(PART_SPEC, "")
    used: list[str] = []
    for m in RE_SIGN_INLINE.finditer(spec):
        key = m.group(1)
        if key not in used:
            used.append(key)
    claims = data["claims"]
    for _, body in claims:
        for m in RE_SIGN_INLINE.finditer(body):
            if m.group(1) not in used:
                used.append(m.group(1))

    if not used:
        return out
    listed = set(data["signs_listed"])
    if not listed:
        out.append(Finding("附图标记", "warn",
                           "正文里用了附图标记（%s），但没找到「附图标记说明」列表——"
                           "建议在附图说明里逐个列出，便于自查一致性"
                           % "、".join(used), "references/drawings.md §3"))
        return out
    missing = [u for u in used if u not in listed]
    if missing:
        out.append(Finding("附图标记", "fail",
                           "正文用了标记 %s，但「附图标记说明」里没有——说明书文字"
                           "部分提及的标记必须在附图中出现" % "、".join(missing), basis))
    extra = sorted(listed - set(used))
    # 说明书文字部分的附图标记按惯例**不加括号**（如「销 11」），而 used 只收括号形式的
    # 标记，于是这类写法会被当成「列了却没用到」。这里再看「发明内容/具体实施方式」
    # 的散文里有没有「中文 + 数字」的裸标记——只看这两节，就不会把「附图标记说明」
    # 列表里的号当成正文使用。该判断只用于**抑制提示**，不会据此判定失败。
    prose = "".join(data["spec_sections"].get(k, "")
                    for k in ("发明内容", "具体实施方式"))
    extra = [u for u in extra
             if not re.search(r"[\u4e00-\u9fff]\s?" + re.escape(u)
                              + r"(?![0-9A-Za-z.%℃°～~])", prose)]
    if extra:
        out.append(Finding("附图标记", "warn",
                           "「附图标记说明」列了 %s，但正文里没用到——附图中未出现的"
                           "标记不得在说明书文字部分提及" % "、".join(extra), basis))
    return out


def check_wording(data: dict) -> list[Finding]:
    """检查法定的「引用语」与商业性宣传用语。"""
    out: list[Finding] = []
    basis = "references/specification.md §6（细则第 20 条第 3 款）"
    spec = data["parts"].get(PART_SPEC, "")
    abstract = data["parts"].get(PART_ABSTRACT, "")
    for label, text, where in (("说明书", spec, "说明书"), ("摘要", abstract, "摘要")):
        m = RE_BANNED_IN_SPEC.search(text)
        if m:
            out.append(Finding("引用语", "fail",
                               "%s里出现「%s」——不得使用这类引用语" % (label, m.group(0)),
                               basis))
        for phrase in ADVERTISING:
            if phrase in text:
                out.append(Finding("宣传用语", "fail",
                                   "%s里出现疑似商业性宣传用语「%s」" % (label, phrase),
                                   "references/format.md §6"))
    return out


def check_sequence(data: dict) -> list[Finding]:
    """涉及核苷酸/氨基酸序列时，说明书应当包括序列表。"""
    out: list[Finding] = []
    spec = data["parts"].get(PART_SPEC, "")
    if RE_SEQUENCE_HINT.search(spec) and "序列表" not in data["spec_order"]:
        out.append(Finding("序列表", "warn",
                           "说明书里提到了核苷酸/氨基酸序列，但没看到独立的「序列表」"
                           "部分——含序列的发明专利申请说明书应当包括符合规定格式的"
                           "序列表", "references/specification.md §7（细则第 20 条第 4 款）"))
    return out


def check_figures(data: dict) -> list[Finding]:
    """检查附图部分的基本规范。"""
    out: list[Finding] = []
    basis = "references/drawings.md §1（细则第 21 条第 1 款；指南第一部分第一章 4.3）"
    figures = data["parts"].get(PART_FIGURES, "")
    if not figures.strip():
        return out
    nums = re.findall(r"图\s*(\d+)", figures)
    if nums:
        seq = sorted({int(x) for x in nums})
        if seq != list(range(1, len(seq) + 1)):
            out.append(Finding("附图编号", "fail",
                               "图号不是从 1 起连续：实际 %s——几幅附图应当按"
                               "「图1，图2，……」顺序编号排列" % seq, basis))
    if re.search(r"(照片|相片)", figures):
        out.append(Finding("附图形式", "warn",
                           "附图部分提到照片——一般不得使用照片作为附图，金相结构、"
                           "组织细胞、电泳图谱等特殊情况除外", "references/drawings.md §4"))
    if re.search(r"(蓝图|工程蓝图)", figures):
        out.append(Finding("附图形式", "fail", "附图不得使用工程蓝图",
                           "references/drawings.md §4"))
    return out


ALL_CHECKS = (
    ("部件齐全", check_presence),
    ("发明名称", check_name),
    ("说明书结构", check_spec_sections),
    ("摘要", check_abstract),
    ("权利要求", check_claims),
    ("附图标记", check_signs),
    ("措辞", check_wording),
    ("序列表", check_sequence),
    ("附图", check_figures),
)


def strip_comments(text: str) -> str:
    """删掉 HTML 注释块。

    骨架与提纲里用 `<!-- ... -->` 写填写提示，那些提示**不属于申请文件正文**：
    若参与计字数与措辞检查，摘要会被自己的提示语撑到 300 字以上，
    提示语里举例的「工程蓝图」「厚度较薄」也会被误判成草稿缺陷。
    """
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def run_checks(text: str) -> tuple[dict, list[Finding]]:
    """跑全部检查，返回 (解析结果, 发现列表)。"""
    data = parse_draft(strip_comments(text))
    findings: list[Finding] = []
    for _, fn in ALL_CHECKS:
        findings.extend(fn(data))
    return data, findings


# ------------------------------------------------------------------ 骨架生成
SKELETON = """# 发明名称：一种（写清主题名称，一般不超过 25 字）

<!-- 用法：把方括号里的提示替换成你自己的内容；检查用
     python scripts/check_patent.py 本文件.md
     本骨架只是形式骨架，技术内容必须你自己写。 -->

## 说明书摘要

本发明公开了一种……，属于……技术领域。针对现有技术中……的问题，本发明
采用……的技术方案，通过……，实现了……。本发明主要用于……。

摘要附图：图 1

<!-- 摘要要求（依据：references/format.md §5）
     · 写明名称、所属技术领域、所要解决的技术问题、技术方案的要点、主要用途
     · 文字部分含标点不超过 300 字，不得使用标题，不得使用商业性宣传用语
     · 出现的附图标记要加括号，如（1）
     · 有附图时要在请求书中写明摘要附图的图号 -->

## 权利要求书

1. 一种……，其特征在于，包括……（1），……（2）……。

2. 根据权利要求 1 所述的……，其特征在于，……。

3. 根据权利要求 1 或 2 所述的……，其特征在于，……。

<!-- 权利要求要求（依据：references/claims.md）
     · 用阿拉伯数字顺序编号，编号前不得冠以「权利要求」或「权项」
     · 每一项只允许在结尾处用一个句号；不得有插图
     · 除绝对必要外不得用「如说明书……所述」「如图……所示」
     · 独立权利要求 = 前序部分（主题名称 + 与最接近现有技术共有的必要技术特征）
       + 特征部分（「其特征是……」）；独权写在从属权利要求之前
     · 从属权利要求 = 引用部分（权项号 + 主题名称）+ 限定部分（附加技术特征）
     · 从属只能引用在前的权项；多项从属只能择一引用，且不得作为另一项多项从属的基础
     · 附图标记放在相应技术特征后并置于括号内；附图标记不构成对权利要求的限制 -->

## 说明书

### 技术领域

本发明涉及……技术领域，具体涉及一种……。

### 背景技术

<!-- 写明对理解、检索、审查有用的背景技术；有可能的，引证反映这些背景技术的
     文件（专利文献写公开号，非专利文献写完整出处）。这里**不要**写成本发明的
     优点，那是发明内容的事。 -->

### 发明内容

<!-- 依据：references/specification.md §4（细则第 20 条第 1 款第（三）项）
     必须写三件事：① 所要解决的技术问题；② 解决该问题采用的技术方案；
     ③ 对照现有技术写明的有益效果。 -->

本发明所要解决的技术问题是……。

为解决上述技术问题，本发明采用如下技术方案：……。

本发明的有益效果是：……。

### 附图说明

图 1 是……的整体结构示意图；
图 2 是……的局部放大示意图。

附图标记说明：1—……；2—……；11—……。

<!-- 说明书无附图时，本部分连同标题可以整体省略（依据：references/format.md §4）-->

### 具体实施方式

<!-- 详细写明申请人认为实现发明的优选方式；必要时举例说明；有附图的对照附图。
     对照附图描述时，附图标记放在相应技术名称的**后面且不加括号**，例如
     「壳体 1 通过销 11 与盖 2 连接」，不要写成「1 通过 11 与 2 连接」。
     （依据：references/drawings.md §3）-->

下面结合附图和实施例对本发明作进一步说明。

**实施例 1**

……。

<!-- 实施例要写到「本领域技术人员能够实现」的程度：给出具体参数、材料、条件、
     步骤和验证数据。这是说明书是否充分公开（专利法第 26 条第 3 款）的关键。 -->

## 说明书附图

图 1　……示意图
图 2　……示意图

<!-- 附图要求（依据：references/drawings.md）
     · 用制图工具绘制，线条均匀清晰、足够深，不得涂改，不得使用工程蓝图
     · 一般用黑色墨水；必要时可提交彩色附图。不得用照片（金相/细胞/电泳等除外）
     · 图号标在相应附图的正下方，编号前冠以「图」字
     · 缩小到三分之二仍能分辨细节；摘要附图缩小到 4cm×6cm 仍能分辨
     · 附图中除必需的词语外不得含其他注释；词语用中文
     · 电子申请：图片用 JPEG/TIF，不超过 165mm×245mm，图号以文字形式表示 -->

## 引证文件

<!-- 背景技术里引证过的每一份文件都列在这里。格式示例：
     [1] CN1234567A，2019-01-01，一种……
     [2] 张三. 论文标题[J]. 期刊名, 2020, 40(3): 1-8.  -->
"""


# ------------------------------------------------------------------ 报告输出
def report(data: dict, findings: list[Finding], as_json: bool = False,
           strict: bool = False) -> int:
    """打印报告并返回退出码。"""
    fails = [f for f in findings if f.level == "fail"]
    warns = [f for f in findings if f.level == "warn"]

    if as_json:
        print(json.dumps({
            "ok": not fails and not (strict and warns),
            "name": data["name"],
            "claims": len(data["claims"]),
            "spec_sections": data["spec_order"],
            "abstract_chars": len(abstract_text(data)),
            "fails": [f.as_dict() for f in fails],
            "warns": [f.as_dict() for f in warns],
        }, ensure_ascii=False, indent=2))
        return 1 if (fails or (strict and warns)) else 0

    print("=" * 72)
    print("申请文件草稿自检")
    print("=" * 72)
    print("发明名称：%s" % (data["name"] or "（未找到）"))
    print("权利要求：%d 项" % len(data["claims"]))
    print("说明书小节：%s" % (" → ".join(data["spec_order"]) or "（未找到）"))
    print("摘要字数：%d" % len(abstract_text(data)))
    print()

    if fails:
        print("【不合规 %d 项】" % len(fails))
        for f in fails:
            print("  " + str(f))
        print()
    if warns:
        print("【需要复核 %d 项】" % len(warns))
        for f in warns:
            print("  " + str(f))
        print()
    if not fails and not warns:
        print("没有发现问题。")
        print()
        print("注意：本脚本只做机械校验，**不评价技术方案的新颖性、创造性和保护范围**。")
        print("提交前请由执业代理师复核，并核对 references/ 里的法条出处是否仍然现行。")
    else:
        print("以上依据都写在 references/ 对应文档里；法条原文与适用范围以那几份文档为准。")

    print()
    if fails:
        print("结论：不合规 %d 项，需要复核 %d 项。" % (len(fails), len(warns)))
    elif warns and strict:
        print("结论：--strict 模式下 %d 项需要复核，视为未通过。" % len(warns))
    elif warns:
        print("结论：合规，但有 %d 项建议复核。" % len(warns))
    else:
        print("结论：机械校验全部通过。")

    return 1 if (fails or (strict and warns)) else 0


# ------------------------------------------------------------------ 自测
def self_test() -> int:
    """不读任何外部文件也能跑的固件测试。"""
    checks: list[tuple[str, bool]] = []

    def check(label: str, cond: bool) -> None:
        checks.append((label, bool(cond)))

    # ---- 解析 ----
    d = parse_draft("# 发明名称：一种示例装置\n\n## 说明书摘要\n本发明公开了一种示例装置。\n\n"
                    "## 权利要求书\n1. 一种示例装置，其特征在于，包括壳体（1）。\n"
                    "2. 根据权利要求 1 所述的示例装置，其特征在于，所述壳体为金属。\n\n"
                    "## 说明书\n### 技术领域\n本发明涉及机械领域。\n"
                    "### 背景技术\n现有技术……\n### 发明内容\n有益效果是……\n"
                    "### 附图说明\n图 1 是示意图。\n附图标记说明：1—壳体。\n"
                    "### 具体实施方式\n壳体 1 为金属。\n")
    check("解析出发明名称", d["name"] == "一种示例装置")
    check("解析出 2 项权利要求", len(d["claims"]) == 2)
    check("权利要求编号正确", [n for n, _ in d["claims"]] == [1, 2])
    check("解析出说明书五部分",
          d["spec_order"] == ["技术领域", "背景技术", "发明内容", "附图说明", "具体实施方式"])
    check("解析出附图标记说明", d["signs_listed"] == ["1"])
    check("解析出摘要正文", "本发明公开了一种示例装置" in abstract_text(d))

    # 没有 `发明名称：` 前缀的一级标题也要认
    d2 = parse_draft("# 一种无前缀的装置\n\n## 说明书摘要\n……\n")
    check("一级标题不带「发明名称：」也能认出名称", d2["name"] == "一种无前缀的装置")

    # 段落编号前缀不该混进权利要求正文
    d3 = parse_draft("## 权利要求书\n1、一种装置。\n2）根据权利要求 1 所述的装置。\n")
    check("「1、」「2）」两种编号都能解析", [n for n, _ in d3["claims"]] == [1, 2])

    # 续行要并进上一条权利要求
    d4 = parse_draft("## 权利要求书\n1. 一种装置，\n   其特征在于，包括壳体。\n")
    check("权利要求的续行被合并", len(d4["claims"]) == 1 and "壳体" in d4["claims"][0][1])

    # ---- 引用解析 ----
    check("引用「1 或 2」展开", referenced_claims("根据权利要求 1 或 2 所述的装置") == [1, 2])
    check("引用「1 至 3」展开", referenced_claims("根据权利要求 1 至 3 中任一项所述的装置")
          == [1, 2, 3])
    check("引用「2、4、6 或 8」展开",
          referenced_claims("根据权利要求 2、4、6 或 8 所述的装置") == [2, 4, 6, 8])
    check("单一引用", referenced_claims("根据权利要求 1 所述的装置") == [1])
    check("无引用时为空", referenced_claims("一种装置，其特征在于……") == [])

    # ---- 全绿样例 ----
    good = ("# 发明名称：一种示例装置\n\n"
            "## 说明书摘要\n本发明公开了一种示例装置，属于机械传动技术领域。针对现有"
            "技术中离合组件磨损快的问题，本发明通过设置弹性件与摩擦片配合，实现了"
            "动力的柔性断开。本发明主要用于自动化设备。\n\n"
            "## 权利要求书\n"
            "1. 一种示例装置，其特征在于，包括壳体（1）和设于所述壳体（1）内的驱动"
            "单元（2），所述驱动单元（2）通过离合组件（4）与输出部件（3）连接。\n"
            "2. 根据权利要求 1 所述的示例装置，其特征在于，所述离合组件（4）包括"
            "摩擦片（41）和弹性件（42）。\n\n"
            "## 说明书\n"
            "### 技术领域\n本发明涉及机械传动技术领域。\n"
            "### 背景技术\n现有技术的离合组件磨损较快。\n"
            "### 发明内容\n本发明所要解决的技术问题是磨损快。为解决该问题，本发明"
            "采用弹性件与摩擦片配合的方案。本发明的有益效果是寿命更长。\n"
            "### 附图说明\n图 1 是整体结构示意图。\n"
            "附图标记说明：1—壳体；2—驱动单元；3—输出部件；4—离合组件；"
            "41—摩擦片；42—弹性件。\n"
            "### 具体实施方式\n下面结合附图说明。壳体 1 内设有驱动单元 2。\n\n"
            "## 说明书附图\n图 1　整体结构示意图\n")
    _, findings = run_checks(good)
    fails = [f for f in findings if f.level == "fail"]
    check("完整规范样例没有 fail（实际：%s）"
          % ("；".join(f.message for f in fails[:2]) or "无"), not fails)

    def fails_of(text: str) -> list[str]:
        _, fs = run_checks(text)
        return [f.message for f in fs if f.level == "fail"]

    def warns_of(text: str) -> list[str]:
        _, fs = run_checks(text)
        return [f.message for f in fs if f.level == "warn"]

    # ---- 名称 ----
    check("名称超 60 字判失败",
          any("上限" in m for m in fails_of(good.replace("一种示例装置", "长" * 61))))
    check("名称超 25 字给警告",
          any("一般上限" in m for m in warns_of(good.replace("一种示例装置", "长" * 30))))

    # ---- 摘要 ----
    long_abs = good.replace("本发明公开了一种示例装置，属于机械传动技术领域。",
                            "本发明公开了" + "字" * 320)
    check("摘要超 300 字判失败", any("300 字上限" in m for m in fails_of(long_abs)))
    check("摘要缺正文判失败",
          any("摘要部分没有正文" in m for m in fails_of(good.replace(
              "本发明公开了一种示例装置，属于机械传动技术领域。针对现有"
              "技术中离合组件磨损快的问题，本发明通过设置弹性件与摩擦片配合，实现了"
              "动力的柔性断开。本发明主要用于自动化设备。", ""))))

    # ---- 权利要求 ----
    check("编号跳号判失败",
          any("连续" in m for m in fails_of(good.replace(
              "2. 根据权利要求 1 所述的示例装置", "5. 根据权利要求 1 所述的示例装置"))))
    check("从属引用在后权项判失败",
          any("在后的权利要求" in m for m in fails_of(
              "## 权利要求书\n"
              "1. 一种装置，其特征在于，包括壳体。\n"
              "2. 根据权利要求 3 所述的装置，其特征在于，壳体为金属。\n"
              "3. 根据权利要求 1 所述的装置，其特征在于，还包括盖。\n")))
    check("一项权利要求两个句号判失败",
          any("只允许在结尾处用句号" in m for m in fails_of(good.replace(
              "所述驱动单元（2）通过离合组件（4）与输出部件（3）连接。",
              "所述驱动单元（2）通过离合组件（4）与输出部件（3）连接。还包括盖。"))))
    check("权利要求结尾缺句号判失败",
          any("结尾没有句号" in m for m in fails_of(good.replace(
              "所述驱动单元（2）通过离合组件（4）与输出部件（3）连接。",
              "所述驱动单元（2）通过离合组件（4）与输出部件（3）连接"))))
    check("权利要求里「如图……所示」判失败",
          any("如图" in m for m in fails_of(good.replace(
              "所述驱动单元（2）通过离合组件（4）与输出部件（3）连接。",
              "所述驱动单元（2）如图 1 所示连接。"))))
    check("权利要求里「如说明书……所述」判失败",
          any("如说明书" in m for m in fails_of(good.replace(
              "所述驱动单元（2）通过离合组件（4）与输出部件（3）连接。",
              "所述驱动单元（2）如说明书第三段所述连接。"))))
    check("权利要求里有插图判失败",
          any("不得有插图" in m for m in fails_of(
              "## 权利要求书\n1. 一种装置，其特征在于，包括壳体。![](a.png)\n")))
    check("权利要求里「等等」判失败",
          any("保护范围不清楚" in m for m in fails_of(good.replace(
              "摩擦片（41）和弹性件（42）。", "摩擦片（41）和弹性件（42）等等。"))))
    check("权利要求里单字「等」开放式列举判失败",
          any("开放式列举用语" in m for m in fails_of(good.replace(
              "摩擦片（41）和弹性件（42）。",
              "摩擦片（41）和弹性件（42）等部件。"))))
    check("「等间距」「等于」等固定词不误报",
          not any("开放式列举用语" in m for m in fails_of(good.replace(
              "摩擦片（41）和弹性件（42）。",
              "摩擦片（41）和弹性件（42）等间距排列，其间距等于所述壳体的宽度。"))))
    check("权利要求里「例如」「必要时」判失败",
          all(any("保护范围不清楚" in m for m in fails_of(good.replace(
              "摩擦片（41）和弹性件（42）。", "摩擦片（41）和弹性件（42）%s。" % w)))
              for w in ("例如", "必要时", "最佳", "或类似物")))
    check("HTML 注释不计入摘要字数与措辞检查",
          not fails_of(good.replace(
              "## 说明书摘要",
              "<!-- 写法提示：例如可用工程蓝图，厚度较薄的照片也可以 -->\n"
              "## 说明书摘要")))
    check("没有独立权利要求判失败",
          any("没有任何独立权利要求" in m for m in fails_of(
              "## 权利要求书\n1. 根据权利要求 1 所述的装置。\n")))
    check("第一项是从属权利要求判失败",
          any("不是独立权利要求" in m for m in fails_of(
              "## 权利要求书\n1. 根据权利要求 2 所述的装置。\n2. 一种装置。\n")))
    check("独权缺「其特征在于」给警告",
          any("前序部分与特征部分" in m for m in warns_of(good.replace(
              "1. 一种示例装置，其特征在于，包括", "1. 一种示例装置，包括"))))
    check("多项从属引用另一项多项从属判失败",
          any("多项从属" in m for m in fails_of(
              "## 权利要求书\n"
              "1. 一种装置，其特征在于，包括壳体。\n"
              "2. 根据权利要求 1 所述的装置，其特征在于，壳体为金属。\n"
              "3. 根据权利要求 1 或 2 所述的装置，其特征在于，还包括盖。\n"
              "4. 根据权利要求 2 或 3 所述的装置，其特征在于，还包括销。\n")))

    # ---- 说明书结构 ----
    check("说明书缺「发明内容」判失败",
          any("缺少法定部分标题" in m for m in fails_of(good.replace("### 发明内容", "### 发明概述"))))
    check("说明书顺序错判失败",
          any("顺序不对" in m for m in fails_of(
              "## 说明书\n### 技术领域\nA\n### 发明内容\nB\n### 背景技术\nC\n")))
    check("没有说明书小节标题判失败",
          any("找不到" in m for m in fails_of("## 说明书\n这里什么标题都没有。\n")))
    check("发明内容缺有益效果给警告",
          any("有益效果" in m for m in warns_of(good.replace(
              "本发明的有益效果是寿命更长。", "本发明结构简单。"))))
    check("缺附图说明只给警告",
          any("可以省略" in m for m in warns_of(
              good.replace("### 附图说明\n图 1 是整体结构示意图。\n"
                           "附图标记说明：1—壳体；2—驱动单元；3—输出部件；4—离合组件；"
                           "41—摩擦片；42—弹性件。\n", ""))))

    # ---- 附图标记 ----
    check("正文用了未列出的标记判失败",
          any("附图标记说明」里没有" in m for m in fails_of(good.replace(
              "附图标记说明：1—壳体；2—驱动单元；3—输出部件；4—离合组件；"
              "41—摩擦片；42—弹性件。", "附图标记说明：1—壳体。"))))
    check("列了但正文没用到给警告",
          any("正文里没用到" in m for m in warns_of(good.replace(
              "41—摩擦片；42—弹性件。", "41—摩擦片；42—弹性件；5—支架。"))))
    check("正文里的裸标记（不带括号）不算「没用到」",
          not any("正文里没用到" in m for m in warns_of(good.replace(
              "41—摩擦片；42—弹性件。", "41—摩擦片；42—弹性件；5—支架。").replace(
              "壳体 1 内设有驱动单元 2。",
              "壳体 1 内设有驱动单元 2，所述驱动单元 2 通过支架 5 固定。"))))

    # ---- 措辞 ----
    check("说明书里「如权利要求……所述」判失败",
          any("引用语" in m for m in fails_of(good.replace(
              "现有技术的离合组件磨损较快。", "如权利要求 1 所述的装置磨损快。"))))
    check("宣传用语判失败",
          any("宣传用语" in m for m in fails_of(good.replace(
              "本发明涉及机械传动技术领域。", "本发明是世界领先的技术。"))))

    # ---- 序列表 ----
    check("提到序列但无序列表部分给警告",
          any("序列表" in m for m in warns_of(good.replace(
              "下面结合附图说明。壳体 1 内设有驱动单元 2。",
              "下面结合附图说明。本发明的核苷酸序列如 SEQ ID NO:1 所示。"))))

    # ---- 附图 ----
    check("图号不连续判失败",
          any("图号不是从 1 起连续" in m for m in fails_of(good.replace(
              "图 1　整体结构示意图", "图 1　整体结构示意图\n图 3　另一示意图"))))
    check("附图用工程蓝图判失败",
          any("工程蓝图" in m for m in fails_of(good.replace(
              "图 1　整体结构示意图", "图 1　工程蓝图"))))

    # ---- 登记表 ----
    check("检查登记表覆盖 9 项", len(ALL_CHECKS) == 9)

    # ---- 骨架 ----
    skel_fails = fails_of(SKELETON)
    check("骨架能解析出发明名称",
          parse_draft(SKELETON)["name"].startswith("一种（写清主题名称"))
    check("骨架本身不会报大量 fail（%d 项）" % len(skel_fails), len(skel_fails) <= 6)

    failed = [label for label, good_ in checks if not good_]
    for label, good_ in checks:
        print("  %s %s" % ("PASS" if good_ else "FAIL", label))
    print()
    print("self-test: %d/%d 通过" % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


# ------------------------------------------------------------------ CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="发明专利申请文件草稿的机械自检器（只做形式校验，不评价技术方案）")
    parser.add_argument("draft", nargs="?", metavar="FILE",
                        help="Markdown 草稿文件；不填且不用 --init 时读标准输入")
    parser.add_argument("--init", action="store_true",
                        help="生成一份带占位提示的说明书骨架（配合 FILE 使用）")
    parser.add_argument("--json", action="store_true", dest="as_json",
                        help="以 JSON 输出结果")
    parser.add_argument("--strict", action="store_true",
                        help="把警告也当成失败（CI 用）")
    parser.add_argument("--self-test", action="store_true",
                        help="只跑解析逻辑的固件测试")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.init:
        if not args.draft:
            print("--init 需要指定目标文件，例如 python scripts/check_patent.py --init draft.md")
            return 1
        path = pathlib.Path(args.draft)
        if path.exists():
            print("文件已存在，不覆盖：%s" % path)
            return 1
        path.write_text(SKELETON, encoding="utf-8")
        print("已生成说明书骨架：%s" % path)
        print("接下来：填内容 → python scripts/check_patent.py %s" % path)
        return 0

    if args.draft:
        path = pathlib.Path(args.draft)
        if not path.exists():
            print("找不到文件：%s" % path)
            return 1
        text = path.read_text(encoding="utf-8").lstrip("\ufeff")
    else:
        text = sys.stdin.read()

    data, findings = run_checks(text)
    return report(data, findings, args.as_json, args.strict)


if __name__ == "__main__":
    sys.exit(main())
