#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把一份申请文件草稿一键打成整套**可提交的格式**（纯标准库）。

用法::

    python scripts/build_all.py 申请文件.md                  # 输出到 <草稿名>-out/
    python scripts/build_all.py 申请文件.md -o 提交包/
    python scripts/build_all.py 申请文件.md --pdf            # 装了 TeX 就顺带编一份 PDF
    python scripts/build_all.py --self-test                  # 固件测试

产出（都在 `-o` 目录里）::

    01-说明书摘要.docx   02-权利要求书.docx   03-说明书.docx   04-说明书附图.docx
    src/说明书摘要.md    src/权利要求书.md    src/说明书.md    src/说明书附图.md
    latex/main.tex       latex/refs.bib       （CNIPA 版式，可直接 xelatex 编译）
    00-全文（草稿）.pdf                        （仅 --pdf，且本机装了 xelatex/bibtex）
    自检报告.txt         自检报告.json         manifest.json（逐文件 SHA-256）

**为什么按四个部件各出一份 .docx**：国知局的电子申请是**按部件分别上传**的
（说明书摘要 / 权利要求书 / 说明书 / 说明书附图），交一份合订本反而不合要求。
所以拆件不是排版偏好，而是提交形态本身。

三条设计原则（与 `SKILL.md` 的三条铁律、`gen_draft.py` 对齐）:

1. **不另立一套合规规则**。草稿自检直接调 `check_patent.run_checks`；LaTeX 版式
   自检直接调 `check_latex.STRUCTURAL_CHECKS`；本脚本只做拆分、套版式和打包。
   机械检查的结论以那两个脚本为准，本脚本一个字都不重复实现。
2. **能报的错都要报出来**。`check_patent` 的 fail 级发现、LaTeX 结构自检的失败、
   素材缺失（没有附图、没有附图标记说明、引证文件为空）都会写进 `自检报告.txt`
   并影响退出码；不静默补、不静默丢。
3. **产物可复现**。不写时间戳、不写绝对路径、不写机器名；`.docx` 沿用
   `make_docx` 的固定时间戳，逐字节可复现；文本产物统一用 `\\n` 换行（Windows
   上也不会变成 `\\r\\n`）。因此**同一份草稿用同样参数、同样输出目录名打包，
   在任何机器上逐字节一致**。
   唯一的例外是 `--pdf` 产出的 PDF：xelatex 会往 PDF 里写生成时间与文件 ID，
   同一份 `.tex` 两次编译的字节不同——这与本脚本无关，`自检报告.txt` 里的
   PDF 哈希因此每次都会变，其余产物不受影响。

本脚本只处理**发明专利**。实用新型必须有附图、外观设计不写权利要求书，
两者的部件拆分与检查项都不同，不在本脚本的覆盖范围内。

参考:
    `references/format.md`（版式与摘要）、`references/procedure.md`（申请流程与
    电子申请）、`references/drawings.md`（附图）、`assets/latex/cnipa/main.tex`
    （版式与宏的来源）。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_latex  # noqa: E402  （同目录脚本，CI 的白名单允许同目录 import）
import check_patent  # noqa: E402
import make_docx  # noqa: E402

VERSION = "1.0.0"

OUT_DIR_SUFFIX = "-out"

# 四个法定部件 → 输出文件名前缀。顺序就是《专利法实施细则》要求的提交顺序，
# 也是电子申请里四个部件各自的文件。
PART_FILES: tuple[tuple[str, str], ...] = (
    (check_patent.PART_ABSTRACT, "01-说明书摘要"),
    (check_patent.PART_CLAIMS, "02-权利要求书"),
    (check_patent.PART_SPEC, "03-说明书"),
    (check_patent.PART_FIGURES, "04-说明书附图"),
)

SRC_DIR = "src"
TEX_DIR = "latex"
REPORT_TXT = "自检报告.txt"
REPORT_JSON = "自检报告.json"
MANIFEST = "manifest.json"
PDF_NAME = "00-全文（草稿）.pdf"

# 少了这两个部件就谈不上「按部件打包」：直接拒绝，而不是硬凑一份出来。
REQUIRED_PARTS: tuple[str, ...] = (check_patent.PART_CLAIMS, check_patent.PART_SPEC)

# LaTeX 结构自检里**允许**失败的两种情形：(检查项, 允许缺席的对象, 失败信息特征)。
# 两者的法律依据相同——「没有附图」时，说明书里不必有「附图说明」部分，申请文件
# 里也不必单列「说明书附图」页（references/format.md §4）。除此之外的结构性失败
# 一律计入退出码，不做任何豁免。
LATEX_EXEMPT: tuple[tuple[str, str, str], ...] = (
    ("一级部件顺序", check_patent.PART_FIGURES, "缺少一级部件"),
    ("说明书五部分", "附图说明", "缺少法定部分标题"),
)

RE_BOLD_ONLY = re.compile(r"^\*\*(.+)\*\*$")
RE_FIGURE_LINE = re.compile(r"^\s*图\s*(\d+)\s*[　\s]*(\S.*?)\s*$")
RE_COMMENT = re.compile(r"<!--.*?-->", re.S)

# LaTeX 特殊字符 → 转义写法。用查表**单趟**替换，不能用一串 str.replace：
# `\\` 换成 `\\textbackslash{}` 之后如果再跑花括号那一趟，就会把新生成的
# `{}` 又转义掉，结果变成 `\\textbackslash\{\}`。
LATEX_ESCAPE: dict[str, str] = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "%": r"\%",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

# LaTeX 版式头。与 assets/latex/cnipa/main.tex 同源（同一套宏、同一组 geometry），
# 但刻意**内嵌**而不是去读那个文件：build_all.py 要能被单独复制到任何地方用。
# 自测里有校验兜住二者关键参数不漂移（A4、四个页边距、fontset、宏齐全）。
LATEX_PREAMBLE = r"""% !TeX program = xelatex
% =============================================================================
% 中国发明专利申请文件（草稿）——由 scripts/build_all.py 生成
% =============================================================================
%
% 编译链（四步，缺一不可）：
%   xelatex -interaction=nonstopmode main.tex
%   bibtex  main
%   xelatex -interaction=nonstopmode main.tex
%   xelatex -interaction=nonstopmode main.tex
%
% 为什么必须 XeLaTeX：中文要调用系统字体，pdfLaTeX 编 ctex 会直接报错。
% 为什么必须跑两遍收尾：参考文献、交叉引用、页码都要第二遍才收敛。
%
% 这份 main.tex 是**工作稿**：宏与版式参数与仓库里的 assets/latex/cnipa/main.tex
% 同源。版式的强制性依据在《专利审查指南》第五部分第一章 4.1–5.6，
% 逐条对应见 references/format.md；正式提交前请用 CNIPA 官方渠道核对，
% 并建议交由执业代理师复核。
%
% ★ 电子申请的一个坑：官方《关于规范提交专利电子申请的指引》明确要求
%   「说明书不应添加任何形式的段落编号」——新申请 XML 的说明书段号由系统
%   自动生成。下面的 [0001] 段号是**草稿阶段**方便按段答复审查意见用的，
%   正式提交前必须把 \paranumbertrue 改成 \paranumberfalse（生成时加
%   --no-para-number 也可以）。
% =============================================================================

\documentclass[12pt,a4paper,fontset=windows]{ctexart}

% ---- 版式 -------------------------------------------------------------------
\usepackage[top=25mm,bottom=15mm,left=25mm,right=15mm,headsep=5mm,footskip=8mm]{geometry}
\usepackage{fancyhdr}
\usepackage{lastpage}

% ---- 数学、表格、图形 -------------------------------------------------------
\usepackage{amsmath,amssymb}
\usepackage{booktabs}
\usepackage{array}
\usepackage{graphicx}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}

% ---- 段落编号 [0001] --------------------------------------------------------
% 说明书段落编号不是提交时必须的，但**答复审查意见时按段引用**极为方便，
% 也是公开文本（CN…A）的编号方式。
% 开关：草稿默认开；正式提交（尤其电子申请）务必改成 \paranumberfalse。
\newif\ifparanumber
\paranumbertrue
\newcounter{specpara}
\newcommand{\padfour}[1]{%
  \ifnum#1<10 [000#1]\else
  \ifnum#1<100 [00#1]\else
  \ifnum#1<1000 [0#1]\else
  [#1]\fi\fi\fi}
% 注意：这里必须用 \thespecpara，不能写 \value{specpara}——
% \ifnum 后面跟 \value{...} 会报 "Missing number, treated as zero"（已实测）。
\newcommand{\spara}[1]{\par\refstepcounter{specpara}%
  \ifparanumber\noindent\padfour{\thespecpara}\ \fi
  #1\par}
% 不带编号的段落（用于摘要、发明内容里的小标题下正文等）
\newcommand{\npara}[1]{\par\noindent #1\par}

% ---- 结构宏 ----------------------------------------------------------------
% 一级部件标题（说明书摘要 / 权利要求书 / 说明书）：三号黑体居中
\newcommand{\parttitle}[1]{%
  \par\vspace{0.6em}%
  \begin{center}{\zihao{3}\heiti #1}\end{center}%
  \vspace{0.3em}\par}
% 说明书内部的法定小节标题（技术领域 / 背景技术 / ……）：小四黑体顶格
\newcommand{\sect}[1]{\par\vspace{0.5em}\noindent{\zihao{-4}\heiti #1}\par\vspace{0.2em}}
% 权利要求：编号 + 正文，编号顶格
\newcommand{\claim}[2]{\par\noindent #1. #2\par}
% 附图标记：权利要求与摘要里必须带括号，正文里反过来不带（references/drawings.md §3）
\newcommand{\mref}[1]{（#1）}
% 引用对比文件（正文里用 \cmpfile{键名}，条目写在 refs.bib）
\newcommand{\cmpfile}[1]{\cite{#1}}

% ---- 页眉页脚 --------------------------------------------------------------
% 申请文件正文一般不设页眉；页码保留在页脚居中，便于校对与答复时指页。
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0pt}
\cfoot{\small 第 \thepage\ 页\quad 共 \pageref{LastPage} 页}

% ---- 元信息（草稿用，正式提交的请求书另外填写） -----------------------------
\title{\drafttitle}
\date{\today}
"""

