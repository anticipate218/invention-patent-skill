#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""CNIPA 发明专利申请文件 LaTeX 模板的「真编译 + 真合规」体检。

为什么需要这个脚本:
    `assets/latex/cnipa/main.tex` 是直接发给用户用的模板，文件头写明了编译顺序
    （xelatex → bibtex → xelatex → xelatex）。但它的正确性——宏包是否齐全、
    `\\cite` 能否解析、`\\ref` 是否收敛、中文字体是否装得上——只有**真正编译
    一遍**才能确认。CI 若不跑这一步，模板可以一直悄悄地坏：仓库全绿，而用户
    拿到手第一遍就报 `File \\`xxx.sty' not found`。

    脚本把模板的 `main.tex` + `refs.bib` 复制到系统临时目录编译，因此**不会往
    仓库里留下 .aux/.log/.pdf 之类的编译垃圾**，也不改动仓库里的任何模板文件。

除了「能不能编译」，脚本还查 7 类**结构性合规**（查 .tex 源码，不查 PDF）:
    1. 一级部件顺序 —— 说明书摘要 → 权利要求书 → 说明书 → 说明书附图 → 引证文件；
    2. 说明书五部分顺序 —— 技术领域 → 背景技术 → 发明内容 → 附图说明 → 具体实施方式；
    3. 版式 —— A4、上 25 / 左 25 / 右 15 / 下 15 mm；
    4. 权利要求 —— 编号从 1 起连续、首项为独立权利要求、从属只引用在前的权利要求；
    5. 摘要字数 —— 不超过 300 字（含标点）；
    6. 法定「引用语」与商业性宣传用语 —— 摘要／说明书不得出现「如权利要求……
       所述」，权利要求不得出现「如说明书……所述」「如图……所示」；
    7. 附图标记 —— 正文用到的每个标记都要在「附图标记说明」里列出。

    查源码而不是查 PDF：这些关系完全由 .tex 里若干条命令的先后与写法决定，
    查源码更直接，也省掉一个抽取 PDF 文本的外部依赖（PDF 抽取还要处理中文字体
    编码，脆弱得多）。

    **每一条都对应 references/ 里的法条出处**，脚本只做机械校验；法条原文与
    适用范围请以 `references/format.md`、`references/claims.md` 为准。

关于中文字体（这是唯一一处「编译的不是原文件」）:
    模板用 `fontset=windows`，直接调用 Windows 自带的宋体/黑体，在 Windows 上
    开箱即用；但 Windows 字体在 Linux 上并不存在。所以脚本在**临时副本**里把
    `fontset=windows` 换成随 TeX 发行版自带、Windows 上同样可用的 `fandol`，再
    编译。仓库里的模板一个字都不动。

    这个替换是「尽力而为」的：如果模板里已经没有 `fontset=windows`（例如改成
    了 `auto`），脚本打印一行说明后按原样编译。真正的判据始终是编译结果——
    字体装不上时 fontspec 会报 `The font "..." cannot be found`，那是硬失败。

用法:
    python scripts/check_latex.py                 # 缺 TeX 时跳过（退出码 0）
    python scripts/check_latex.py --require       # 缺 TeX 时视为失败（CI 用这个）
    python scripts/check_latex.py --keep          # 保留临时目录，便于翻 .log
    python scripts/check_latex.py --keep-fontset  # 不替换字体集，编译仓库原件
    python scripts/check_latex.py --tex-dir DIR   # 把 DIR 加到 PATH 最前面找引擎
    python scripts/check_latex.py --self-test     # 只测解析逻辑，不需要装 TeX

退出码:
    0 = 模板编译通过且结构性合规（或按约定跳过）；1 = 编译失败 / 合规检查不过。

只依赖标准库；引擎（xelatex / bibtex）由 PATH 上的 TeX 发行版提供。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
LATEX_DIR = REPO_ROOT / "assets" / "latex"

# 模板清单。CNIPA 只有一种申请文件版式，所以这里是单元素元组；保留元组形态是为了
# 将来加入别的版式（如 PCT 国际申请首页）时不用改调用点。
TEMPLATES = (
    {
        "name": "cnipa",
        "engine": "xelatex",
        "needs_bibtex": True,
        "min_pages": 3,
    },
)

# 仓库里的模板用 fontset=windows；Linux 上没有这些字体，临时副本里换成 fandol。
FONTSET_FROM = "fontset=windows"
FONTSET_TO = "fontset=fandol"

MIN_PDF_BYTES = 5000        # 小于这个体积基本可以断定不是一份正常排版的申请文件

# ------------------------------------------------------------ 结构性合规的判据
# 一级部件的法定顺序（《专利法实施细则》对申请文件组成的要求；请求书是国知局
# 表格，不在模板内，所以这里从「说明书摘要」起算）。
PART_ORDER = ("说明书摘要", "权利要求书", "说明书", "说明书附图")
PART_ORDER_LAST = "引证文件"       # \bibliography 生成的参考文献题名

# 说明书五部分的法定顺序（《专利法实施细则》第 20 条第 1 款；第 2 款要求每部分
# 前写明标题——所以这里连标题一起查）。
SPEC_SECTIONS = ("技术领域", "背景技术", "发明内容", "附图说明", "具体实施方式")

# 版式：A4 297×210mm；页边距上 25 / 左 25 / 右 15 / 下 15 mm。
# 出处见 references/format.md（《专利审查指南》第一部分第一章）。
A4_WIDTH_MM = 210
A4_HEIGHT_MM = 297
REQUIRED_MARGINS = {"top": "25mm", "left": "25mm", "right": "15mm", "bottom": "15mm"}

# 摘要字数上限（含标点）。出处见 references/format.md。
ABSTRACT_MAX_CHARS = 300
ABSTRACT_WARN_CHARS = 270   # 预警阈值：接近上限就提醒，别等超了才发现

# 法定「引用语」：《专利法实施细则》第 20 条第 3 款（说明书）与第 22 条（权利
# 要求书）。这些是明文禁止的措辞，检出即失败。
FORBIDDEN_REFS_IN_SPEC = (r"如权利要求[^。；]{0,40}所述",)
FORBIDDEN_REFS_IN_CLAIMS = (r"如说明书[^。；]{0,40}所述", r"如图[^。；]{0,20}所示")

# 商业性宣传用语：**这是启发式清单，远非穷尽**，只收录边界清楚、几乎不会误伤的
# 几个。写权利要求／说明书时请按 references/format.md 的说明自查。
ADVERTISING_PHRASES = ("世界领先", "国际领先", "国内首创", "填补空白", "独家", "国家级")

