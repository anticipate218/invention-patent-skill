#!/usr/bin/env python3
"""把专利文件 Markdown 草稿转成 CNIPA 排版的 .docx —— 纯标准库，不依赖 Word / pandoc / LibreOffice。

为什么自己拼 OOXML：本技能的定位是"给任何一个 AI 助手用"，用户机器上不一定有 Word、
更不一定有 pandoc 或 LibreOffice。.docx 本质就是一堆 XML 打成的 ZIP，用 `zipfile` +
字符串拼装即可生成，且**输出可复现**（同样的输入必定得到逐字节相同的文件），
这样金标准哈希才有意义。代价是只实现 WordprocessingML 的一个子集——够用即可。

排版参数集中在 `LAYOUT`：它给出的是**代理实务常见排版**，不是法律强制值。
强制性要求（纸张、字迹、各部分标题与顺序、摘要字数、附图规范）以《专利法实施细则》
与《专利审查指南》为准，见 `references/format.md`；本脚本不代替该文档做合规判断。

用法：
    python scripts/make_docx.py draft.md                      # 输出 draft.docx
    python scripts/make_docx.py draft.md -o 申请文件.docx
    python scripts/make_docx.py draft.md --para-number        # 正文段落自动加 [0001]
    python scripts/make_docx.py --inspect 申请文件.docx        # 只体检，不生成
    python scripts/make_docx.py --self-test

支持的 Markdown 子集（故意只做这些，避免半吊子实现）：
    `# 一级标题` / `## 二级` / `### 三级`、普通段落、`- ` 无序项、`1. ` 有序项、
    `| a | b |` 表格、`**粗体**`、`*斜体*`、`` `等宽` ``。

退出码：0 通过；1 失败；2 用法错误。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OD_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"
EP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

# 生成时间固定，否则同样的输入会得到不同的字节，无法做金标准哈希
FIXED_TS = "2020-01-01T00:00:00Z"
ZIP_DATE = (2020, 1, 1, 0, 0, 0)

# 排版参数。单位：mm（页边距）/ 半磅（字号，24 = 小四 12pt）/ 240 分之一行（行距）
LAYOUT: dict[str, object] = {
    "margin_top_mm": 25.0,
    "margin_bottom_mm": 15.0,
    "margin_left_mm": 25.0,
    "margin_right_mm": 15.0,
    "page_w_mm": 210.0,
    "page_h_mm": 297.0,
    "title_sz": 32,       # 三号 16pt
    "h1_sz": 28,          # 四号 14pt
    "h2_sz": 24,          # 小四 12pt
    "h3_sz": 24,
    "body_sz": 24,        # 小四 12pt
    "line_240": 360,      # 1.5 倍行距
    "first_line_indent": 0,   # 字符宽度倍数 ×100（0 = 不缩进）
    "latin_font": "Times New Roman",
    "cjk_font": "SimSun",
    "title_cjk_font": "SimHei",
    "heading_cjk_font": "SimHei",
}

# 段落样式 id → (显示名, 依据, 是否居中)
STYLE_DEFS: dict[str, tuple[str, str, bool]] = {
    "PatentTitle": ("Patent Title", "PatentTitle", True),
    "PatentH1": ("Patent Heading 1", "PatentH1", True),
    "PatentH2": ("Patent Heading 2", "PatentH2", False),
    "PatentH3": ("Patent Heading 3", "PatentH3", False),
    "PatentBody": ("Patent Body", "PatentBody", False),
    "PatentList": ("Patent List", "PatentList", False),
    "PatentTable": ("Patent Table Text", "PatentTable", False),
    "PatentMono": ("Patent Mono", "PatentMono", False),
}

HEADING_STYLE = {1: "PatentH1", 2: "PatentH2", 3: "PatentH3"}

# 内联标记 → (正则, 是否粗体, 是否斜体, 是否等宽)
INLINE_RULES: tuple[tuple[str, bool, bool, bool], ...] = (
    (r"\*\*(.+?)\*\*", True, False, False),
    (r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", False, True, False),
    (r"`(.+?)`", False, False, True),
)

TABLE_SEP_RE = re.compile(r"^\|[\s:|-]+\|$")
PARA_MARK_RE = re.compile(r"^\[(\d{4})\]\s")
TABLE_ROW_RE = re.compile(r"^\|(.+)\|$")
LI_UNORDERED_RE = re.compile(r"^[-*+]\s+(.*)$")
LI_ORDERED_RE = re.compile(r"^(\d+)[.)]\s+(.*)$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
EMPTY_RE = re.compile(r"^\s*$")


# ---------------------------------------------------------------------------
# Markdown 解析
# ---------------------------------------------------------------------------


def parse_markdown(text: str) -> list[tuple[str, object]]:
    """把 Markdown 子集解析成块列表。

    参数:
        text: 草稿全文（已去除 BOM）。

    返回:
        `[(kind, payload), ...]`，kind 取 `title`/`h1`/`h2`/`h3`/`p`/`li`/`oli`/`table`；
        `table` 的 payload 是 `list[list[str]]`（首行为表头），其余 payload 是字符串。

    算法:
        逐行扫描。先判表格（连续的 `|` 行，第二行是分隔行时整块吃掉），再判标题、
        列表项，最后归入段落；空行结束当前段落。

    复杂度:
        O(n)，n 为字符数；行内标记的正则回溯受段落长度限制。

    陷阱:
        `|` 也可能出现在正文里（例如"甲|乙"），所以只有**连续两行**都长得像表格
        且第二行是分隔行时才当表格；否则按普通段落处理，不会吞掉正文。

    参考:
        WordprocessingML 段落模型；见 `references/format.md`。
    """
    lines = text.replace("\ufeff", "").splitlines()
    blocks: list[tuple[str, object]] = []
    i = 0
    while i < len(lines):
        raw = lines[i]
        if EMPTY_RE.match(raw):
            i += 1
            continue

        # 表格：本行是 |...|，下一行是分隔行
        m_row = TABLE_ROW_RE.match(raw.strip())
        if m_row and i + 1 < len(lines) and TABLE_SEP_RE.match(lines[i + 1].strip()):
            rows = [[c.strip() for c in m_row.group(1).split("|")]]
            i += 2
            while i < len(lines):
                m2 = TABLE_ROW_RE.match(lines[i].strip())
                if not m2 or TABLE_SEP_RE.match(lines[i].strip()):
                    break
                rows.append([c.strip() for c in m2.group(1).split("|")])
                i += 1
            blocks.append(("table", rows))
            continue

        m_h = HEADING_RE.match(raw)
        if m_h:
            level = len(m_h.group(1))
            kind = "title" if level == 1 else f"h{min(level - 1, 3)}"
            blocks.append((kind, m_h.group(2).strip()))
            i += 1
            continue

        m_li = LI_UNORDERED_RE.match(raw.strip())
        if m_li:
            blocks.append(("li", m_li.group(1).strip()))
            i += 1
            continue

        m_oli = LI_ORDERED_RE.match(raw.strip())
        if m_oli:
            blocks.append(("oli", f"{m_oli.group(1)}. {m_oli.group(2).strip()}"))
            i += 1
            continue

        # 段落：吃到空行 / 标题 / 表格开头为止
        buf = [raw.strip()]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            if EMPTY_RE.match(nxt) or HEADING_RE.match(nxt) or LI_UNORDERED_RE.match(nxt.strip()):
                break
            if TABLE_ROW_RE.match(nxt.strip()) and i + 1 < len(lines) \
                    and TABLE_SEP_RE.match(lines[i + 1].strip()):
                break
            buf.append(nxt.strip())
            i += 1
        blocks.append(("p", "".join(buf) if _is_cjk_joinable(buf) else " ".join(buf)))
    return blocks


def _is_cjk_joinable(parts: list[str]) -> bool:
    """判断续行应当直接拼接（中文换行不留空格）还是用空格拼接（西文换行）。

    参数:
        parts: 同一段落的各行。

    返回:
        最后一行以中日韩字符或全角标点结尾时返回 True。

    算法:
        只看**前一行**的末字符：中文排版里软换行不产生空格，西文则相反。

    复杂度:
        O(1)。

    陷阱:
        这条规则对中英混排是近似的；但专利草稿里段落换行几乎都发生在中文之间，
        近似带来的差异只会体现在西文段落内的多余/缺失空格，不影响结构。
    """
    if len(parts) < 2:
        return False
    tail = parts[-2][-1:] if parts[-2] else ""
    return bool(tail) and (("\u4e00" <= tail <= "\u9fff") or tail in "，。；：、）》”】！？")


def split_inline(text: str) -> list[tuple[str, bool, bool, bool]]:
    """把一行文本拆成 `[(片段, 粗体, 斜体, 等宽), ...]`。

    参数:
        text: 可能含 `**粗体**`、`*斜体*`、`` `等宽` `` 的文本。

    返回:
        片段列表；标记被解析成对应的格式化标志，标记字符本身不出现在片段里。

    算法:
        逐个规则用非贪婪正则扫描；取"每轮最早出现的匹配"，命中后两侧继续递归，
        保证嵌套（如 `**粗体里有 `码`**`）按出现顺序被拆开。

    复杂度:
        O(len(text) * 规则数)，规则数为常数。

    陷阱:
        未闭合的 `*` 会留在原文里——这是刻意的：宁可显示星号，也不要静默吞字符。
    """
    if not text:
        return []
    best: tuple[int, int, re.Match[str], tuple[bool, bool, bool]] | None = None
    for pattern, bold, italic, mono in INLINE_RULES:
        m = re.search(pattern, text)
        if m is None:
            continue
        if best is None or m.start() < best[0]:
            best = (m.start(), m.end(), m, (bold, italic, mono))
    if best is None:
        return [(text, False, False, False)]
    start, end, m, flags = best
    out: list[tuple[str, bool, bool, bool]] = []
    if start > 0:
        out.append((text[:start], False, False, False))
    for piece in split_inline(m.group(1)):
        out.append((piece[0], piece[1] or flags[0], piece[2] or flags[1], piece[3] or flags[2]))
    out.extend(split_inline(text[end:]))
    return out


# ---------------------------------------------------------------------------
# XML 生成
# ---------------------------------------------------------------------------


def _run_xml(text: str, bold: bool, italic: bool, mono: bool) -> str:
    """生成一个 `w:r` 运行块（含必要的转义）。"""
    rpr = []
    if mono:
        rpr.append(f'<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:eastAsia="{LAYOUT["cjk_font"]}"/>')
    if bold:
        rpr.append("<w:b/>")
    if italic:
        rpr.append("<w:i/>")
    rpr_xml = f"<w:rPr>{''.join(rpr)}</w:rPr>" if rpr else ""
    # xml:space="preserve" 保证中文之间的连续空格不被 Word 吃掉
    return f'<w:r>{rpr_xml}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _para_xml(style: str, runs: list[str], *, outline: int = 0) -> str:
    """生成一个 `w:p` 段落块。"""
    ppr = [f'<w:pStyle w:val="{style}"/>']
    if outline:
        ppr.append(f'<w:outlineLvl w:val="{outline - 1}"/>')
    return f"<w:p><w:pPr>{''.join(ppr)}</w:pPr>{''.join(runs)}</w:p>"


def _text_para(style: str, text: str, *, outline: int = 0) -> str:
    """把一段纯文本按行内标记渲染成段落。"""
    runs = [_run_xml(t, b, i, m) for t, b, i, m in split_inline(text)]
    if not runs:
        runs = [_run_xml("", False, False, False)]
    return _para_xml(style, runs, outline=outline)


def _table_xml(rows: list[list[str]]) -> str:
    """生成 `w:tbl` 表格块。列数取所有行的最大值，缺的格子补空。"""
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    width = 9360  # 正文可用宽度（twips）：A4 减去左右页边距
    grid = "".join(f'<w:gridCol w:w="{width // ncol}"/>' for _ in range(ncol))
    borders = (
        '<w:tblBorders>'
        + "".join(f'<w:{side} w:val="single" w:sz="4" w:space="0" w:color="808080"/>'
                  for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
        + "</w:tblBorders>"
    )
    out = [
        "<w:tbl><w:tblPr>",
        f'<w:tblW w:w="{width}" w:type="dxa"/>',
        borders,
        '<w:tblLayout w:type="fixed"/>',
        "</w:tblPr>",
        f"<w:tblGrid>{grid}</w:tblGrid>",
    ]
    for r_i, row in enumerate(rows):
        cells = []
        for c_i in range(ncol):
            cell = row[c_i] if c_i < len(row) else ""
            style = "PatentTable"
            runs = [_run_xml(t, True, i2, m) for t, _, i2, m in split_inline(cell)] if r_i == 0 \
                else [_run_xml(t, b, i2, m) for t, b, i2, m in split_inline(cell)]
            if not runs:
                runs = [_run_xml("", False, False, False)]
            cells.append(
                f'<w:tc><w:tcPr><w:tcW w:w="{width // ncol}" w:type="dxa"/></w:tcPr>'
                f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr>{"".join(runs)}</w:p></w:tc>'
            )
        header = "<w:trPr><w:tblHeader/></w:trPr>" if r_i == 0 else ""
        out.append(f"<w:tr>{header}{''.join(cells)}</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def _sectpr_xml(layout: dict[str, object]) -> str:
    """生成 `w:sectPr`（纸张、页边距、页脚引用）。"""
    mm = 56.6929  # 1 mm = 56.6929 twips
    return (
        "<w:sectPr>"
        f'<w:pgSz w:w="{int(round(float(layout["page_w_mm"]) * mm))}" '
        f'w:h="{int(round(float(layout["page_h_mm"]) * mm))}"/>'
        f'<w:pgMar w:top="{int(round(float(layout["margin_top_mm"]) * mm))}" '
        f'w:right="{int(round(float(layout["margin_right_mm"]) * mm))}" '
        f'w:bottom="{int(round(float(layout["margin_bottom_mm"]) * mm))}" '
        f'w:left="{int(round(float(layout["margin_left_mm"]) * mm))}" '
        'w:header="851" w:footer="992" w:gutter="0"/>'
        '<w:cols w:space="425"/>'
        '<w:docGrid w:type="lines" w:linePitch="312"/>'
        "</w:sectPr>"
    )


def build_document_xml(blocks: list[tuple[str, object]], layout: dict[str, object] | None = None,
                       *, para_number: bool = False) -> str:
    """把块列表渲染成完整的 `word/document.xml`。

    参数:
        blocks: `parse_markdown` 的输出。
        layout: 排版参数；None 时用模块级 `LAYOUT`。
        para_number: 为 True 时给正文段落自动加 `[0001]` 式段落编号（每段递增）。

    返回:
        完整的 document.xml 文本（含 XML 声明）。

    算法:
        顺序遍历块，按 kind 选择段落样式；`title`/`h1` 居中，`li` 加项目符号字符，
        `table` 交给 `_table_xml`。段落编号只作用于 `p` 块；遇到标题**不重置**计数器
        （一份文件内编号连续，便于审查意见答复里按段引用）；草稿里已经写成
        `[0001]` 的段落**不会**被重复编号，只会把计数器推到它的编号值。

    复杂度:
        O(总字符数 + 表格格数)。

    陷阱:
        `w:docGrid` 会让 Word 按网格对齐中文行；如果行距设置与网格冲突，Word 会
        按网格走。这里把 linePitch 固定为 312 twips（约 15.6pt）以匹配小四 + 1.5 倍行距。

    参考:
        ECMA-376 Part 1，§17.3（段落）、§17.4（表格）、§17.6（节属性）。
    """
    lay = layout or LAYOUT
    body: list[str] = []
    idx = 0
    for kind, payload in blocks:
        if kind == "title":
            body.append(_text_para("PatentTitle", str(payload)))
        elif kind == "h1":
            body.append(_text_para("PatentH1", str(payload), outline=1))
        elif kind == "h2":
            body.append(_text_para("PatentH2", str(payload), outline=2))
        elif kind == "h3":
            body.append(_text_para("PatentH3", str(payload), outline=3))
        elif kind == "li":
            body.append(_text_para("PatentList", "\u2022 " + str(payload)))
        elif kind == "oli":
            body.append(_text_para("PatentList", str(payload)))
        elif kind == "table":
            body.append(_table_xml(payload))  # type: ignore[arg-type]
            # 表格后补一个空段，避免相邻两个表格被 Word 粘成一张
            body.append(_text_para("PatentBody", ""))
        else:
            text = str(payload)
            if para_number and not PARA_MARK_RE.match(text):
                # 草稿里已经写了 [0001] 的段落不再重复编号，只把计数器推到它之后
                idx += 1
                text = f"[{idx:04d}] {text}"
            elif para_number:
                m = PARA_MARK_RE.match(text)
                idx = max(idx, int(m.group(1)) if m else idx)  # type: ignore[union-attr]
            body.append(_text_para("PatentBody", text))
    return (XML_DECL
            + f'<w:document xmlns:w="{W_NS}" xmlns:r="{R_NS}"><w:body>'
            + "".join(body)
            + _sectpr_xml(lay)
            + "</w:body></w:document>")


def build_content_types() -> str:
    """生成 `[Content_Types].xml`。"""
    return (
        XML_DECL
        + f'<Types xmlns="{CT_NS}">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
        "</Types>"
    )


def build_root_rels() -> str:
    """生成 `_rels/.rels`。"""
    return (
        XML_DECL
        + f'<Relationships xmlns="{PKG_REL_NS}">'
        f'<Relationship Id="rId1" Type="{OD_REL_NS}/officeDocument" Target="word/document.xml"/>'
        f'<Relationship Id="rId2" Type="{CP_NS}" Target="docProps/core.xml"/>'
        f'<Relationship Id="rId3" Type="{OD_REL_NS}/extended-properties" Target="docProps/app.xml"/>'
        "</Relationships>"
    )


def build_document_rels() -> str:
    """生成 `word/_rels/document.xml.rels`。"""
    return (
        XML_DECL
        + f'<Relationships xmlns="{PKG_REL_NS}">'
        f'<Relationship Id="rId1" Type="{OD_REL_NS}/styles" Target="styles.xml"/>'
        "</Relationships>"
    )


def build_core_props(title: str, author: str) -> str:
    """生成 `docProps/core.xml`（时间戳固定，保证可复现）。"""
    return (
        XML_DECL
        + f'<cp:coreProperties xmlns:cp="{CP_NS}" xmlns:dc="{DC_NS}"'
        f' xmlns:dcterms="{DCTERMS_NS}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{escape(title)}</dc:title>"
        f"<dc:creator>{escape(author)}</dc:creator>"
        f"<cp:lastModifiedBy>{escape(author)}</cp:lastModifiedBy>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{FIXED_TS}</dcterms:created>'
        f'<dcterms:modified xsi:type="dcterms:W3CDTF">{FIXED_TS}</dcterms:modified>'
        "</cp:coreProperties>"
    )


def build_app_props() -> str:
    """生成 `docProps/app.xml`。"""
    return (
        XML_DECL
        + f'<Properties xmlns="{EP_NS}" xmlns:vt="{VT_NS}">'
        "<Application>invention-patent-skill make_docx.py</Application>"
        "<DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop>"
        "<LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc>"
        "<HyperlinksChanged>false</HyperlinksChanged><AppVersion>16.0000</AppVersion>"
        "</Properties>"
    )


def build_styles_xml(layout: dict[str, object] | None = None) -> str:
    """生成 `word/styles.xml`，包含 docDefaults 与全部段落样式。

    参数:
        layout: 排版参数；None 时用模块级 `LAYOUT`。

    返回:
        完整的 styles.xml 文本。

    算法:
        每个段落样式由三段组成：段落属性（对齐/行距/缩进）、运行属性（字体/字号），
        以及可选的 `w:basedOn="Normal"`。中文用 `w:eastAsia` 单独指定，否则
        Word 会对中文回退到默认字体，输出与预期不符。

    复杂度:
        O(样式数)。

    陷阱:
        字号 `w:sz` 的单位是**半磅**：小四 12pt 要写 24。写成 12 会得到 6pt 的蚊子字。
    """
    lay = layout or LAYOUT
    body_sz = int(lay["body_sz"])
    line = int(lay["line_240"])
    indent = int(lay["first_line_indent"])
    latin = str(lay["latin_font"])
    cjk = str(lay["cjk_font"])

    parts: list[str] = [
        XML_DECL,
        f'<w:styles xmlns:w="{W_NS}">',
        "<w:docDefaults><w:rPrDefault><w:rPr>"
        f'<w:rFonts w:ascii="{latin}" w:hAnsi="{latin}" w:eastAsia="{cjk}" w:cs="{latin}"/>'
        f'<w:sz w:val="{body_sz}"/><w:szCs w:val="{body_sz}"/>'
        "</w:rPr></w:rPrDefault>"
        "<w:pPrDefault><w:pPr>"
        f'<w:spacing w:line="{line}" w:lineRule="auto"/>'
        "</w:pPr></w:pPrDefault></w:docDefaults>",
        # Normal 必须存在：Word 与 LibreOffice 都会引用它
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
        "<w:name w:val=\"Normal\"/>"
        f'<w:pPr><w:spacing w:line="{line}" w:lineRule="auto"/>'
        '<w:jc w:val="both"/></w:pPr>'
        f'<w:rPr><w:rFonts w:ascii="{latin}" w:hAnsi="{latin}" w:eastAsia="{cjk}"/>'
        f'<w:sz w:val="{body_sz}"/></w:rPr></w:style>',
    ]

    size_of = {
        "PatentTitle": int(lay["title_sz"]),
        "PatentH1": int(lay["h1_sz"]),
        "PatentH2": int(lay["h2_sz"]),
        "PatentH3": int(lay["h3_sz"]),
        "PatentBody": body_sz,
        "PatentList": body_sz,
        "PatentTable": body_sz,
        "PatentMono": body_sz,
    }
    font_of = {
        "PatentTitle": str(lay["title_cjk_font"]),
        "PatentH1": str(lay["heading_cjk_font"]),
        "PatentH2": str(lay["heading_cjk_font"]),
        "PatentH3": str(lay["heading_cjk_font"]),
    }
    for style_id, (name, _based, centered) in STYLE_DEFS.items():
        sz = size_of[style_id]
        east = font_of.get(style_id, cjk)
        ppr = [f'<w:spacing w:line="{line}" w:lineRule="auto"']
        if style_id in ("PatentH1", "PatentH2", "PatentH3"):
            ppr.append(' w:before="120" w:after="60"')
        ppr.append("/>")
        ppr.append(f'<w:jc w:val="{"center" if centered else "both"}"/>')
        if style_id in ("PatentBody",) and indent:
            ppr.append(f'<w:ind w:firstLineChars="{indent}" w:firstLine="{int(indent * body_sz / 100)}"/>')
        if style_id == "PatentList":
            ppr.append('<w:ind w:left="480" w:hanging="240"/>')
        if style_id == "PatentTable":
            ppr.append('<w:spacing w:after="0"/>')
        parts.append(
            f'<w:style w:type="paragraph" w:styleId="{style_id}">'
            f'<w:name w:val="{name}"/><w:basedOn w:val="Normal"/>'
            f'<w:qFormat/><w:pPr>{"".join(ppr)}</w:pPr>'
            f'<w:rPr><w:rFonts w:ascii="{latin}" w:hAnsi="{latin}" w:eastAsia="{east}"/>'
            f'<w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/>'
            + ("<w:b/>" if style_id in ("PatentTitle", "PatentH1", "PatentH2", "PatentH3") else "")
            + "</w:rPr></w:style>"
        )
    parts.append("</w:styles>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# 打包
# ---------------------------------------------------------------------------

PART_ORDER = (
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
    "word/_rels/document.xml.rels",
    "word/styles.xml",
    "docProps/core.xml",
    "docProps/app.xml",
)


def write_docx(blocks: list[tuple[str, object]], path: str | Path, *,
               title: str = "", author: str = "invention-patent-skill",
               layout: dict[str, object] | None = None,
               para_number: bool = False) -> Path:
    """把块列表写成 .docx 文件。

    参数:
        blocks: `parse_markdown` 的输出。
        path: 输出文件路径（父目录须已存在，或由本函数创建）。
        title: 写入 docProps 的文档标题（不影响正文）。
        author: 写入 docProps 的作者/最后修改者。
        layout: 排版参数；None 时用 `LAYOUT`。
        para_number: 是否为正文段落自动加 `[0001]` 编号。

    返回:
        实际写入的 `Path`。

    算法:
        固定顺序生成 7 个部件，用 `zipfile.ZipFile(..., ZIP_DEFLATED)` 写入；
        每个条目的时间戳固定为 `ZIP_DATE`、外部属性固定，因此**逐字节可复现**。
        ZIP 内的顺序固定为 `PART_ORDER`，不随字典遍历顺序变化。

    复杂度:
        O(输出体积)。

    陷阱:
        不要用 `zipfile.ZipFile.write()` 直接写磁盘文件——那样会把源文件的 mtime
        带进条目头，输出就不可复现了。必须用 `writestr` 且显式给 `ZipInfo`。
    """
    lay = layout or LAYOUT
    doc_xml = build_document_xml(blocks, lay, para_number=para_number)
    payload = {
        "[Content_Types].xml": build_content_types(),
        "_rels/.rels": build_root_rels(),
        "word/document.xml": doc_xml,
        "word/_rels/document.xml.rels": build_document_rels(),
        "word/styles.xml": build_styles_xml(lay),
        "docProps/core.xml": build_core_props(title, author),
        "docProps/app.xml": build_app_props(),
    }
    out = Path(path)
    if out.parent and not out.parent.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in PART_ORDER:
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            info.create_system = 0
            zf.writestr(info, payload[name].encode("utf-8"))
    out.write_bytes(buf.getvalue())
    return out


# ---------------------------------------------------------------------------
# 体检
# ---------------------------------------------------------------------------


def inspect_docx(path: str | Path) -> dict[str, object]:
    """解析一个 .docx，返回结构与排版摘要（不依赖 Word）。

    参数:
        path: .docx 路径。

    返回:
        字典，键包括 `ok`、`parts`（部件名列表）、`missing`（缺失的必需部件）、
        `paragraphs`（段落数）、`texts`（每段纯文本）、`styles`（用到的样式 id 计次）、
        `tables`（表格数）、`page`（`{w_mm, h_mm}`）、`margins`（`{top,bottom,left,right}` mm）、
        `bytes`、`sha256`、`errors`。

    算法:
        用 `zipfile` 读包，`xml.etree.ElementTree` 解析；命名空间统一取 W_NS 前缀
        `{...}` 拼接，避免依赖具体前缀名。README 的 nice-to-have：全部只读，不改文件。

    复杂度:
        O(文档体积)。

    陷阱:
        自制的包若漏掉 `[Content_Types].xml`，Word 会报"文件已损坏"却不说原因；
        所以这里把必需部件清单做成硬校验，先在本脚本里拦住。
    """
    p = Path(path)
    info: dict[str, object] = {
        "ok": False, "parts": [], "missing": [], "paragraphs": 0, "texts": [],
        "styles": {}, "tables": 0, "page": {}, "margins": {},
        "bytes": p.stat().st_size if p.is_file() else 0, "sha256": "", "errors": [],
    }
    errors: list[str] = info["errors"]  # type: ignore[assignment]
    if not p.is_file():
        errors.append(f"文件不存在：{p}")
        return info
    raw = p.read_bytes()
    info["sha256"] = hashlib.sha256(raw).hexdigest()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        errors.append(f"不是合法的 ZIP/OOXML 包：{exc}")
        return info
    with zf:
        names = zf.namelist()
        info["parts"] = list(names)
        missing = [n for n in PART_ORDER if n not in names]
        info["missing"] = missing
        if missing:
            errors.append(f"缺少必需部件：{missing}")

        def parse(name: str):
            try:
                return ET.fromstring(zf.read(name))
            except (KeyError, ET.ParseError) as exc:
                errors.append(f"{name} 解析失败：{exc}")
                return None

        for name in ("[Content_Types].xml", "_rels/.rels", "word/_rels/document.xml.rels"):
            parse(name)
        if parse("word/styles.xml") is None:
            errors.append("styles.xml 不可用")

        root = parse("word/document.xml")
        if root is not None:
            w = f"{{{W_NS}}}"
            body = root.find(f"{w}body")
            if body is None:
                errors.append("document.xml 没有 w:body")
            else:
                texts: list[str] = []
                styles: dict[str, int] = {}
                tables = 0
                for el in body.iter():
                    if el.tag == f"{w}p":
                        texts.append("".join(t.text or "" for t in el.iter(f"{w}t")))
                    elif el.tag == f"{w}tbl":
                        tables += 1
                    elif el.tag == f"{w}pStyle":
                        sid = el.get(f"{w}val") or ""
                        styles[sid] = styles.get(sid, 0) + 1
                info["texts"] = texts
                info["paragraphs"] = len(texts)
                info["styles"] = styles
                info["tables"] = tables
                sect = body.find(f"{w}sectPr")
                if sect is not None:
                    mm = 1.0 / 56.6929
                    pgsz = sect.find(f"{w}pgSz")
                    pgmar = sect.find(f"{w}pgMar")
                    if pgsz is not None:
                        info["page"] = {
                            "w_mm": round(int(pgsz.get(f"{w}w") or 0) * mm, 1),
                            "h_mm": round(int(pgsz.get(f"{w}h") or 0) * mm, 1),
                        }
                    if pgmar is not None:
                        info["margins"] = {
                            k: round(int(pgmar.get(f"{w}{k}") or 0) * mm, 1)
                            for k in ("top", "bottom", "left", "right")
                        }
                else:
                    errors.append("document.xml 缺少 w:sectPr（纸张/页边距无从判断）")
    info["ok"] = not errors
    return info


# ---------------------------------------------------------------------------
# 自检
# ---------------------------------------------------------------------------


def _self_test(failures: list[str]) -> None:
    """跑固件测试。失败项写进 `failures`（不抛异常，便于一次看完所有问题）。"""

    def check(name: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"PASS  {name}")
        else:
            print(f"FAIL  {name}" + (f"  —— {detail}" if detail else ""))
            failures.append(name)

    # 1) 段落解析
    md = (
        "# 说明书\n\n"
        "## 技术领域\n\n"
        "[0001] 本发明涉及一种示例装置。\n"
        "具体地，涉及一种用于演示的装置。\n\n"
        "## 具体实施方式\n\n"
        "- 第一步\n"
        "- 第二步\n\n"
        "1. 权利要求一\n"
        "2. 权利要求二\n\n"
        "| 部件 | 标号 |\n|---|---|\n| 壳体 | 1 |\n| 电机 | 2 |\n"
    )
    blocks = parse_markdown(md)
    kinds = [k for k, _ in blocks]
    check("标题为 title", kinds[0] == "title", str(kinds[:3]))
    check("二级标题为 h1", kinds[1] == "h1", str(kinds[:3]))
    check("解析出表格", "table" in kinds, str(kinds))
    check("解析出无序项", "li" in kinds, str(kinds))
    check("解析出有序项", "oli" in kinds, str(kinds))
    check("表格 3 行", any(k == "table" and len(v) == 3 for k, v in blocks),
          str([v for k, v in blocks if k == "table"]))
    para = [v for k, v in blocks if k == "p"]
    check("中文续行不留空格", any("装置。具体地" in str(v) for v in para), str(para[:2]))

    # 2) 行内标记
    runs = split_inline("前**粗**中`码`后")
    flags = [(t, b, i, m) for t, b, i, m in runs]
    check("粗体解析", ("粗", True, False, False) in flags, str(flags))
    check("等宽解析", ("码", False, False, True) in flags, str(flags))
    check("未闭合星号保留", "".join(t for t, _, _, _ in split_inline("a*b")) == "a*b")
    check("嵌套标记", any(b and n for _, b, _, n in split_inline("**a`b`**")),
          str(split_inline("**a`b`**")))

    # 3) XML 转义
    escaped = build_document_xml([("p", "a<b>&c\"d")])
    check("XML 转义 <", "a&lt;b&gt;" in escaped)
    check("XML 转义 &", "&amp;c" in escaped)

    # 4) 结构体检 + 可复现
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        draft = tmp / "draft.md"
        draft.write_text(md, encoding="utf-8")
        out1 = write_docx(parse_markdown(draft.read_text(encoding="utf-8")), tmp / "a.docx",
                          title="说明书", para_number=True)
        out2 = write_docx(parse_markdown(draft.read_text(encoding="utf-8")), tmp / "b.docx",
                          title="说明书", para_number=True)
        info = inspect_docx(out1)
        check("体检通过", bool(info["ok"]), str(info["errors"]))
        check("7 个部件齐全", len(info["parts"]) == 7 and not info["missing"],
              str(info["parts"]))
        check("段落数 > 5", int(info["paragraphs"]) > 5, str(info["paragraphs"]))
        check("识别到 1 张表", int(info["tables"]) == 1, str(info["tables"]))
        styles = info["styles"]
        check("用到标题样式", "PatentTitle" in styles and "PatentH1" in styles, str(styles))
        check("段落编号生效",
              any(str(t).startswith("[0001]") for t in info["texts"]),  # type: ignore[union-attr]
              str(info["texts"][:3]))
        pre = tmp / "pre.docx"
        write_docx(parse_markdown("[0001] 甲\n\n乙\n\n丙\n"), pre, para_number=True)
        texts2 = list(inspect_docx(pre)["texts"])  # type: ignore[arg-type]
        check("已有段落号不重复编号", texts2[0] == "[0001] 甲", repr(texts2[:1]))
        check("段落号接着已有编号往下走",
              texts2[1] == "[0002] 乙" and texts2[2] == "[0003] 丙", repr(texts2[1:3]))
        page = info["page"]
        check("纸张为 A4 宽 210mm", abs(float(page.get("w_mm", 0)) - 210.0) < 0.6, str(page))
        check("纸张为 A4 高 297mm", abs(float(page.get("h_mm", 0)) - 297.0) < 0.6, str(page))
        margins = info["margins"]
        check("左页边距 25mm", abs(float(margins.get("left", 0)) - 25.0) < 0.6, str(margins))
        check("右页边距 15mm", abs(float(margins.get("right", 0)) - 15.0) < 0.6, str(margins))
        check("逐字节可复现", info["sha256"] == inspect_docx(out2)["sha256"],
              f'{info["sha256"][:12]} vs {inspect_docx(out2)["sha256"][:12]}')
        check("PDF 之外无残留临时文件", not list(tmp.glob("*.tmp")))

        # 5) 坏包能被抓住
        bad = tmp / "bad.docx"
        bad.write_bytes(b"not a zip at all")
        bad_info = inspect_docx(bad)
        check("坏包被拒绝", not bad_info["ok"] and bool(bad_info["errors"]), str(bad_info["errors"]))

        incomplete = tmp / "incomplete.docx"
        with zipfile.ZipFile(incomplete, "w") as zf:
            zf.writestr("word/document.xml", "<w:document/>")
        inc_info = inspect_docx(incomplete)
        check("缺部件被报出", "[Content_Types].xml" in inc_info["missing"],
              str(inc_info["missing"]))

    # 6) 样式表
    styles_xml = build_styles_xml()
    check("字号为半磅（小四=24）", '<w:sz w:val="24"/>' in styles_xml)
    check("指定了中文字体", 'w:eastAsia="SimSun"' in styles_xml)
    check("行距 1.5 倍=360", 'w:line="360"' in styles_xml)
    check("所有样式都有定义",
          all(f'w:styleId="{s}"' in styles_xml for s in STYLE_DEFS), "缺样式")


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    参数:
        argv: 参数列表；None 时取 `sys.argv[1:]`。

    返回:
        进程退出码：0 成功，1 失败，2 用法错误。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):  # pragma: no cover
            pass

    parser = argparse.ArgumentParser(
        prog="make_docx.py",
        description="把专利文件 Markdown 草稿转成 .docx（纯标准库，输出可复现）。",
        epilog="例：python scripts/make_docx.py 说明书.md -o 说明书.docx --para-number")
    parser.add_argument("draft", nargs="?", help="Markdown 草稿路径")
    parser.add_argument("-o", "--out", help="输出 .docx 路径（默认与草稿同名）")
    parser.add_argument("--title", default="", help="写入文档属性的标题")
    parser.add_argument("--author", default="invention-patent-skill", help="写入文档属性的作者")
    parser.add_argument("--para-number", action="store_true",
                        help="为正文段落自动加 [0001] 式段落编号")
    parser.add_argument("--inspect", metavar="DOCX", help="只体检一个已有 .docx，不生成")
    parser.add_argument("--self-test", action="store_true", help="跑内置固件测试")
    parser.add_argument("-q", "--quiet", action="store_true", help="少打印")
    args = parser.parse_args(argv)

    if args.self_test:
        failures: list[str] = []
        _self_test(failures)
        total = 28
        print(f"\n固件测试：{total - len(failures)}/{total} 通过")
        return 1 if failures else 0

    if args.inspect:
        info = inspect_docx(args.inspect)
        print(f"文件：{args.inspect}")
        print(f"体积：{info['bytes']} 字节  SHA-256：{info['sha256']}")
        print(f"部件：{len(info['parts'])} 个" + (f"  缺：{info['missing']}" if info["missing"] else ""))
        print(f"段落：{info['paragraphs']}  表格：{info['tables']}")
        print(f"样式：{info['styles']}")
        print(f"纸张：{info['page']}  页边距(mm)：{info['margins']}")
        for e in info["errors"]:  # type: ignore[union-attr]
            print(f"[ERROR] {e}")
        if not args.quiet:
            for t in list(info["texts"])[:8]:  # type: ignore[arg-type]
                print(f"  | {t}")
        print(f"\n结果：{'通过' if info['ok'] else '失败'}")
        return 0 if info["ok"] else 1

    if not args.draft:
        parser.print_usage(sys.stderr)
        print("错误：请给一个 Markdown 草稿路径，或用 --self-test / --inspect", file=sys.stderr)
        return 2

    src = Path(args.draft)
    if not src.is_file():
        print(f"错误：找不到 {src}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else src.with_suffix(".docx")
    blocks = parse_markdown(src.read_text(encoding="utf-8"))
    title = args.title or (next((str(v) for k, v in blocks if k == "title"), "") or src.stem)
    write_docx(blocks, out, title=title, author=args.author, para_number=args.para_number)
    info = inspect_docx(out)
    if not args.quiet:
        print(f"写出 {out}")
        print(f"  段落 {info['paragraphs']}  表格 {info['tables']}  体积 {info['bytes']} 字节")
        print(f"  SHA-256 {info['sha256']}")
    if not info["ok"]:
        for e in info["errors"]:  # type: ignore[union-attr]
            print(f"[ERROR] {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