# refs.bib 全部写成注释：默认没有引证文件时 bibtex 不会因为空库报错，
# 需要引证时把条目取消注释即可（背景技术里引证过的文件必须在此可查）。
REFS_BIB = """% refs.bib —— 背景技术里引证过的对比文件 / 文献条目。
%
% 正文里用 \\cmpfile{键名} 引用（宏定义见 main.tex），按 unsrt 样式编号。
% 正式提交时每条都要写全公开号 / 文献出处；下面是一条格式示例，用完请删掉注释
% 或替换成真实条目。注意《专利审查指南》要求引证的是**已公开**的文件。
%
% @article{sample2020,
%   author  = {张三},
%   title   = {示例文献标题},
%   journal = {示例期刊},
%   year    = {2020},
%   volume  = {40},
%   number  = {3},
%   pages   = {1--8},
% }
%
% @patent{samplecn,
%   author  = {李四},
%   title   = {一种示例装置},
%   number  = {CN1234567A},
%   year    = {2019},
% }
"""

SELF_TEST_TOTAL = 103


# ---------------------------------------------------------------------------
# 纯函数：文本 → 各部件文本 / LaTeX
# ---------------------------------------------------------------------------


def latex_escape(text: str) -> str:
    """把普通文本转成能安全放进 LaTeX 参数里的文本。

    参数:
        text: 任意文本（可能含 `%`、`_`、`&` 这些 LaTeX 特殊字符）。

    返回:
        转义后的文本。

    算法:
        查表**单趟**逐字符替换。

    复杂度:
        O(n)。

    陷阱:
        不能写成「先换反斜杠再换花括号」的一串 `str.replace`：反斜杠换出来的
        `\\textbackslash{}` 会被后面那一趟再转义一次，得到一个编译不过的
        `\\textbackslash\\{\\}`。单趟查表从根上避开这个顺序依赖。
    """
    return "".join(LATEX_ESCAPE.get(ch, ch) for ch in text)


def latex_inline(text: str, signs: set[str]) -> tuple[str, list[str]]:
    """正文 → LaTeX 片段：**已登记**的附图标记换成 `\\mref{N}`，其余字符转义。

    参数:
        text: 一段正文（可能含 `（2）` 这样的附图标记）。
        signs: 已在「附图标记说明」里登记过的标记号集合。

    返回:
        `(LaTeX 片段, 未登记却被当成标记用到的号列表)`。

    算法:
        用 `check_patent.RE_SIGN_INLINE` 定位括号里的号；号在 `signs` 里才替换成
        `\\mref{}`，否则原样留给转义那一趟。

    复杂度:
        O(n)。

    陷阱:
        不能把所有 `（数字）` 都换成 `\\mref{}`：`（2020）` 是年份、`（3×3）`
        是算式，而且 `check_patent` 已经把「正文用了却没登记的标记」报成 fail
        了——这里再硬写成 `\\mref` 只会把同一处错误挪个地方藏起来。
    """
    out: list[str] = []
    unknown: list[str] = []
    last = 0
    for m in check_patent.RE_SIGN_INLINE.finditer(text):
        num = m.group(1)
        if num not in signs:
            unknown.append(num)
            continue                      # 不动 last：这段原文留给最后整体转义
        out.append(latex_escape(text[last:m.start()]))
        out.append(r"\mref{%s}" % num)
        last = m.end()
    out.append(latex_escape(text[last:]))
    return "".join(out), unknown


def spec_order(data: dict) -> list[str]:
    """取草稿里说明书小节的**出现顺序**（去重，只留法定小节的第一次出现）。

    参数:
        data: `check_patent.parse_draft` 的结果。

    返回:
        小节名列表。

    复杂度:
        O(n)。

    陷阱:
        出现重复标题时 `parse_draft` 只会保留**最后一版正文**（字典覆盖），
        所以调用方必须把重复这件事报出来，不能让前一版悄悄消失。
    """
    out: list[str] = []
    for name in data["spec_order"]:
        if name in check_patent.SPEC_SECTIONS and name not in out:
            out.append(name)
    return out


def preflight_notes(data: dict) -> list[str]:
    """生成前就能断定的、必须让用户看到的事实。"""
    notes: list[str] = []
    order = spec_order(data)
    dup = len(data["spec_order"]) - len(order)
    if dup > 0:
        notes.append("说明书里有 %d 处重复的小节标题；同名小节只保留了**最后一版**"
                     "正文，请人工确认前面那版没有丢内容。" % dup)
    missing = [s for s in check_patent.SPEC_SECTIONS
               if s not in order and s not in check_patent.OPTIONAL_SECTIONS]
    if missing:
        notes.append("说明书缺法定小节：%s（按草稿原样输出，不代补）。" % "、".join(missing))
    if not order:
        notes.append("草稿里没有识别到任何说明书小节标题（技术领域 / 背景技术 / …）。")
    if check_patent.PART_FIGURES not in data["parts"]:
        notes.append("草稿没有「说明书附图」部分：产出的 LaTeX 里也就没有附图页，"
                     "需要附图时请补齐并同步更新说明书里的附图说明。")
    if not data["signs_listed"]:
        notes.append("草稿里没有「附图标记说明」：附图中的标记与正文对不上是形式缺陷"
                     "（《专利法实施细则》第 21 条），有附图时必须补。")
    return notes


def section_text(section: str, body: str) -> str:
    """小节正文 → 单部件 Markdown 里的正文。

    参数:
        section: 小节名。
        body: `parse_draft` 切出来的小节正文。

    返回:
        规整后的正文。

    算法:
        「附图说明」按行拆段（中间插空行），其余原样。

    复杂度:
        O(n)。

    陷阱:
        附图说明在草稿里常写成连续几行「图 1 是……；图 2 是……；」，而它本来
        **一图一段**。不拆的话 Word 里会挤成一整坨，与 CNIPA 的常规版式不符。
    """
    body = body.strip()
    if section == "附图说明":
        return "\n\n".join(ln.strip() for ln in body.split("\n") if ln.strip())
    return body


def paragraphs(body: str) -> list[str]:
    """把一段正文按空行切成段落，段内的软换行按中/西文规则拼回去。

    参数:
        body: 小节正文。

    返回:
        段落列表。

    算法:
        先按空行切块，再用 `make_docx._is_cjk_joinable` 判断段内换行该不该补空格
        ——**故意复用** `make_docx` 的那条规则，好让 .md / .docx / .tex 三种产物
        对同一份草稿的断句完全一致。

    复杂度:
        O(n)。
    """
    out: list[str] = []
    for block in re.split(r"\n\s*\n", body.replace("\r\n", "\n")):
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue
        out.append("".join(lines) if make_docx._is_cjk_joinable(lines) else " ".join(lines))
    return out