# ---------------------------------------------------------------- 日志解析规则
# 硬错误：LaTeX 的报错行一律以 '!' 开头（缺宏包、字体装不上、未定义命令都在这）
_RE_HARD_ERROR = re.compile(r"^!", re.M)
# 未解析引用。新旧 LaTeX 的引号风格不同（`x' 与 'x'），所以不锚定引号字符。
_RE_UNDEF_CITE = re.compile(r"Citation[^\n]{0,300}?undefined")
_RE_UNDEF_REF = re.compile(r"Reference[^\n]{0,300}?undefined")
_RE_UNDEF_ANY = re.compile(r"There were undefined references")
_RE_MISSING_FONT = re.compile(r"font\s+\"[^\"]+\"\s+cannot be found", re.I)
_RE_EMERGENCY = re.compile(r"Emergency stop|Fatal error occurred", re.I)
# 跑完 3 遍还有这条警告，说明交叉引用/页码没收敛
_RE_RERUN = re.compile(r"Rerun to get (?:cross-references|outline) right", re.I)
# 注意字节数是可选的：TeX Live 写 "main.pdf (9 pages, 341464 bytes)."，
# 而 MiKTeX 只写 "main.pdf (9 pages)."——不能把字节数写成必填，否则在 MiKTeX
# 上会把明明编译成功的模板判成"没产出 PDF"。页数取自日志，字节数一律以磁盘
# 上真实的文件大小为准（见 run_template）。
_RE_OUTPUT = re.compile(r"Output written on (\S+?\.pdf) \((\d+) pages?(?:, (\d+) bytes)?\)")
_RE_OVERFULL = re.compile(r"Overfull \\hbox")
_RE_UNDERFULL = re.compile(r"Underfull \\hbox")
# .blg（bibtex 的日志）里真正致命的：打不开 .bib / .bst
_RE_BLG_FATAL = re.compile(r"^I couldn't open (?:database|style) file", re.M)
# bibtex 的其它报错（缺字段、注释里混进条目类型等）。它把错误写在正文里而**不**
# 以 '!' 开头，所以必须单独抓。本仓库真踩过这个坑：refs.bib 的注释里写了条目
# 类型符号，bibtex 把它当成新条目的开头，报了 4 条错，而 PDF 照样编得出来。
_RE_BLG_ERROR = re.compile(r"^(?:You're missing|I was expecting|I'm skipping|"
                           r"Warning--|Repeated entry|Sorry---you've exceeded)", re.M)


def _short(text: str, limit: int = 130) -> str:
    """把日志行压成一行短摘要，方便塞进汇总表。"""
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _split_comment(line: str) -> tuple[str, str]:
    """把一行 LaTeX 拆成 (代码部分, 注释部分)；`\\%` 不算注释起点。"""
    for i, ch in enumerate(line):
        if ch == "%" and (i == 0 or line[i - 1] != "\\"):
            return line[:i], line[i:]
    return line, ""


def _strip_comments(text: str) -> str:
    """去掉所有注释，只留真正会被 TeX 执行的代码。

    **为什么必须做这一步**：模板文件头的说明文字里就写着 `\\bibliography{refs}`、
    `fontset=windows`，还有「如权利要求……所述」这类在讲规则的被禁措辞。按整篇
    文本搜索，这些说明句会被当成真的命令或真的违规，检查结果全是假的。
    """
    return "\n".join(_split_comment(line)[0] for line in text.splitlines())


def override_fontset_text(text: str) -> tuple[str, int]:
    """把**代码里**的 `fontset=windows` 换成 `fontset=fandol`，返回 (新文本, 次数)。

    只动代码、不动注释：模板文件头的说明文字里也有 `fontset=windows`，改了会
    把"请把 fontset=windows 换成 fontset=fandol"这句说明改成同义反复，还会让
    计数虚高。独立成纯函数是为了能在 `--self-test` 里测，不需要装 TeX。
    """
    lines = text.split("\n")
    total = 0
    for i, line in enumerate(lines):
        code, comment = _split_comment(line)
        hits = code.count(FONTSET_FROM)
        if hits:
            total += hits
            lines[i] = code.replace(FONTSET_FROM, FONTSET_TO) + comment
    return "\n".join(lines), total


def _balanced_group(text: str, open_idx: int) -> tuple[str, int]:
    """从 `text[open_idx] == '{'` 开始取配对的花括号内容，返回 (内容, 右括号下标)。

    右括号下标为 -1 表示括号没配对（源码残缺）。自己写而不用正则，是因为
    `\\claim{1}{... \\mref{2} ...}` 这类嵌套结构正则匹配不了。
    """
    depth = 0
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == "\\":            # 跳过被转义的字符（\{ \} 不算括号）
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[open_idx + 1: i], i
        i += 1
    return text[open_idx + 1:], -1


def _command_bodies(code: str, command: str) -> list[tuple[str, int]]:
    """取出 `\\command{...}` 的所有参数体，返回 [(参数体, 命令起始下标)]。"""
    out: list[tuple[str, int]] = []
    pattern = re.compile(re.escape(command) + r"\s*\{")
    for m in pattern.finditer(code):
        body, _ = _balanced_group(code, m.end() - 1)
        out.append((body, m.start()))
    return out


def _first_index(text: str, *patterns: str) -> int | None:
    """返回任一模式在文本中最靠前的位置；都没有则 None。"""
    hits = [m.start() for p in patterns for m in re.finditer(p, text)]
    return min(hits) if hits else None


# ------------------------------------------------------------------ 各条检查


def check_part_order(tex_text: str) -> tuple[bool, str]:
    """核对一级部件的先后：摘要 → 权利要求书 → 说明书 → 附图 → 引证文件。"""
    code = _strip_comments(tex_text)
    positions: dict[str, int] = {}
    for body, at in _command_bodies(code, r"\parttitle"):
        title = body.strip()
        if title in PART_ORDER and title not in positions:
            positions[title] = at

    missing = [p for p in PART_ORDER if p not in positions]
    if missing:
        return False, "缺少一级部件：%s" % "、".join(missing)

    ordered = sorted(PART_ORDER, key=lambda p: positions[p])
    if list(ordered) != list(PART_ORDER):
        return False, "一级部件顺序不对：实际为 %s（法定顺序 %s）" % (
            " → ".join(ordered), " → ".join(PART_ORDER))

    bib_at = _first_index(code, r"\\bibliography\{", r"\\begin\{thebibliography\}")
    if bib_at is None:
        return False, r"源码里找不到 \bibliography{...} 或 thebibliography 环境"
    if bib_at < positions[PART_ORDER[-1]]:
        return False, "引证文件排在「%s」之前 ✗" % PART_ORDER[-1]
    return True, "一级部件顺序 ✓（%s → %s）" % (" → ".join(PART_ORDER), PART_ORDER_LAST)


def check_spec_sections(tex_text: str) -> tuple[bool, str]:
    """核对说明书五部分的标题与先后（细则第 20 条第 1、2 款）。"""
    code = _strip_comments(tex_text)
    positions: dict[str, int] = {}
    for body, at in _command_bodies(code, r"\sect"):
        title = body.strip()
        if title in SPEC_SECTIONS and title not in positions:
            positions[title] = at

    missing = [s for s in SPEC_SECTIONS if s not in positions]
    if missing:
        return False, "说明书缺少法定部分标题：%s" % "、".join(missing)

    ordered = sorted(SPEC_SECTIONS, key=lambda s: positions[s])
    if list(ordered) != list(SPEC_SECTIONS):
        return False, "说明书各部分顺序不对：实际为 %s（法定顺序 %s）" % (
            " → ".join(ordered), " → ".join(SPEC_SECTIONS))
    return True, "说明书五部分 ✓（%s）" % " → ".join(SPEC_SECTIONS)


def check_layout(tex_text: str) -> tuple[bool, str]:
    """核对纸张与页边距。"""
    code = _strip_comments(tex_text)
    problems: list[str] = []

    if "a4paper" not in code:
        problems.append("文档类里没有 a4paper")
    geo = re.search(r"\\usepackage\s*\[([^\]]*)\]\s*\{geometry\}", code)
    if not geo:
        problems.append(r"没有 \usepackage[...]{geometry}")
    else:
        opts: dict[str, str] = {}
        for item in geo.group(1).split(","):
            if "=" in item:
                key, _, value = item.partition("=")
                opts[key.strip()] = value.strip()
        for key, want in REQUIRED_MARGINS.items():
            got = opts.get(key)
            if got is None:
                problems.append("geometry 没有设置 %s 边距（应为 %s）" % (key, want))
            elif got.replace(" ", "") != want:
                problems.append("geometry 的 %s 边距是 %s，应为 %s" % (key, got, want))

    if problems:
        return False, "版式：" + "；".join(problems)
    return True, "版式 ✓（A4 %d×%dmm；%s）" % (
        A4_WIDTH_MM, A4_HEIGHT_MM,
        "、".join("%s=%s" % (k, v) for k, v in REQUIRED_MARGINS.items()))


def parse_claims(code: str) -> list[tuple[int, str]]:
    """取出权利要求书里的 `\\claim{编号}{正文}`，返回 [(编号, 正文)]。"""
    out: list[tuple[int, str]] = []
    for m in re.finditer(r"\\claim\s*\{", code):
        num_body, close = _balanced_group(code, m.end() - 1)
        if close < 0:
            continue
        tail = code[close + 1:]
        brace = tail.find("{")
        if brace < 0:
            continue
        if tail[:brace].strip():
            continue           # 编号与正文之间混进了别的东西，不是这条宏
        body, _ = _balanced_group(tail, brace)
        try:
            out.append((int(num_body.strip()), body))
        except ValueError:
            continue
    return out


def referenced_claims(body: str) -> list[int]:
    """取出权利要求正文里引用的权利要求号。

    要处理三种合法写法（指南第二部分第二章 3.3.2 给的正是这三种）：
        根据权利要求 1 或 2 所述的……          → [1, 2]
        根据权利要求 2、4、6 或 8 所述的……     → [2, 4, 6, 8]
        根据权利要求 4 至 9 中任一权利要求所述的…… → [4, 5, 6, 7, 8, 9]
    「至」写在末尾的那个数字**不是**范围终点，所以只有它前面紧挨着的是数字或
    「至」时才当成范围。
    """
    nums: list[int] = []
    range_sep = r"(?:至|~|-|—|－)"
    list_sep = r"(?:或|、|,|，)"
    # 范围：N 至 M
    for m in re.finditer(r"权利要求\s*(\d+)\s*%s\s*(\d+)" % range_sep, body):
        lo, hi = int(m.group(1)), int(m.group(2))
        nums.extend(range(min(lo, hi), max(lo, hi) + 1))
    # 并列：N、M 或 K（先摘掉范围写法，避免同一处被数两遍）
    collapsed = re.sub(r"(\d+)\s*%s\s*(\d+)" % range_sep, r"\1", body)
    for m in re.finditer(r"权利要求\s*((?:\d+\s*%s\s*)*\d+)" % list_sep, collapsed):
        nums.extend(int(x) for x in re.findall(r"\d+", m.group(1)))
    return sorted(set(nums))


def check_claims(tex_text: str) -> tuple[bool, str]:
    """核对权利要求编号与引用关系（细则第 22、23、24、25 条）。"""
    code = _strip_comments(tex_text)
    claims = parse_claims(code)
    if not claims:
        return False, r"权利要求书里没有找到任何 \claim{编号}{正文}"

    problems: list[str] = []
    numbers = [n for n, _ in claims]
    if numbers != list(range(1, len(numbers) + 1)):
        problems.append("编号不是从 1 起连续：实际 %s" % numbers)

    independent = [n for n, body in claims if not referenced_claims(body)]
    if not independent:
        problems.append("没有任何独立权利要求")
    elif numbers and numbers[0] not in independent:
        problems.append("第一项权利要求（%d）不是独立权利要求" % numbers[0])

    for n, body in claims:
        for ref in referenced_claims(body):
            if ref not in numbers:
                problems.append("权利要求 %d 引用了不存在的权利要求 %d" % (n, ref))
            elif ref >= n:
                problems.append("权利要求 %d 引用了在其之后的权利要求 %d" % (n, ref))

    # 「每一项权利要求只允许在其结尾处使用句号」（指南第二部分第二章 3.3）。
    # 这项检查只数**正文里**的句号：`\mref{...}` 之类的宏参数已被剥离，注释也
    # 早被去掉，所以不会误伤。中间出现句号说明这一项被写成了好几句话。
    for n, body in claims:
        plain = latex_to_plain(body)
        if plain.count("。") > 1:
            problems.append("权利要求 %d 正文里有 %d 个句号——只允许在结尾处用句号"
                            % (n, plain.count("。")))
        elif plain and not plain.endswith("。"):
            problems.append("权利要求 %d 的结尾没有句号" % n)

    if problems:
        return False, "权利要求：" + "；".join(problems)
    return True, "权利要求 ✓（共 %d 项，独立 %d 项，编号 1–%d）" % (
        len(claims), len(independent), len(numbers))


def abstract_body(code: str) -> str | None:
    """取「说明书摘要」与「权利要求书」之间第一个 `\\npara`／`\\spara` 的内容。

    这是本模板的约定：摘要正文写成一条 `\\npara{...}`。取不到就返回 None，
    由调用方说明「没找到摘要正文」而不是静默按 0 字通过。
    """
    start = _first_index(code, r"\\parttitle\s*\{\s*说明书摘要\s*\}")
    end = _first_index(code, r"\\parttitle\s*\{\s*权利要求书\s*\}")
    if start is None:
        return None
    region = code[start: end if end is not None else len(code)]
    for command in (r"\npara", r"\spara"):
        bodies = _command_bodies(region, command)
        if bodies:
            return bodies[0][0]
    return None