def part_markdown(data: dict, part: str) -> str | None:
    """把解析结果还原成**单个部件**的 Markdown。

    参数:
        data: `check_patent.parse_draft` 的结果。
        part: 部件名（`check_patent.PART_*`）。

    返回:
        该部件的 Markdown；草稿里没有这个部件时返回 None。

    算法:
        摘要/附图直接取原文并把部件标题作为一级标题补上；权利要求书按
        `编号. 正文` 一行一项；说明书加发明名称居中标题后逐小节展开。

    复杂度:
        O(n)。

    陷阱:
        必须是**单部件**：分开的 .docx 是要分别上传的，把四个部件混在一份文件里
        就等于交了一份合订本。
    """
    if part == check_patent.PART_ABSTRACT:
        raw = data["parts"].get(part, "").strip()
        if not raw:
            return None
        return "# %s\n\n%s\n" % (part, raw)
    if part == check_patent.PART_CLAIMS:
        if not data["claims"]:
            return None
        chunks = ["# %s\n" % part]
        for num, body in data["claims"]:
            chunks.append("\n%d. %s\n" % (num, body))
        return "".join(chunks)
    if part == check_patent.PART_SPEC:
        order = spec_order(data)
        if not order:
            return None
        # 指南第一部分第一章 4.2：说明书第一页第一行写明发明名称，与请求书一致，
        # 左右居中，前面不得冠以「发明名称」等字样。
        chunks = ["# %s\n" % (data["name"] or "（草稿首行没有写发明名称）")]
        for name in order:
            body = section_text(name, data["spec_sections"].get(name, ""))
            chunks.append("\n## %s\n\n%s\n" % (name, body.rstrip("\n")))
        return "".join(chunks)
    if part == check_patent.PART_FIGURES:
        raw = data["parts"].get(part, "").strip()
        if not raw:
            return None
        lines = [ln.strip() for ln in raw.split("\n") if ln.strip()]
        return "# %s\n\n%s\n" % (part, "\n\n".join(lines))
    return None


def build_latex(data: dict, *, para_number: bool = True) -> tuple[str, list[str]]:
    """按 CNIPA 版式把草稿组装成一份完整的 `main.tex`。

    参数:
        data: `check_patent.parse_draft` 的结果。
        para_number: 说明书段落是否带 `[0001]` 编号（草稿默认带）。

    返回:
        `(main.tex 文本, 生成说明列表)`。

    算法:
        按法定部件顺序拼：摘要 → 权利要求书 → 说明书 → 说明书附图 → 引证文件。
        说明书小节按**草稿里的顺序**输出（不做重排）——顺序不对是草稿的问题，
        要让 `check_latex` 的结构自检把它照出来，而不是在这里抹平。

    复杂度:
        O(n)。

    陷阱:
        `\\mref{}` 只给登记过的标记；正文里的 `（2）` 若没登记就保持原样，
        由 `check_patent` 报 fail。两个脚本对同一件事的判断必须一致。
    """
    notes: list[str] = []
    name = data["name"] or "（草稿首行没有写发明名称）"
    signs = set(data["signs_listed"])
    unknown: list[str] = []

    body: list[str] = []
    body.append(r"\begin{document}")
    body.append("")
    body.append("% 草稿题头：标明这是工作稿，正式提交时删掉这一段")
    body.append(r"\begin{center}")
    body.append(r"  {\zihao{-4}\heiti 发明专利申请文件（草稿）}\\[0.3em]")
    # 这行里出现的文件名必须走转义：`build_all.py` 的下划线在 LaTeX 里是数学下标，
    # 裸写会直接把整段拖进数学模式（实测报 Missing $ inserted）。
    body.append(r"  {\small %s}" % latex_escape(
        "由 invention-patent-skill 生成，仅供起草阶段使用（源文件见 latex/ 目录）"))
    body.append(r"\end{center}")
    body.append(r"\vspace{0.5em}")
    body.append("")

    # ---- 说明书摘要 ----
    body.append("% " + "-" * 73)
    body.append(r"\parttitle{说明书摘要}")
    body.append("% " + "-" * 73)
    abstract = check_patent.abstract_text(data)
    if abstract:
        seg, bad = latex_inline(abstract, signs)
        unknown.extend(bad)
        body.append(r"\npara{%s}" % seg)
    else:
        # 摘要里不得出现「如权利要求……所述」这类引用语（指南第二部分第二章 2.4），
        # 也不得使用商业性宣传用语；上限 300 字（含标点）。
        notes.append("草稿没有摘要正文：LaTeX 里留了空位，正式提交前必须补写。")
        body.append(r"% TODO 草稿里没有摘要正文；摘要要写清技术问题、方案要点与主要用途，"
                    r"不超过 300 字。")
    hit = re.search(r"^\s*摘要附图\s*[：:]\s*(.+?)\s*$", data["parts"].get(
        check_patent.PART_ABSTRACT, ""), re.M)
    body.append(r"\vspace{0.5em}")
    if hit:
        body.append(r"\noindent{\zihao{-4}\heiti 摘要附图：}%s。"
                    % latex_escape(check_patent.strip_md_inline(hit.group(1))))
    else:
        body.append(r"% TODO 草稿没写「摘要附图：图 N」；有附图时这一行是必须的。")
        notes.append("草稿没写「摘要附图：图 N」：有附图时摘要附图是必须指定的。")
    body.append("")

    # ---- 权利要求书 ----
    body.append("% " + "-" * 73)
    body.append(r"\parttitle{权利要求书}")
    body.append("% " + "-" * 73)
    if data["claims"]:
        for num, claim in data["claims"]:
            seg, bad = latex_inline(claim, signs)
            unknown.extend(bad)
            body.append(r"\claim{%d}{%s}" % (num, seg))
    else:
        body.append(r"% TODO 草稿里没有权利要求书。")
    body.append("")

    # ---- 说明书 ----
    body.append("% " + "-" * 73)
    body.append(r"\parttitle{说明书}")
    body.append("% " + "-" * 73)
    body.append(r"\begin{center}{\zihao{-3}\heiti %s}\end{center}" % latex_escape(name))
    body.append(r"\vspace{0.5em}")
    order = spec_order(data)
    for section in order:
        body.append("")
        body.append(r"\sect{%s}" % latex_escape(section))
        text = section_text(section, data["spec_sections"].get(section, ""))
        for para in paragraphs(text):
            bold = RE_BOLD_ONLY.match(para)
            if bold:
                body.append(r"\npara{\textbf{%s}}" % latex_escape(bold.group(1)))
                continue
            seg, bad = latex_inline(para, signs)
            unknown.extend(bad)
            body.append(r"\spara{%s}" % seg)
        if not paragraphs(text):
            body.append(r"% TODO 这个小节在草稿里没有正文。")
            notes.append("说明书「%s」小节在草稿里没有正文，LaTeX 里只留了标题。" % section)
    if not order:
        body.append(r"\sect{技术领域}")
        body.append(r"% TODO 草稿里没有可用的小节标题。")
    body.append("")

    # ---- 说明书附图 ----
    body.append("% " + "-" * 73)
    body.append("% 说明书附图（正式提交时通常另页绘制；此处给出占位说明）")
    body.append("% " + "-" * 73)
    body.append(r"\newpage")
    body.append(r"\parttitle{说明书附图}")
    fig_lines = [ln.strip() for ln in data["parts"].get(check_patent.PART_FIGURES, "")
                 .split("\n") if ln.strip()]
    if fig_lines:
        stray = 0
        for line in fig_lines:
            m = RE_FIGURE_LINE.match(line)
            if not m:
                # 「说明书附图」部分里也可能有作者写的说明文字，那不是图题。
                # 当成图去套占位框会排出「此处插入（说明文字）：…」这种荒唐东西，
                # 所以这里只按普通文字排，并在生成说明里点名。
                stray += 1
                body.append(r"\noindent %s\par" % latex_escape(line))
                body.append(r"\vspace{0.5em}")
                continue
            label = "图 %s" % m.group(1)
            body.append(r"\noindent %s\par" % latex_escape(line))
            body.append(r"\vspace{0.5em}")
            body.append(r"\begin{center}")
            body.append(r"  \fbox{\parbox[c][35mm][c]{0.8\textwidth}{\centering\small")
            body.append(r"    此处插入%s：用绘图软件绘制后以矢量图插入。\\" % latex_escape(label))
            body.append(r"    附图要求（线条、编号、文字限制）见 references/drawings.md。}}")
            body.append(r"\end{center}")
            body.append(r"\vspace{1em}")
        if stray:
            notes.append("说明书附图部分有 %d 行不是「图 N …」形式的图题，已按普通文字排在"
                         "附图页，没有套占位框。" % stray)
    else:
        # 无附图时可以没有这一部分；这里保留标题只是为了让版式检查仍能核对四个
        # 一级部件的先后，产出报告里会明确点名「草稿没有附图」。
        body.append(r"% TODO 草稿没有「说明书附图」部分。发明无附图时，这一部分连同")
        body.append(r"% 说明书里的「附图说明」都可以整体删掉；要附图就补在这里。")
        body.append(r"\noindent （本申请尚未提供附图；正式提交前请补齐或删除本部分。）\par")
    body.append("")

    # ---- 引证文件 ----
    body.append("% " + "-" * 73)
    body.append("% 引证的对比文件 / 参考文献：背景技术里引证过的文件必须在此可查。")
    body.append("% " + "-" * 73)
    body.append(r"\newpage")
    body.append(r"\renewcommand{\refname}{引证文件}")
    body.append(r"\bibliographystyle{unsrt}")
    body.append(r"\bibliography{refs}")
    body.append("")
    body.append(r"\end{document}")

    text = LATEX_PREAMBLE + "\n" + "\n".join(body) + "\n"
    if para_number is False:
        # 开关那一行在版式头里，所以要在**拼好之后**改。判据是「整行只有这个宏」：
        # 文件头那段说明里也提到过 \paranumbertrue，那是注释，绝不能一起改掉。
        text = "\n".join(r"\paranumberfalse" if ln.strip() == r"\paranumbertrue" else ln
                         for ln in text.split("\n"))

    unknown = sorted(set(unknown), key=lambda s: (len(s), s))
    if unknown:
        notes.append("正文里的（%s）没有登记在「附图标记说明」里，LaTeX 里按普通括号"
                     "原样保留、没有写成 \\mref——附图标记必须先在说明里列出来。"
                     % "、".join(unknown))
    return text, notes