def latex_to_plain(text: str) -> str:
    """把一段 LaTeX 压成「近似可见文本」，用于计字数与查违禁语。

    处理顺序有讲究：
      1. 先把 `\\mref{1}` 换成它真正排出来的 `（1）`——摘要的 300 字上限是**按
         可见字符**算的（含标点），把附图标记的括号吃掉会少算字数；
      2. 再去掉其余命令名（`\\noindent`、`\\zihao` 之类）；
      3. 去掉剩下的花括号与转义符，合并空白。

    这只是近似——它不解释宏展开，所以**只能用于机械检查**，不能拿它当最终排版
    结果。
    """
    out = re.sub(r"\\mref\s*\{([^{}]*)\}", r"（\1）", text)
    out = re.sub(r"\\[a-zA-Z@]+\s*", "", out)
    out = out.replace("\\%", "%").replace("\\&", "&").replace("\\_", "_")
    out = re.sub(r"[{}$~^]", "", out)
    out = re.sub(r"\s+", "", out)
    return out


def check_abstract(tex_text: str) -> tuple[bool, str]:
    """核对摘要字数（≤300 字，含标点）。"""
    code = _strip_comments(tex_text)
    body = abstract_body(code)
    if body is None:
        return False, r"没找到摘要正文（约定：\parttitle{说明书摘要} 之后写一条 \npara{...}）"
    n = len(latex_to_plain(body))
    if n > ABSTRACT_MAX_CHARS:
        return False, "摘要 %d 字，超过上限 %d 字" % (n, ABSTRACT_MAX_CHARS)
    if n > ABSTRACT_WARN_CHARS:
        return True, "摘要 %d 字，接近上限 %d 字——留点余量" % (n, ABSTRACT_MAX_CHARS)
    if n < 30:
        return True, "摘要只有 %d 字，可能还没写完" % n
    return True, "摘要 %d 字 ✓（上限 %d）" % (n, ABSTRACT_MAX_CHARS)


def check_forbidden_wording(tex_text: str) -> tuple[bool, str]:
    """查法定的「引用语」与商业性宣传用语。"""
    code = _strip_comments(tex_text)
    problems: list[str] = []

    spec_at = _first_index(code, r"\\parttitle\s*\{\s*说明书\s*\}")
    claim_at = _first_index(code, r"\\parttitle\s*\{\s*权利要求书\s*\}")
    abstract = abstract_body(code) or ""

    for pattern in FORBIDDEN_REFS_IN_SPEC:
        for m in re.finditer(pattern, abstract):
            problems.append("摘要里出现被禁引用语「%s」" % m.group(0))
        if spec_at is not None:
            for m in re.finditer(pattern, code[spec_at:]):
                problems.append("说明书里出现被禁引用语「%s」" % m.group(0))

    if claim_at is not None:
        region_end = spec_at if spec_at is not None else len(code)
        for pattern in FORBIDDEN_REFS_IN_CLAIMS:
            for m in re.finditer(pattern, code[claim_at:region_end]):
                problems.append("权利要求书里出现被禁引用语「%s」" % m.group(0))

    for phrase in ADVERTISING_PHRASES:
        if phrase in code:
            problems.append("出现疑似商业性宣传用语「%s」" % phrase)

    if problems:
        tail = "等 %d 处" % len(problems) if len(problems) > 4 else ""
        return False, "；".join(problems[:4]) + tail
    return True, "措辞 ✓（无被禁引用语；启发式宣传语清单 %d 项全未命中）" % len(ADVERTISING_PHRASES)


def check_reference_signs(tex_text: str) -> tuple[bool, str]:
    """核对附图标记：正文用到的标记都要在「附图标记说明」里列出（细则第 21 条）。"""
    code = _strip_comments(tex_text)
    used: list[str] = []
    for body, _ in _command_bodies(code, r"\mref"):
        key = body.strip()
        if key and key not in used:
            used.append(key)

    listed: list[str] = []
    # 用 `[^}]*` 而不是 `.+`：标记说明往往跨行写，而 `.+` 不跨行；同时 `[^}]*`
    # 正好停在包裹它的 `\spara{...}` 的右花括号处，不会把后面的正文吞进来。
    m = re.search(r"附图标记说明[：:]\s*([^}]*)", code)
    if m:
        for token in re.split(r"[；;，,、]", m.group(1)):
            token = token.strip()
            for dash in ("—", "-", "－", "–"):
                if dash in token:
                    token = token.split(dash)[0]
                    break
            token = token.strip()
            if token:
                listed.append(token)

    if not used:
        return True, r"正文没有使用 \mref{...}（无附图标记）— 跳过"
    missing = [k for k in used if k not in listed]
    if missing:
        return False, "附图标记 %s 用在了正文里，却没有列入「附图标记说明」" % "、".join(missing)
    extra = [k for k in listed if k not in used]
    if extra:
        return True, "附图标记 ✓（%d 个；说明里多出未使用的 %s，请确认是否要删）" % (
            len(used), "、".join(extra))
    return True, "附图标记 ✓（%d 个，正文与说明一致）" % len(used)


# 全部结构性检查的登记表：名字 → 函数。main 与 self_test 都用它遍历。
STRUCTURAL_CHECKS = (
    ("一级部件顺序", check_part_order),
    ("说明书五部分", check_spec_sections),
    ("版式", check_layout),
    ("权利要求", check_claims),
    ("摘要字数", check_abstract),
    ("措辞", check_forbidden_wording),
    ("附图标记", check_reference_signs),
)


def analyse_log(text: str) -> tuple[list[str], dict]:
    """解析 main.log，返回 (问题列表, 统计字典)。"""
    stats = {
        "hard_errors": [ln.strip() for ln in text.splitlines() if ln.startswith("!")],
        "undef_cites": _RE_UNDEF_CITE.findall(text),
        "undef_refs": _RE_UNDEF_REF.findall(text),
        "missing_fonts": _RE_MISSING_FONT.findall(text),
        "overfull": len(_RE_OVERFULL.findall(text)),
        "underfull": len(_RE_UNDERFULL.findall(text)),
        "pages": None,
        "pdf_bytes": None,
    }
    hit = _RE_OUTPUT.search(text)
    if hit:
        stats["pages"] = int(hit.group(2))
        if hit.group(3) is not None:
            stats["pdf_bytes"] = int(hit.group(3))

    problems: list[str] = []
    if stats["hard_errors"]:
        problems.append("硬错误 %d 条（首条：%s）"
                        % (len(stats["hard_errors"]), _short(stats["hard_errors"][0])))
    if stats["undef_cites"]:
        problems.append("未解析的 \\cite 共 %d 处（首处：%s）"
                        % (len(stats["undef_cites"]), _short(stats["undef_cites"][0])))
    if stats["undef_refs"]:
        problems.append("未解析的 \\ref 共 %d 处（首处：%s）"
                        % (len(stats["undef_refs"]), _short(stats["undef_refs"][0])))
    elif _RE_UNDEF_ANY.search(text):
        problems.append("日志声称存在未解析引用（There were undefined references）")
    if stats["missing_fonts"]:
        problems.append("字体装不上：%s" % _short(stats["missing_fonts"][0]))
    if _RE_EMERGENCY.search(text):
        problems.append("出现 Emergency stop / Fatal error")
    if _RE_RERUN.search(text):
        problems.append("交叉引用未收敛：跑完 3 遍日志里仍有 Rerun to get cross-references right")
    if stats["pages"] is None:
        problems.append("日志里没有 “Output written on main.pdf”——大概率没编译出 PDF")
    return problems, stats


def analyse_blg(text: str) -> list[str]:
    """解析 bibtex 的 .blg，返回真正算失败的问题列表。

    bibtex 对「注释里混进了条目类型」「字段名拼错」这类错误**不以 '!' 开头**，
    只在正文里写一行抱怨然后继续。所以只查 `_RE_BLG_FATAL` 会让 .bib 写坏了
    脚本还报绿——必须把 `_RE_BLG_ERROR` 也当成失败。
    """
    problems: list[str] = []
    fatal = _RE_BLG_FATAL.search(text)
    if fatal:
        problems.append("bibtex 打不开文件：%s" % _short(fatal.group(0)))
    for m in _RE_BLG_ERROR.finditer(text):
        problems.append("bibtex 报错：%s" % _short(text[m.start(): m.start() + 200], 160))
        if len(problems) >= 3:
            break
    return problems


def find_engines(tex_dir: str | None = None) -> dict[str, str | None]:
    """在 PATH（以及可选的 tex_dir）上找编译引擎。"""
    if tex_dir:
        os.environ["PATH"] = str(tex_dir) + os.pathsep + os.environ.get("PATH", "")
    return {name: shutil.which(name) for name in ("xelatex", "bibtex")}


def _run(cmd: list[str], cwd: pathlib.Path, timeout: int) -> subprocess.CompletedProcess:
    """跑一条命令，吞掉输出（判定完全靠 .log，不靠 stdout）。"""
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def run_template(tpl: dict, work_root: pathlib.Path, timeout: int,
                 keep_fontset: bool = False) -> dict:
    """编译一个模板并体检，返回结果字典。"""
    name = tpl["name"]
    src = LATEX_DIR / name
    work = work_root / name
    work.mkdir(parents=True, exist_ok=True)
    for fname in ("main.tex", "refs.bib"):
        shutil.copy(src / fname, work / fname)

    tex_path = work / "main.tex"
    tex_text = tex_path.read_text(encoding="utf-8")

    notes: list[str] = []
    if keep_fontset:
        notes.append("按 --keep-fontset 保留 %s，编译的就是仓库里那份原件" % FONTSET_FROM)
    else:
        patched, replaced = override_fontset_text(tex_text)
        if replaced:
            tex_path.write_text(patched, encoding="utf-8")
            notes.append("临时副本已把 %s 换成 %s（%d 处）"
                         % (FONTSET_FROM, FONTSET_TO, replaced))
        else:
            notes.append("模板里没有 %s，按原样编译" % FONTSET_FROM)

    # 结构性合规查的是**仓库原文**（tex_text），不是替换字体后的临时副本——
    # 字体替换只影响编译，不该影响「摘要多少字」这类判断。
    structural: list[tuple[str, bool, str]] = []
    for label, fn in STRUCTURAL_CHECKS:
        ok, msg = fn(tex_text)
        structural.append((label, ok, msg))

    engine = tpl["engine"]
    sequence = [[engine, "-interaction=nonstopmode", "main.tex"]]
    if tpl["needs_bibtex"]:
        sequence.append(["bibtex", "main"])
    sequence += [[engine, "-interaction=nonstopmode", "main.tex"],
                 [engine, "-interaction=nonstopmode", "main.tex"]]

    run_errors: list[str] = []
    for cmd in sequence:
        try:
            proc = _run(cmd, work, timeout)
        except subprocess.TimeoutExpired:
            run_errors.append("%s 超过 %ds 未结束" % (cmd[0], timeout))
            break
        except OSError as exc:
            run_errors.append("%s 无法启动：%s" % (cmd[0], exc))
            break
        if proc.returncode != 0 and cmd[0] != engine:
            # bibtex 的非零退出码通常是「没有 \citation 命令」这类噪音，
            # 真正的判据在 .blg 与最终的 PDF，所以这里只记录，不直接判失败。
            run_errors.append("%s 退出码 %d" % (cmd[0], proc.returncode))

    log_path = work / "main.log"
    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    problems, stats = analyse_log(log_text)

    if not log_text:
        problems.append("没有生成 main.log")

    problems.extend(run_errors)

    # bibtex 真的跑出结果了吗
    bbl = work / "main.bbl"
    if tpl["needs_bibtex"] and re.search(r"\\cite[a-z]*\{", tex_text):
        if not bbl.exists() or bbl.stat().st_size == 0:
            problems.append("有 \\cite 但没有生成 main.bbl——bibtex 没成功")
        blg = work / "main.blg"
        if blg.exists():
            problems.extend(analyse_blg(blg.read_text(encoding="utf-8", errors="replace")))

    pdf = work / "main.pdf"
    if not pdf.exists():
        problems.append("没有生成 main.pdf")
    else:
        # 体积以磁盘上的真实文件为准：MiKTeX 的日志里根本没有字节数
        stats["pdf_bytes"] = pdf.stat().st_size
        if stats["pdf_bytes"] < MIN_PDF_BYTES:
            problems.append("main.pdf 只有 %d 字节，不像是正常排版结果" % stats["pdf_bytes"])

    if stats["pages"] is not None and stats["pages"] < tpl["min_pages"]:
        problems.append("只有 %d 页，低于下限 %d 页——模板可能被改空了"
                        % (stats["pages"], tpl["min_pages"]))

    for label, ok, msg in structural:
        if not ok:
            problems.append("%s：%s" % (label, msg))

    return {
        "name": name,
        "engine": engine,
        "notes": notes,
        "structural": structural,
        "stats": stats,
        "problems": problems,
        "work": work,
        "ok": not problems,
    }