def latex_structural(tex_text: str, draft_parts: set[str]) -> list[tuple[str, bool, str]]:
    """对生成的 main.tex 跑 `check_latex` 的结构性检查，返回 `[(项, 通过, 说明)]`。

    参数:
        tex_text: main.tex 全文。
        draft_parts: 草稿里实际出现的部件名集合（用于判定豁免是否成立）。

    返回:
        逐项结果。

    算法:
        调 `check_latex.STRUCTURAL_CHECKS` 里的每个函数；只有 `LATEX_EXEMPT` 登记
        的两种「本来就可以没有」的缺席才降级成提示。

    复杂度:
        O(n)。

    陷阱:
        豁免必须同时满足两点：失败信息确实是「缺少」而不是「顺序」，且那个对象
        在草稿里就真的不存在。否则会把「草稿里明明有附图说明、却写在了具体实施
        方式后面」这种真错误也放过。
    """
    results: list[tuple[str, bool, str]] = []
    for label, fn in check_latex.STRUCTURAL_CHECKS:
        ok, msg = fn(tex_text)
        if not ok:
            for target_label, allowed, marker in LATEX_EXEMPT:
                if (label == target_label and marker in msg and allowed not in draft_parts
                        and all(p in draft_parts for p in check_patent.SPEC_SECTIONS
                                if p != allowed)):
                    ok = True
                    msg += ("（草稿本来就没有「%s」；无附图时该部分可以整体省略，"
                            "按 references/format.md §4 不计为失败）" % allowed)
                    break
        results.append((label, ok, msg))
    return results


# ---------------------------------------------------------------------------
# 产物：写文件 / 摘要 / 清单
# ---------------------------------------------------------------------------