def self_test() -> int:
    """不装 TeX 也能跑的固件测试：日志解析 + 字体替换 + 7 条结构检查。"""
    checks: list[tuple[str, bool]] = []

    def check(label: str, cond: bool) -> None:
        checks.append((label, bool(cond)))

    # ---- 日志解析 ----
    clean = ("This is XeTeX\n"
             "Overfull \\hbox (2.0pt too wide) in paragraph at lines 10--11\n"
             "Output written on main.pdf (4 pages, 86995 bytes).\n")
    problems, stats = analyse_log(clean)
    check("干净日志：无问题", problems == [])
    check("干净日志：页数 4", stats["pages"] == 4)
    check("干净日志：字节数 86995", stats["pdf_bytes"] == 86995)
    check("干净日志：Overfull 只计数不判失败", stats["overfull"] == 1 and problems == [])

    # MiKTeX 的日志不写字节数，不能因此把编译成功判成失败
    miktex = "Output written on main.pdf (4 pages).\n"
    problems, stats = analyse_log(miktex)
    check("MiKTeX 风格日志：页数识别为 4", stats["pages"] == 4)
    check("MiKTeX 风格日志：字节数为 None 且不算问题",
          stats["pdf_bytes"] is None and problems == [])

    bad = "! LaTeX Error: File `booktabs.sty' not found.\n"
    problems, _ = analyse_log(bad)
    check("缺宏包被识别", any("硬错误" in p for p in problems))

    font = ('! Package fontspec Error: The font "SimSun" cannot be found.\n'
            "Output written on main.pdf (3 pages, 50000 bytes).\n")
    problems, stats = analyse_log(font)
    check("缺字体被识别", any("字体装不上" in p for p in problems))
    check("缺字体同时算硬错误", bool(stats["hard_errors"]))

    cite = ("LaTeX Warning: Citation `examplepatent' on page 2 undefined on input line 161.\n"
            "Output written on main.pdf (4 pages, 60000 bytes).\n")
    problems, stats = analyse_log(cite)
    check("未解析 \\cite 被识别", any("未解析" in p and "cite" in p for p in problems))
    check("未解析 \\cite 只算 1 处", len(stats["undef_cites"]) == 1)

    # 新版 LaTeX 用的是成对单引号，不能把引号风格写死进正则
    cite_new = "LaTeX Warning: Citation 'examplepatent' on page 2 undefined on input line 161.\n"
    _, stats = analyse_log(cite_new)
    check("未解析 \\cite（新引号风格）被识别", len(stats["undef_cites"]) == 1)

    ref = "LaTeX Warning: Reference `LastPage' on page 1 undefined on input line 82.\n"
    problems, stats = analyse_log(ref)
    check("未解析 \\ref 被识别", any("未解析" in p and "ref" in p for p in problems))
    check("未解析 \\ref 只算 1 处", len(stats["undef_refs"]) == 1)

    rerun = ("LaTeX Warning: Label(s) may have changed. Rerun to get cross-references right.\n"
             "Output written on main.pdf (4 pages, 60000 bytes).\n")
    problems, _ = analyse_log(rerun)
    check("交叉引用未收敛被识别", any("未收敛" in p for p in problems))

    nopdf = "! Emergency stop.\n"
    problems, stats = analyse_log(nopdf)
    check("没产出 PDF 被识别", stats["pages"] is None and len(problems) >= 2)

    # ---- bibtex 日志 ----
    blg_ok = ("This is BibTeX, Version 0.99e\nThe style file: unsrt.bst\n"
              "Database file #1: refs.bib\nYou've used 2 entries,\n")
    check("干净 .blg：无问题", analyse_blg(blg_ok) == [])

    blg_fatal = "I couldn't open database file refs.bib\n"
    check("打不开 .bib 被识别", any("打不开" in p for p in analyse_blg(blg_fatal)))

    # 这就是本仓库真实踩过的坑：注释里写了条目类型符号，bibtex 把它当成新条目
    blg_comment = ("You're missing an entry type---line 12 of file refs.bib\n"
                   "I'm skipping whatever remains of this entry\n")
    got = analyse_blg(blg_comment)
    check("注释里混进条目类型被识别（真实踩过的坑）",
          len(got) >= 1 and any("bibtex 报错" in p for p in got))

    # ---- 字体替换 ----
    patched, n = override_fontset_text(r"\documentclass[12pt,a4paper,fontset=windows]{ctexart}")
    check("字体替换生效", n == 1 and "fontset=fandol" in patched and FONTSET_FROM not in patched)
    _, n0 = override_fontset_text(r"\documentclass[12pt,a4paper]{ctexart}")
    check("没有 fontset=windows 时替换次数为 0", n0 == 0)

    # 模板文件头的注释里也写着 fontset=windows，那是说明文字，不该被替换
    commented = ("%  中文字体：ctexart + fontset=windows 直接调用宋体\n"
                 "\\documentclass[12pt,a4paper,fontset=windows]{ctexart}\n")
    patched, n = override_fontset_text(commented)
    check("只替换代码、不碰注释里的 fontset=windows", n == 1)
    check("注释里的字眼原样保留",
          "%  中文字体：ctexart + fontset=windows 直接调用宋体" in patched)

    # ---- 花括号配对 ----
    body, close = _balanced_group("{a \\mref{1} b}", 0)
    check("嵌套花括号配对", body == "a \\mref{1} b" and close == len("{a \\mref{1} b}") - 1)
    _, close = _balanced_group("{a {b}", 0)
    check("括号不配对返回 -1", close == -1)
    body, _ = _balanced_group("{a \\{ b}", 0)
    check("转义花括号不参与配对", body == "a \\{ b")

    # ---- 一级部件顺序 ----
    good_parts = (
        "\\parttitle{说明书摘要}\n\\npara{摘要}\n"
        "\\parttitle{权利要求书}\n\\claim{1}{一种装置}\n"
        "\\parttitle{说明书}\n\\sect{技术领域}\n"
        "\\parttitle{说明书附图}\n"
        "\\bibliography{refs}\n")
    ok, _ = check_part_order(good_parts)
    check("一级部件顺序正确 → 通过", ok)

    swapped = good_parts.replace("\\parttitle{说明书摘要}", "\\parttitle{ZZZ}").replace(
        "\\parttitle{权利要求书}", "\\parttitle{说明书摘要}").replace(
        "\\parttitle{ZZZ}", "\\parttitle{权利要求书}")
    ok, msg = check_part_order(swapped)
    check("摘要排在权利要求书之后 → 失败", (not ok) and "顺序" in msg)

    missing = good_parts.replace("\\parttitle{说明书附图}\n", "")
    ok, msg = check_part_order(missing)
    check("缺一级部件被识别", (not ok) and "缺少" in msg)

    ok, msg = check_part_order(good_parts.replace("\\bibliography{refs}\n", ""))
    check("没有 \\bibliography 被识别", (not ok) and "找不到" in msg)

    # 注释里出现 \parttitle 不能算数
    ok, _ = check_part_order("% \\parttitle{说明书附图} 是后面才有的\n" + good_parts)
    check("顺序检查忽略注释里的 \\parttitle", ok)

    # ---- 说明书五部分 ----
    good_spec = "".join("\\sect{%s}\n" % s for s in SPEC_SECTIONS)
    ok, _ = check_spec_sections(good_spec)
    check("说明书五部分顺序正确 → 通过", ok)

    reordered = "".join("\\sect{%s}\n" % s
                        for s in ("技术领域", "发明内容", "背景技术",
                                  "附图说明", "具体实施方式"))
    ok, msg = check_spec_sections(reordered)
    check("背景技术排到发明内容之后 → 失败", (not ok) and "顺序" in msg)

    ok, msg = check_spec_sections(good_spec.replace("\\sect{附图说明}\n", ""))
    check("缺说明书某部分被识别", (not ok) and "缺少" in msg)

    # ---- 版式 ----
    good_geo = ("\\documentclass[12pt,a4paper,fontset=windows]{ctexart}\n"
                "\\usepackage[top=25mm,bottom=15mm,left=25mm,right=15mm]{geometry}\n")
    ok, _ = check_layout(good_geo)
    check("版式正确 → 通过", ok)

    ok, msg = check_layout(good_geo.replace("left=25mm", "left=20mm"))
    check("左边距不对被识别", (not ok) and "left" in msg)

    ok, msg = check_layout(good_geo.replace("a4paper,", ""))
    check("缺 a4paper 被识别", (not ok) and "a4paper" in msg)

    ok, msg = check_layout("\\documentclass{ctexart}\n")
    check("缺 geometry 被识别", (not ok) and "geometry" in msg)

    # 注释里写的 geometry 不能算数
    ok, _ = check_layout("% \\usepackage[top=1mm,left=1mm,right=1mm,bottom=1mm]{geometry}\n"
                         + good_geo)
    check("版式检查忽略注释", ok)

    # ---- 权利要求 ----
    claims = ("\\claim{1}{一种装置，其特征在于，包括壳体。}\n"
              "\\claim{2}{根据权利要求 1 所述的装置，其特征在于，所述壳体为金属。}\n"
              "\\claim{3}{根据权利要求 1 或 2 所述的装置，其特征在于，还包括散热孔。}\n")
    ok, msg = check_claims(claims)
    check("权利要求编号与引用正确 → 通过", ok)
    check("独立权利要求计数为 1", "独立 1 项" in msg)

    ok, msg = check_claims(claims.replace("\\claim{2}", "\\claim{5}"))
    check("编号不连续被识别", (not ok) and "连续" in msg)

    ok, msg = check_claims("\\claim{1}{根据权利要求 2 所述的装置。}\n"
                           "\\claim{2}{一种装置。}\n")
    check("引用在后的权利要求被识别", (not ok) and "之后" in msg)

    ok, msg = check_claims("\\claim{1}{根据权利要求 7 所述的装置。}\n")
    check("引用不存在的权利要求被识别", (not ok) and "不存在" in msg)

    ok, msg = check_claims("\\claim{1}{根据权利要求 1 所述的装置。}\n")
    check("没有独立权利要求被识别", (not ok) and "独立" in msg)

    ok, msg = check_claims("\\parttitle{权利要求书}\n")
    check("没有权利要求被识别", (not ok) and "没有找到" in msg)

    # 多项从属的「1 至 3」范围写法要展开成 1,2,3
    check("范围引用展开", referenced_claims("根据权利要求 1 至 3 中任一项所述的装置")
          == [1, 2, 3])
    check("「或」连接的多个引用展开", referenced_claims("根据权利要求 1 或 2 所述的装置")
          == [1, 2])

    # 权利要求正文里嵌套 \mref{...} 时，解析不能被花括号带偏
    nested = "\\claim{4}{根据权利要求 1 所述的装置\\mref{1}，其特征在于，壳体\\mref{1}。}\n"
    parsed = parse_claims(nested)
    check("嵌套 \\mref 不影响权利要求解析", len(parsed) == 1 and parsed[0][0] == 4)
    check("嵌套 \\mref 的正文被完整取出", "壳体" in parsed[0][1])

    # 「每一项权利要求只允许在其结尾处使用句号」（指南第二部分第二章 3.3）
    two_stops = "\\claim{1}{一种装置。其特征在于，包括壳体。}\n"
    ok, msg = check_claims(two_stops)
    check("权利要求中间出现句号被识别", (not ok) and "句号" in msg)

    no_stop = "\\claim{1}{一种装置，其特征在于，包括壳体}\n"
    ok, msg = check_claims(no_stop)
    check("权利要求结尾没有句号被识别", (not ok) and "结尾没有句号" in msg)

    # 分号、冒号不算句号；正文里的 \mref 参数不会带来假的句号
    semis = "\\claim{1}{一种装置，包括：壳体\\mref{1}；销\\mref{11}。}\n"
    ok, msg = check_claims(semis)
    check("逗号分号冒号不触发句号检查", ok)

    # ---- 摘要字数 ----
    short = "\\parttitle{说明书摘要}\n\\npara{%s}\n\\parttitle{权利要求书}\n" % ("字" * 100)
    ok, msg = check_abstract(short)
    check("短摘要通过", ok and "100 字" in msg)

    over = "\\parttitle{说明书摘要}\n\\npara{%s}\n\\parttitle{权利要求书}\n" % ("字" * 301)
    ok, msg = check_abstract(over)
    check("超 300 字的摘要被识别", (not ok) and "超过上限" in msg)

    warn = "\\parttitle{说明书摘要}\n\\npara{%s}\n\\parttitle{权利要求书}\n" % ("字" * 285)
    ok, msg = check_abstract(warn)
    check("接近上限给出预警但仍通过", ok and "接近上限" in msg)

    ok, msg = check_abstract("\\parttitle{说明书摘要}\n\\parttitle{权利要求书}\n")
    check("找不到摘要正文被识别", (not ok) and "没找到" in msg)

    check("latex_to_plain 把 \\mref 还原成可见的（1）",
          latex_to_plain(r"\noindent 甲 \mref{1} 乙") == "甲（1）乙")

    # 摘要是 \npara 还是 \spara 都要能取到
    spara_abs = "\\parttitle{说明书摘要}\n\\spara{%s}\n\\parttitle{权利要求书}\n" % ("字" * 50)
    ok, msg = check_abstract(spara_abs)
    check("摘要写成 \\spara 也能取到", ok and "50 字" in msg)

    # ---- 措辞 ----
    wording_ok = ("\\parttitle{说明书摘要}\n\\npara{一种装置。}\n"
                  "\\parttitle{权利要求书}\n\\claim{1}{一种装置\\mref{1}。}\n"
                  "\\parttitle{说明书}\n\\sect{具体实施方式}\n参见图 1。\n")
    ok, _ = check_forbidden_wording(wording_ok)
    check("措辞正确 → 通过", ok)

    bad_ref = wording_ok.replace("一种装置\\mref{1}。",
                                 "如说明书第一段所述的装置\\mref{1}。")
    ok, msg = check_forbidden_wording(bad_ref)
    check("权利要求里「如说明书……所述」被识别", (not ok) and "如说明书" in msg)

    bad_fig = wording_ok.replace("一种装置\\mref{1}。", "如图 1 所示的装置\\mref{1}。")
    ok, msg = check_forbidden_wording(bad_fig)
    check("权利要求里「如图……所示」被识别", (not ok) and "如图" in msg)

    bad_spec = wording_ok.replace("参见图 1。", "如权利要求 1 所述的装置，其壳体为金属。")
    ok, msg = check_forbidden_wording(bad_spec)
    check("说明书里「如权利要求……所述」被识别", (not ok) and "如权利要求" in msg)

    bad_abs = wording_ok.replace("一种装置。}", "如权利要求 1 所述的装置。}")
    ok, msg = check_forbidden_wording(bad_abs)
    check("摘要里「如权利要求……所述」被识别", (not ok) and "摘要" in msg)

    bad_ads = wording_ok.replace("一种装置。}", "一种世界领先的装置。}")
    ok, msg = check_forbidden_wording(bad_ads)
    check("商业性宣传用语被识别", (not ok) and "宣传用语" in msg)

    # 注释里写「如权利要求……所述」是在讲规则，不能算违规
    ok, _ = check_forbidden_wording("% 摘要不得写「如权利要求……所述」这类引用语\n"
                                    + wording_ok)
    check("措辞检查忽略注释", ok)

    # ---- 附图标记 ----
    signs_ok = ("\\claim{1}{壳体\\mref{1}与销\\mref{11}。}\n"
                "\\sect{附图说明}\n附图标记说明：1—壳体；11—销。\n")
    ok, msg = check_reference_signs(signs_ok)
    check("附图标记一致 → 通过", ok and "2 个" in msg)

    signs_missing = "\\claim{1}{壳体\\mref{1}与销\\mref{11}。}\n附图标记说明：1—壳体。\n"
    ok, msg = check_reference_signs(signs_missing)
    check("正文用了但说明里没列的标记被识别", (not ok) and "11" in msg)

    # 标记说明跨行写是常态（模板里就是跨行的），解析不能只看第一行
    signs_wrapped = ("\\claim{1}{壳体\\mref{4}与片\\mref{41}。}\n"
                     "\\spara{附图标记说明：1—壳体；11—销；\n"
                     "4—离合组件；41—摩擦片。}\n")
    ok, msg = check_reference_signs(signs_wrapped)
    check("跨行的附图标记说明能被完整解析", ok and "2 个" in msg)

    # 右花括号之后的正文不能被当成标记说明的一部分
    signs_trailing = ("\\claim{1}{壳体\\mref{1}。}\n"
                      "\\spara{附图标记说明：1—壳体。}\n"
                      "\\spara{下面讲实施例，标记 7 出现在这里。}\n")
    ok, msg = check_reference_signs(signs_trailing)
    check("标记说明不会吞掉后面的正文", ok)

    ok, msg = check_reference_signs("\\claim{1}{一种装置。}\n")
    check("没有附图标记时跳过", ok and "跳过" in msg)

    signs_extra = "\\claim{1}{壳体\\mref{1}。}\n附图标记说明：1—壳体；11—销。\n"
    ok, msg = check_reference_signs(signs_extra)
    check("说明里多出未使用的标记只提示不失败", ok and "未使用" in msg)

    # ---- 登记表本身 ----
    check("结构检查登记表覆盖 7 项", len(STRUCTURAL_CHECKS) == 7)
    check("模板登记表只有一个 cnipa", [t["name"] for t in TEMPLATES] == ["cnipa"])

    failed = [label for label, good in checks if not good]
    for label, good in checks:
        print("  %s %s" % ("PASS" if good else "FAIL", label))
    print()
    print("self-test: %d/%d 通过" % (len(checks) - len(failed), len(checks)))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="编译 assets/latex/cnipa 模板并体检（不改动仓库文件）")
    parser.add_argument("--only", action="append", metavar="NAME",
                        help="只测指定模板，可重复（当前只有 cnipa）")
    parser.add_argument("--require", action="store_true",
                        help="找不到编译引擎时判为失败（CI 用）；默认是跳过")
    parser.add_argument("--keep", action="store_true",
                        help="保留临时编译目录，便于翻 .log / .pdf")
    parser.add_argument("--keep-fontset", action="store_true",
                        help="不做字体集替换，编译仓库里的原件（只有装 Windows 字体时才可能成功）")
    parser.add_argument("--tex-dir", metavar="DIR",
                        help="把该目录加到 PATH 最前面（例如本机 MiKTeX 的 bin 目录）")
    parser.add_argument("--timeout", type=int, default=300,
                        help="单条命令的超时秒数（默认 300）")
    parser.add_argument("--self-test", action="store_true",
                        help="只跑解析逻辑的固件测试，不需要装 TeX")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    selected = list(TEMPLATES)
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {t["name"] for t in TEMPLATES}
        if unknown:
            print("未知模板：%s（可选 %s）"
                  % (", ".join(sorted(unknown)), " / ".join(t["name"] for t in TEMPLATES)))
            return 1
        selected = [t for t in TEMPLATES if t["name"] in wanted]

    engines = find_engines(args.tex_dir)
    needed = sorted({t["engine"] for t in selected} | ({"bibtex"} if any(
        t["needs_bibtex"] for t in selected) else set()))
    missing = [e for e in needed if not engines.get(e)]

    if missing:
        print("找不到编译引擎：%s" % ", ".join(missing))
        print("本机没装 TeX 发行版（或没把它加进 PATH）。")
        print("  · 想现在就体检：装 TeX Live / MiKTeX，或用 --tex-dir 指向 bin 目录；")
        print("  · 只想跑逻辑自测：python scripts/check_latex.py --self-test")
        return 1 if args.require else 0

    work_root = pathlib.Path(tempfile.mkdtemp(prefix="cnipalatex_"))
    print("临时编译目录：%s" % work_root)
    print("引擎：%s" % "，".join("%s=%s" % (e, engines[e]) for e in needed))
    print()

    results = []
    try:
        for tpl in selected:
            print("=" * 70)
            print("编译 %s（%s）…" % (tpl["name"], tpl["engine"]))
            result = run_template(tpl, work_root, args.timeout, args.keep_fontset)
            results.append(result)
            stats = result["stats"]
            print("  页数=%s  PDF字节=%s  Overfull=%d  Underfull=%d"
                  % (stats["pages"], stats["pdf_bytes"], stats["overfull"], stats["underfull"]))
            for note in result["notes"]:
                print("  · %s" % note)
            for label, ok, msg in result["structural"]:
                print("  %s %s：%s" % ("✓" if ok else "✗", label, msg))
            # 结构性失败已经在上面的逐条清单里打过一次了，这里不再重复；
            # 只打「编译/产出」层面的问题。
            shown = {"%s：%s" % (label, msg)
                     for label, ok, msg in result["structural"] if not ok}
            for problem in result["problems"]:
                if problem not in shown:
                    print("  ✗ %s" % problem)
            if result["ok"]:
                print("  ✓ 通过")
    finally:
        if args.keep:
            print()
            print("已保留临时目录：%s" % work_root)
        else:
            shutil.rmtree(work_root, ignore_errors=True)

    print()
    print("=" * 70)
    bad = [r for r in results if not r["ok"]]
    for r in results:
        print("%-6s %-9s %s" % (r["name"], r["engine"],
                                "OK" if r["ok"] else "FAIL：%s" % _short(r["problems"][0], 80)))
    print()
    print("LaTeX 模板：%d/%d 通过" % (len(results) - len(bad), len(results)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