def write_text(path: Path, text: str) -> Path:
    """写文本文件：UTF-8、强制 `\\n` 换行（跨平台逐字节一致）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_json(path: Path, obj: object) -> Path:
    """写 JSON：UTF-8、不转义中文、缩进 2、结尾一个换行。"""
    return write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def file_sha256(path: Path, chunk: int = 1 << 16) -> str:
    """分块算 SHA-256（不把整个文件读进内存）。"""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def relative(path: Path, root: Path) -> str:
    """输出目录内的相对路径（统一用正斜杠，跨平台一致）。"""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:                       # pragma: no cover - 产物一定在 root 下
        return path.name


def build_manifest(src: Path, files: list[Path], root: Path) -> dict:
    """产物清单：逐文件字节数与 SHA-256，**不含时间戳与绝对路径**。

    参数:
        src: 输入的草稿路径。
        files: 已写出的产物路径列表。
        root: 输出目录（用于算相对路径）。

    返回:
        manifest 字典。

    算法:
        只记相对路径、字节数、SHA-256；排序后写入。

    复杂度:
        O(产物总字节数)。

    陷阱:
        无时间戳是刻意的：manifest 里一旦有 `generated_at`，同一份草稿每次打包
        的哈希都不一样，"逐字节可复现"这句话就废了。
    """
    entries = []
    for path in sorted(files, key=lambda p: relative(p, root)):
        entries.append({
            "path": relative(path, root),
            "bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        })
    return {
        "tool": "build_all.py",
        "version": VERSION,
        "note": "不含时间戳与绝对路径；同一份草稿用同样参数、同样输出目录名打包，"
                "整份清单逐字节一致（可选的 PDF 不在此列：xelatex 会在 PDF 里写入"
                "生成时间与文件 ID，同一份 .tex 两次编译出的 PDF 字节不同）。",
        "source": {
            "name": src.name,
            "bytes": src.stat().st_size,
            "sha256": file_sha256(src),
        },
        "files": entries,
    }


def patent_json(data: dict, findings: list[dict]) -> dict:
    """把 `check_patent` 的发现整理成与 `check_patent --json` 同形的字典。"""
    fails = [f for f in findings if f["level"] == "fail"]
    warns = [f for f in findings if f["level"] == "warn"]
    return {
        "name": data["name"],
        "claims": len(data["claims"]),
        "spec_sections": data["spec_order"],
        "abstract_chars": len(check_patent.abstract_text(data)),
        "fails": fails,
        "warns": warns,
    }


def render_report(*, src: Path, out_dir: Path, data: dict, patent_text: str,
                  structural: list[tuple[str, bool, str]], files: list[Path],
                  notes: list[str], problems: list[str], pdf_note: str | None) -> str:
    """拼出给人看的 `自检报告.txt`。"""
    out: list[str] = []
    add = out.append
    add("=" * 72)
    add("一键输出：发明专利申请文件包")
    add("=" * 72)
    add("工具：build_all.py %s" % VERSION)
    add("输入草稿：%s（%d 字节，SHA-256 %s…）"
        % (src.name, src.stat().st_size, file_sha256(src)[:16]))
    add("输出目录：%s/" % out_dir.name)
    add("发明名称：%s" % (data["name"] or "（未找到）"))
    add("产物：%d 个文件（完整清单与 SHA-256 见 %s）" % (len(files) + 1, MANIFEST))
    add("")

    add("【产物清单】")
    for path in sorted(files, key=lambda p: relative(p, out_dir)):
        add("  %-26s %9d 字节  %s" % (relative(path, out_dir),
                                      path.stat().st_size, file_sha256(path)[:16]))
    add("")

    add("【一、草稿机械自检（check_patent.py）】")
    add(patent_text.rstrip("\n"))
    add("")

    add("【二、LaTeX 版式结构自检（check_latex.py 的结构性检查，不编译）】")
    for label, ok, msg in structural:
        add("  %s %s：%s" % ("✓" if ok else "✗", label, msg))
    add("")
    add("  说明：这一节查的是**生成的 main.tex 源码**；版式之外的措辞、附图标记等")
    add("        以第一节为准。逐条法条依据见 references/format.md。")
    add("")

    if notes:
        add("【三、生成说明】")
        for note in notes:
            add("  · " + note)
        add("")

    add("【四、仍需人工处理的事项】")
    for item in problems:
        add("  · " + item)
    if pdf_note:
        add("  · " + pdf_note)
    if not problems and not pdf_note:
        add("  · 无。")
    add("")

    add("提示：本包只保证「形式与机械检查」。新颖性、创造性、保护范围、")
    add("      说明书是否充分公开，都要人工判断；正式提交前请核对 references/ 里的")
    add("      法条出处是否仍然现行（现行《专利法实施细则》为 2023 年修订版），")
    add("      并建议交由执业代理师复核。")
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# PDF（可选）
# ---------------------------------------------------------------------------


def compile_pdf(tex_dir: Path, out_dir: Path, *, keep_fontset: bool = False,
                timeout: int = 300) -> tuple[Path | None, str | None, list[str]]:
    """把 `latex/main.tex` 编译成 PDF。

    参数:
        tex_dir: 装着 main.tex / refs.bib 的目录。
        out_dir: PDF 的落地目录。
        keep_fontset: True 时不替换字体集（要求本机装了 Windows 字体）。
        timeout: 单条命令的超时秒数。

    返回:
        `(PDF 路径或 None, 失败原因或 None, 说明列表)`。

    算法:
        复制到系统临时目录后按 xelatex → bibtex → xelatex → xelatex 编译；判据是
        `main.log` 的解析结果与 `main.pdf` 是否真的存在、体积是否合理——**不看
        退出码**（bibtex 在没有 `\\cite` 时会以非零码退出，那是噪音）。
        临时目录用完即删，输出目录里不留 .aux/.log。

    复杂度:
        O(编译耗时)。

    陷阱:
        默认把 `fontset=windows` 换成随发行版自带的 `fandol`：Windows 字体在
        Linux/CI 上并不存在。换的只是**临时副本**，输出目录里的 main.tex 保持
        `fontset=windows`（本机 Windows 上开箱即用），这一点与 check_latex.py 一致。
    """
    notes: list[str] = []
    engines = check_latex.find_engines(None)
    if not engines.get("xelatex") or not engines.get("bibtex"):
        return None, "PATH 上找不到 xelatex / bibtex，无法编译 PDF", notes

    work = Path(tempfile.mkdtemp(prefix="buildall_"))
    try:
        for fname in ("main.tex", "refs.bib"):
            shutil.copy2(tex_dir / fname, work / fname)
        if keep_fontset:
            notes.append("按 --keep-fontset 保留 fontset=windows，直接编译输出目录里那份 main.tex。")
        else:
            src = (work / "main.tex").read_text(encoding="utf-8")
            patched, replaced = check_latex.override_fontset_text(src)
            if replaced:
                write_text(work / "main.tex", patched)
                notes.append("临时副本把 fontset=windows 换成了 fontset=fandol（%d 处）；"
                             "输出目录里的 main.tex 仍是 Windows 字体集。" % replaced)
            else:
                notes.append("main.tex 里没有 fontset=windows，按原样编译。")

        errors: list[str] = []
        for cmd in (["xelatex", "-interaction=nonstopmode", "main.tex"],
                    ["bibtex", "main"],
                    ["xelatex", "-interaction=nonstopmode", "main.tex"],
                    ["xelatex", "-interaction=nonstopmode", "main.tex"]):
            try:
                proc = subprocess.run(cmd, cwd=str(work), capture_output=True, text=True,
                                      encoding="utf-8", errors="replace", timeout=timeout)
            except subprocess.TimeoutExpired:
                errors.append("%s 超过 %d 秒没结束" % (cmd[0], timeout))
                break
            except OSError as exc:
                errors.append("%s 无法启动：%s" % (cmd[0], exc))
                break
            if proc.returncode != 0:
                if cmd[0] == "bibtex":
                    # 与 check_latex.run_template 同一口径：没有 \cite 时 bibtex 会以
                    # 非零码退出，那是噪音，不是编译失败。判据看 main.log 和 main.pdf。
                    notes.append("bibtex 退出码 %d（默认没有 \\cite，属正常噪音）。"
                                 % proc.returncode)
                else:
                    notes.append("%s 退出码 %d（判据以 main.log 与 main.pdf 为准）"
                                 % (cmd[0], proc.returncode))

        log_path = work / "main.log"
        problems, stats = check_latex.analyse_log(
            log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else "")
        problems.extend(errors)

        pdf = work / "main.pdf"
        if not pdf.exists():
            problems.append("没有生成 main.pdf")
        elif pdf.stat().st_size < check_latex.MIN_PDF_BYTES:
            problems.append("main.pdf 只有 %d 字节，不像是正常排版结果" % pdf.stat().st_size)

        if problems:
            return None, "；".join(problems[:4]), notes
        dest = out_dir / PDF_NAME
        shutil.copy2(pdf, dest)
        notes.append("PDF 编译成功：%s 页，%d 字节。"
                     % (stats.get("pages"), pdf.stat().st_size))
        return dest, None, notes
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# 打包主流程
# ---------------------------------------------------------------------------


def build_package(src: Path, text: str, data: dict, findings: list, out_dir: Path, *,
                  author: str, para_number: bool, want_pdf: bool, keep_fontset: bool,
                  timeout: int) -> dict:
    """把一份草稿打成整套文件包，返回汇总字典。"""
    notes: list[str] = preflight_notes(data)
    artifacts: list[Path] = []

    comments = len(RE_COMMENT.findall(text))
    if comments:
        notes.append("草稿里有 %d 处 HTML 填写提示（<!-- … -->），已从产出里剔除——"
                     "它们不属于申请文件正文。" % comments)

    (out_dir / SRC_DIR).mkdir(parents=True, exist_ok=True)
    (out_dir / TEX_DIR).mkdir(parents=True, exist_ok=True)

    # ---- 四个部件：Markdown 源文本 + 各自一份 .docx ----
    for part, stem in PART_FILES:
        body = part_markdown(data, part)
        if body is None:
            continue
        artifacts.append(write_text(out_dir / SRC_DIR / ("%s.md" % part), body))
        blocks = make_docx.parse_markdown(body)
        artifacts.append(make_docx.write_docx(
            blocks, out_dir / ("%s.docx" % stem),
            title="%s（%s）" % (data["name"] or "", part), author=author,
            para_number=bool(para_number and part == check_patent.PART_SPEC)))

    # ---- LaTeX 工作稿 ----
    tex, tex_notes = build_latex(data, para_number=para_number)
    notes.extend(tex_notes)
    artifacts.append(write_text(out_dir / TEX_DIR / "main.tex", tex))
    artifacts.append(write_text(out_dir / TEX_DIR / "refs.bib", REFS_BIB))

    structural = latex_structural(tex, set(data["parts"]) | set(data["spec_sections"]))

    # ---- 可选：真编译一份 PDF ----
    pdf_note: str | None = None
    if want_pdf:
        pdf, pdf_error, pdf_notes = compile_pdf(out_dir / TEX_DIR, out_dir,
                                                keep_fontset=keep_fontset, timeout=timeout)
        notes.extend(pdf_notes)
        if pdf is None:
            pdf_note = "PDF 没有生成：%s（.docx 与 latex/ 下的源文件不受影响）" % pdf_error
        else:
            artifacts.append(pdf)

    # ---- 报告与清单 ----
    with contextlib.redirect_stdout(io.StringIO()) as buf:
        code = check_patent.report(data, findings, as_json=False, strict=False)
    patent_text = buf.getvalue()

    problems: list[str] = []
    blocking: list[str] = []
    fail_count = len([f for f in findings if f.level == "fail"])
    if fail_count:
        item = "草稿有 %d 项不合规（见第一节），改完再重新打包。" % fail_count
        problems.append(item)
        blocking.append(item)
    bad_structural = [label for label, ok, _ in structural if not ok]
    if bad_structural:
        item = ("生成的 LaTeX 有 %d 项结构自检未通过：%s。"
                % (len(bad_structural), "、".join(bad_structural)))
        problems.append(item)
        blocking.append(item)
    if not data["parts"].get(check_patent.PART_FIGURES):
        problems.append("草稿没有附图：正式提交前要么补齐附图（申请书附图页 + 说明书附图"
                        "说明），要么把这两处整体删掉。")
    # 这一条是「永远都该看一眼」的提示，不算失败：大部分草稿一开始就没引证文件。
    problems.append("引证文件部分目前是空的：背景技术里引证过的每一份对比文件，都要"
                    "逐条补进 latex/refs.bib 并在正文用 \\cmpfile 引用。")
    ok = not blocking and pdf_note is None

    report_txt_path = out_dir / REPORT_TXT
    write_text(report_txt_path, render_report(
        src=src, out_dir=out_dir, data=data, patent_text=patent_text,
        structural=structural, files=artifacts, notes=notes, problems=problems,
        pdf_note=pdf_note))
    report_texts = artifacts + [report_txt_path]

    write_json(out_dir / REPORT_JSON, {
        "tool": "build_all.py",
        "version": VERSION,
        "ok": ok,
        "source": {"name": src.name, "sha256": file_sha256(src)},
        "patent": patent_json(data, [f.as_dict() for f in findings]),
        "latex": {
            "ok": not bad_structural,
            "checks": [{"label": label, "ok": ok, "message": msg}
                       for label, ok, msg in structural],
        },
        "notes": notes,
        "problems": problems,
        "pdf": {"requested": bool(want_pdf), "ok": pdf_note is None, "problem": pdf_note},
        "files": len(report_texts) + 1,
        "manifest": MANIFEST,
    })

    all_files = report_texts + [out_dir / REPORT_JSON]
    write_json(out_dir / MANIFEST, build_manifest(src, all_files, out_dir))

    return {
        "code": code,
        "notes": notes,
        "problems": problems,
        "structural": structural,
        "files": all_files,
        "pdf_note": pdf_note,
        "patent_text": patent_text,
    }


# ---------------------------------------------------------------------------
# 固件测试
# ---------------------------------------------------------------------------

_FIXTURE = """# 发明名称：一种示例分拣装置

## 说明书摘要

本发明公开了一种示例分拣装置，属于物流分拣技术领域。所述装置包括机架（1）、输送带（2）、视觉单元（3）与控制单元（4）；控制单元（4）根据视觉单元（3）输出的图像确定包裹的去向，并驱动分拣臂（5）动作。本发明用于快递包裹的自动分拣。

摘要附图：图 1

## 权利要求书

1. 一种示例分拣装置，其特征在于，包括机架（1）、输送带（2）、视觉单元（3）和控制单元（4），所述控制单元（4）与所述视觉单元（3）连接。

2. 根据权利要求 1 所述的示例分拣装置，其特征在于，所述视觉单元（3）为线阵相机。

## 说明书

### 技术领域

本发明涉及物流分拣技术领域，具体涉及一种示例分拣装置。

### 背景技术

现有的分拣装置依靠人工识别包裹上的面单，操作人员需要逐件翻看，效率较低。

### 发明内容

本发明所要解决的技术问题是：现有分拣装置依靠人工识别面单，效率较低。

为解决上述技术问题，本发明采用如下技术方案：一种示例分拣装置，包括机架（1）、输送带（2）、视觉单元（3）和控制单元（4），所述控制单元（4）与所述视觉单元（3）连接。

本发明的有益效果是：由视觉单元（3）自动识别包裹，减少了人工翻看的环节。

### 附图说明

图 1 是本发明实施例的分拣装置组成示意图。

附图标记说明：1—机架；2—输送带；3—视觉单元；4—控制单元；5—分拣臂。

### 具体实施方式

下面结合附图和实施例对本发明作进一步说明。

机架 1 采用铝型材搭成，输送带 2 安装在机架 1 的上方。视觉单元 3 装于输送带 2 上方 300 毫米处。控制单元 4 分别与视觉单元 3 和分拣臂 5 连接。

## 说明书附图

图 1　分拣装置组成示意图
"""


def _self_test(failures: list[str]) -> None:
    """不读任何外部文件、不联网、不编译的固件测试。"""
    checks: list[tuple[str, bool]] = []

    def check(label: str, cond: bool) -> None:
        checks.append((label, bool(cond)))

    # ---- 转义 ----
    check("转义：反斜杠单趟替换不成对转义",
          latex_escape("a\\b") == r"a\textbackslash{}b")
    check("转义：十个特殊字符全覆盖",
          latex_escape("{}#$&%_~^\\")
          == r"\{\}\#\$\&\%\_\textasciitilde{}\textasciicircum{}\textbackslash{}")
    check("转义：单趟替换不动新生成的花括号",
          latex_escape("\\{") == r"\textbackslash{}\{")
    check("转义：普通中文与全角标点不动", latex_escape("厚度，约 3 毫米。") == "厚度，约 3 毫米。")
    check("转义：百分号",
          latex_escape("占空比 50%") == r"占空比 50\%")

    # ---- 附图标记 ----
    seg, bad = latex_inline("所述输送带（2）与机架（1）连接。", {"1", "2"})
    check("标记：登记过的换成 mref", seg == r"所述输送带\mref{2}与机架\mref{1}连接。")
    check("标记：登记过的没有未登记号", bad == [])
    seg2, bad2 = latex_inline("标定于（2019）年，写成 (3)。", {"1"})
    check("标记：没登记的括号原样保留", seg2 == "标定于（2019）年，写成 (3)。")
    check("标记：未登记的号被报出来", bad2 == ["2019", "3"])
    seg3, _ = latex_inline("记号（41）与（4a）。", {"41", "4a"})
    check("标记：支持下标字母", seg3 == r"记号\mref{41}与\mref{4a}。")

    # ---- 解析与拆分 ----
    data, findings = check_patent.run_checks(_FIXTURE)
    check("固件草稿：解析出发明名称", data["name"] == "一种示例分拣装置")
    check("固件草稿：无 fail 级问题", not [f for f in findings if f.level == "fail"])
    check("固件草稿：无 warn 级问题", not [f for f in findings if f.level == "warn"])
    check("小节顺序：五个法定小节齐全",
          spec_order(data) == list(check_patent.SPEC_SECTIONS))

    dup = dict(data, spec_order=["技术领域", "技术领域", "背景技术"])
    check("小节顺序：重复标题去重", spec_order(dup) == ["技术领域", "背景技术"])
    check("生成说明：重复标题被点名",
          any("重复" in n for n in preflight_notes(dup)))

    absent = dict(data,
                  parts={k: v for k, v in data["parts"].items()
                         if k != check_patent.PART_FIGURES},
                  spec_sections={k: v for k, v in data["spec_sections"].items()
                                 if k != "附图说明"},
                  spec_order=[s for s in data["spec_order"] if s != "附图说明"])
    check("生成说明：缺附图部分被点名",
          any("说明书附图" in n for n in preflight_notes(absent)))

    check("段落：空行分段", paragraphs("甲\n\n乙") == ["甲", "乙"])
    check("段落：中文软换行不补空格", paragraphs("第一行\n第二行") == ["第一行第二行"])
    check("段落：西文软换行补空格", paragraphs("alpha\nbeta") == ["alpha beta"])
    check("附图说明：按行拆段",
          section_text("附图说明", "图 1 是甲；\n图 2 是乙。") == "图 1 是甲；\n\n图 2 是乙。")
    check("小节正文：其它小节原样", section_text("背景技术", "甲\n乙") == "甲\n乙")

    # ---- 单部件 ----
    abstract_md = part_markdown(data, check_patent.PART_ABSTRACT)
    claims_md = part_markdown(data, check_patent.PART_CLAIMS)
    spec_md = part_markdown(data, check_patent.PART_SPEC)
    figs_md = part_markdown(data, check_patent.PART_FIGURES)
    check("部件：摘要是一级标题", abstract_md.startswith("# 说明书摘要\n"))
    check("部件：摘要留着摘要附图行", "摘要附图：图 1" in abstract_md)
    check("部件：权利要求书编号成行", "\n1. 一种示例分拣装置" in claims_md
          and "\n2. 根据权利要求 1" in claims_md)
    check("部件：说明书以发明名称居中标题开头", spec_md.startswith("# 一种示例分拣装置\n"))
    check("部件：说明书带五个小节标题",
          all("\n## %s\n" % s in spec_md for s in check_patent.SPEC_SECTIONS))
    check("部件：附图说明一图一段", "图 1 是本发明实施例的分拣装置组成示意图。\n\n附图标记说明" in spec_md)
    check("部件：说明书附图部分", figs_md.startswith("# 说明书附图\n"))
    check("部件：缺部件返回 None",
          part_markdown(absent, check_patent.PART_FIGURES) is None)
    check("部件：没有权利要求时返回 None",
          part_markdown(dict(data, claims=[]), check_patent.PART_CLAIMS) is None)

    # ---- LaTeX ----
    tex, tex_notes = build_latex(data)
    doc_tex = tex.split(r"\begin{document}")[1]
    check("LaTeX：正文里没有裸的（2）", "（2）" not in doc_tex)
    check("LaTeX：标记写成了 mref", r"输送带\mref{2}、视觉单元\mref{3}" in tex)
    check("LaTeX：摘要用 npara", r"\npara{本发明公开了一种示例分拣装置" in tex)
    check("LaTeX：摘要附图行", r"\noindent{\zihao{-4}\heiti 摘要附图：}图 1。" in tex)
    check("LaTeX：权利要求用 claim", r"\claim{2}{根据权利要求 1 所述的示例分拣装置" in tex)
    check("LaTeX：正文里的裸标记不加括号", "输送带 2 安装在机架 1 的上方" in tex)
    check("LaTeX：说明书五部分用 sect",
          all(r"\sect{%s}" % s in tex for s in check_patent.SPEC_SECTIONS))
    check("LaTeX：四个一级部件用 parttitle",
          all(r"\parttitle{%s}" % p in tex
              for p in (check_patent.PART_ABSTRACT, check_patent.PART_CLAIMS,
                        check_patent.PART_SPEC, check_patent.PART_FIGURES)))
    check("LaTeX：有 bibliography", r"\bibliography{refs}" in tex)
    check("LaTeX：有引证文件标题", r"\renewcommand{\refname}{引证文件}" in tex)
    check("LaTeX：段号默认开",
          "\n\\paranumbertrue\n" in tex and "\n\\paranumberfalse\n" not in tex)
    tex_off, _ = build_latex(data, para_number=False)
    check("LaTeX：--no-para-number 关掉段号",
          "\n\\paranumberfalse\n" in tex_off and "\n\\paranumbertrue\n" not in tex_off)
    check("LaTeX：段号说明文字不被开关误伤", "paranumbertrue 改成" in tex_off)
    absent_tex, _absent_notes = build_latex(absent)
    check("LaTeX：缺附图时不写附图页占位符",
          r"\parttitle{说明书附图}" in absent_tex and "（本申请尚未提供附图" in absent_tex)
    check("LaTeX：缺附图时源码里留 TODO",
          "TODO 草稿没有「说明书附图」部分" in absent_tex)
    no_abs_md = dict(data, parts=dict(data["parts"], **{check_patent.PART_ABSTRACT: ""}))
    check("LaTeX：没有摘要正文时留 TODO 不留空段",
          "TODO 草稿里没有摘要正文" in build_latex(no_abs_md)[0])
    mixed = dict(data, parts=dict(data["parts"], **{
        check_patent.PART_FIGURES: "图 1　装置组成示意图\n（说明：本示例不含实际图像。）"}))
    mixed_tex, mixed_notes = build_latex(mixed)
    check("LaTeX：附图页里的说明文字不套占位框",
          "此处插入（说明" not in mixed_tex
          and r"\noindent （说明：本示例不含实际图像。）\par" in mixed_tex)
    check("LaTeX：附图页里的说明文字被点名", any("不是「图" in n for n in mixed_notes))

    # 版式头不能漂移：A4、四个页边距、fontset、宏名
    check("版式头：A4", "a4paper" in LATEX_PREAMBLE)
    check("版式头：fontset=windows", "fontset=windows" in LATEX_PREAMBLE)
    check("版式头：geometry 参数与 check_latex 要求一致",
          all("%s=%s" % (k, v) in LATEX_PREAMBLE
              for k, v in check_latex.REQUIRED_MARGINS.items()))
    check("版式头：宏齐全",
          all(r"\newcommand{\%s}" % m in LATEX_PREAMBLE
              for m in ("parttitle", "sect", "spara", "npara", "claim", "mref", "cmpfile", "padfour")))

    # 生成的 tex 必须能过 check_latex 的七项结构检查
    draft_keys = set(data["parts"]) | set(data["spec_sections"])
    structural = dict((label, ok) for label, ok, _ in latex_structural(tex, draft_keys))
    check("结构自检：一级部件顺序通过", structural["一级部件顺序"])
    check("结构自检：说明书五部分通过", structural["说明书五部分"])
    check("结构自检：版式通过", structural["版式"])
    check("结构自检：权利要求通过", structural["权利要求"])
    check("结构自检：摘要字数通过", structural["摘要字数"])
    check("结构自检：措辞通过", structural["措辞"])
    check("结构自检：附图标记通过", structural["附图标记"])

    absent_keys = set(absent["parts"]) | set(absent["spec_sections"])
    absent_structural = dict((label, ok) for label, ok, _
                             in latex_structural(absent_tex, absent_keys))
    check("结构自检：无附图时缺「附图说明」被豁免", absent_structural["说明书五部分"])
    # 豁免的闸门是「草稿里确实没有」：同一份源码，草稿有附图说明就必须照报
    no_sect_tex = tex.replace(r"\sect{附图说明}", "% 附图说明被挪走了")
    kept = dict((label, ok) for label, ok, _
                in latex_structural(no_sect_tex, draft_keys))
    check("结构自检：草稿有附图说明时不豁免缺席", not kept["说明书五部分"])
    moved_tex = (tex.replace(r"\sect{附图说明}", "@@FIG@@")
                    .replace(r"\sect{具体实施方式}", r"\sect{附图说明}")
                    .replace("@@FIG@@", r"\sect{具体实施方式}"))
    moved = dict((label, ok) for label, ok, _
                 in latex_structural(moved_tex, draft_keys))
    check("结构自检：顺序错照样报失败", not moved["说明书五部分"])

    # ---- 落盘：docx 与清单 ----
    with tempfile.TemporaryDirectory(prefix="buildall_test_") as tmp:
        root = Path(tmp)
        src = root / "draft.md"
        src.write_text(_FIXTURE, encoding="utf-8", newline="\n")
        out = root / "pkg"
        result = build_package(src, _FIXTURE, data, findings, out, author="tester",
                               para_number=True, want_pdf=False, keep_fontset=False,
                               timeout=300)

        names = [relative(p, out) for p in result["files"]]
        for want in ("01-说明书摘要.docx", "02-权利要求书.docx", "03-说明书.docx",
                     "04-说明书附图.docx", "src/说明书.md", "latex/main.tex",
                     "latex/refs.bib", REPORT_TXT, REPORT_JSON):
            check("落盘：有 %s" % want, want in names)
        check("落盘：有 manifest.json", (out / MANIFEST).is_file())
        check("落盘：没有 PDF（未要求）", PDF_NAME not in names)
        check("落盘：报告说明机械校验通过",
              "结论：机械校验全部通过。" in (out / REPORT_TXT).read_text(encoding="utf-8"))
        check("落盘：报告点名引证文件为空",
              "引证文件" in (out / REPORT_TXT).read_text(encoding="utf-8"))
        check("落盘：没要求 PDF 时不留 .pdf",
              not list(out.glob("*.pdf")) and not list((out / TEX_DIR).glob("*.aux")))

        docx_path = out / "03-说明书.docx"
        info = make_docx.inspect_docx(docx_path)
        check("落盘：说明书 docx 可解析", bool(info["ok"]))
        check("落盘：说明书 docx 无缺件", info["missing"] == [])
        check("落盘：说明书 docx 含段落编号", any("[0001]" in t for t in info["texts"]))
        other = make_docx.inspect_docx(out / "01-说明书摘要.docx")
        check("落盘：摘要 docx 不含段落编号",
              not any(t.startswith("[000") for t in other["texts"]))

        again = make_docx.write_docx(make_docx.parse_markdown(
            (out / SRC_DIR / "说明书.md").read_text(encoding="utf-8")),
            root / "again.docx", title="%s（%s）" % (data["name"], check_patent.PART_SPEC),
            author="tester", para_number=True)
        check("落盘：docx 逐字节可复现",
              file_sha256(again) == file_sha256(docx_path))

        manifest = json.loads((out / MANIFEST).read_text(encoding="utf-8"))
        listed = {e["path"] for e in manifest["files"]}
        check("清单：条目与实际文件一一对应", listed == set(names))
        check("清单：不含 manifest 自己", MANIFEST not in listed)
        check("清单：哈希与文件一致",
              all(file_sha256(out / e["path"]) == e["sha256"] for e in manifest["files"]))
        check("清单：没有时间戳与绝对路径",
              "generated_at" not in json.dumps(manifest) and tmp not in json.dumps(manifest))
        check("清单：源草稿信息齐全",
              manifest["source"]["name"] == "draft.md"
              and manifest["source"]["sha256"] == file_sha256(src))
        # 真的再打一次包（换父目录、但输出目录同名），整份 manifest 必须一模一样。
        # 这条能抓住"偷偷塞进时间戳/绝对路径"的回归——只重算一遍现有文件的哈希抓不到。
        twin_out = root / "twin" / out.name
        build_package(src, _FIXTURE, data, findings, twin_out, author="tester",
                      para_number=True, want_pdf=False, keep_fontset=False, timeout=300)
        twin = json.loads((twin_out / MANIFEST).read_text(encoding="utf-8"))
        check("清单：二次打包（另一父目录）整份一致", twin == manifest)

        report = json.loads((out / REPORT_JSON).read_text(encoding="utf-8"))
        check("报告 JSON：latex 全通过", report["latex"]["ok"])
        check("报告 JSON：专利自检无 fail", report["patent"]["fails"] == [])
        check("报告 JSON：问题清单与退出码一致", report["ok"] is True)

    # ---- 端到端命令行 ----
    def run_cli(args: list[str]) -> int:
        """跑一次命令行，把它的输出吞掉——固件测试只关心退出码。"""
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            return main(args)

    with tempfile.TemporaryDirectory(prefix="buildall_cli_") as tmp:
        root = Path(tmp)
        src = root / "draft.md"
        src.write_text(_FIXTURE, encoding="utf-8", newline="\n")
        check("命令行：正常草稿返回 0", run_cli([str(src), "-o", str(root / "a"), "-q"]) == 0)
        check("命令行：默认目录名带 -out 后缀",
              run_cli([str(src), "-q"]) == 0 and (root / ("draft" + OUT_DIR_SUFFIX)).is_dir())
        check("命令行：输出目录自动建", (root / "a" / "latex" / "main.tex").is_file())

        bad = root / "bad.md"
        bad.write_text(_FIXTURE.replace("## 权利要求书", "## 权利书"), encoding="utf-8",
                       newline="\n")
        check("命令行：缺权利要求书返回 2", run_cli([str(bad), "-o", str(root / "b")]) == 2)
        check("命令行：文件不存在返回 2",
              run_cli([str(root / "nope.md"), "-o", str(root / "c")]) == 2)
        check("命令行：没给路径返回 2", run_cli([]) == 2)

        over = root / "over.md"
        over.write_text(_FIXTURE.replace("本发明用于快递包裹的自动分拣。", "甲" * 320),
                        encoding="utf-8", newline="\n")
        check("命令行：摘要超限返回 1", run_cli([str(over), "-o", str(root / "d"), "-q"]) == 1)
        check("命令行：--strict 下无警告仍返回 0",
              run_cli([str(src), "-o", str(root / "e"), "--strict", "-q"]) == 0)
        check("命令行：--no-para-number 生效",
              run_cli([str(src), "-o", str(root / "g"), "--no-para-number", "-q"]) == 0
              and "\n\\paranumbertrue\n" not in
              (root / "g" / "latex" / "main.tex").read_text(encoding="utf-8"))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main([str(src), "-o", str(root / "f"), "--json"])
        payload = json.loads(buf.getvalue())
        check("命令行：--json 只输出 JSON", rc == 0 and payload["ok"] is True)
        check("命令行：--json 带清单指针", payload["manifest"] == MANIFEST)
        check("命令行：--json 报出发明名称", payload["name"] == "一种示例分拣装置")

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
        进程退出码：0 全部通过；1 有不合规 / 结构自检不过 / PDF 没编出来；
        2 用法错误或草稿缺到无法拆分。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):      # pragma: no cover
            pass

    parser = argparse.ArgumentParser(
        prog="build_all.py",
        description="把申请文件草稿一键打成整套可提交的格式（纯标准库）。",
        epilog="例：python scripts/build_all.py 申请文件.md；"
               "python scripts/build_all.py 申请文件.md -o 提交包 --pdf")
    parser.add_argument("draft", nargs="?", help="申请文件草稿 Markdown 路径")
    parser.add_argument("-o", "--out", help="输出目录（默认 <草稿名>-out）")
    parser.add_argument("--author", default="invention-patent-skill",
                        help=".docx 的 docProps 作者（默认 invention-patent-skill）")
    parser.add_argument("--no-para-number", dest="para_number", action="store_false",
                        help="不给说明书正文加 [0001] 段号（正式提交电子申请时用）")
    parser.add_argument("--pdf", action="store_true",
                        help="顺带编译一份 PDF；拿不到就报失败（缺 TeX 时请只用 .docx）")
    parser.add_argument("--keep-fontset", action="store_true",
                        help="编译 PDF 时不把 fontset=windows 换成 fandol")
    parser.add_argument("--timeout", type=int, default=300, help="单条编译命令超时秒数")
    parser.add_argument("--strict", action="store_true", help="把「需要复核」也当失败")
    parser.add_argument("--json", action="store_true", help="只打印 JSON 报告")
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

    if not args.draft:
        parser.print_usage(sys.stderr)
        print("错误：请给一份草稿路径，或用 --self-test", file=sys.stderr)
        return 2

    src = Path(args.draft)
    if not src.is_file():
        print("错误：找不到 %s" % src, file=sys.stderr)
        return 2

    text = src.read_text(encoding="utf-8")
    data, findings = check_patent.run_checks(text)

    if not data["name"]:
        print("错误：草稿首行没有「发明名称：……」，无法确定申请文件标题。", file=sys.stderr)
        return 2
    missing = [p for p in REQUIRED_PARTS if not data["parts"].get(p)]
    if missing:
        print("错误：草稿缺以下部件，无法按部件打包：", file=sys.stderr)
        for part in missing:
            print("  · ## %s" % part, file=sys.stderr)
        print("提示：python scripts/check_patent.py --init 草稿.md 可以拿到骨架。",
              file=sys.stderr)
        return 2

    out_dir = Path(args.out) if args.out else src.with_name(src.stem + OUT_DIR_SUFFIX)
    result = build_package(src, text, data, findings, out_dir,
                           author=args.author, para_number=args.para_number,
                           want_pdf=args.pdf, keep_fontset=args.keep_fontset,
                           timeout=args.timeout)

    fails = [f for f in findings if f.level == "fail"]
    warns = [f for f in findings if f.level == "warn"]
    bad_structural = [label for label, ok, _ in result["structural"] if not ok]
    rc = 1 if (fails or bad_structural or result["pdf_note"]) else 0
    if args.strict and warns:
        rc = 1

    if args.json:
        print(json.dumps({
            "source": str(src),
            "out": str(out_dir),
            "name": data["name"],
            "claims": len(data["claims"]),
            "abstract_chars": len(check_patent.abstract_text(data)),
            "files": len(result["files"]) + 1,
            "latex_ok": not bad_structural,
            "pdf": {"requested": args.pdf, "problem": result["pdf_note"]},
            "notes": result["notes"],
            "problems": result["problems"],
            "fails": [f.as_dict() for f in fails],
            "warns": [f.as_dict() for f in warns],
            "manifest": MANIFEST,
            "ok": rc == 0,
        }, ensure_ascii=False, indent=2))
        return rc

    print("=" * 72)
    print("草稿 → 申请文件包")
    print("=" * 72)
    print("草稿：%s" % src)
    print("输出：%s/" % out_dir)
    print("发明名称：%s" % data["name"])
    print("产物：%d 个文件（含 4 份 .docx + LaTeX 工作稿 + 报告 + 清单）"
          % (len(result["files"]) + 1))
    print()

    if not args.quiet:
        print("【产物清单】")
        for path in sorted(result["files"], key=lambda p: relative(p, out_dir)):
            print("  %-26s %9d 字节" % (relative(path, out_dir), path.stat().st_size))
        print()
        if result["notes"]:
            print("【生成说明】")
            for note in result["notes"]:
                print("  · " + note)
            print()

    sys.stdout.write(result["patent_text"])
    print()
    print("【LaTeX 版式结构自检（不编译）】")
    for label, ok, msg in result["structural"]:
        print("  %s %s：%s" % ("✓" if ok else "✗", label, msg))
    print()

    print("【仍需人工处理的事项】")
    for item in result["problems"]:
        print("  · " + item)
    if result["pdf_note"]:
        print("  · " + result["pdf_note"])
    print()
    print("提示：本包只保证「形式与机械检查」；技术内容、新颖性、创造性、保护范围")
    print("      仍须人工复核，正式提交前建议交由执业代理师把关。")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
