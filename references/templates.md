# 模板与工具链

本文件只讲"**用什么、怎么跑通**"。法条与形式要求见 `references/format.md`、`references/claims.md`、`references/specification.md`、`references/drawings.md`。

---

## 一、三条产出路线

| 路线 | 适用 | 产出 | 入口 |
|---|---|---|---|
| **Markdown 草稿 → Word** | 绝大多数用户；要交电子申请 | `.docx`（CNIPA 常见排版） | `scripts/make_docx.py` |
| **LaTeX 模板** | 需要精确版式、批量生成、公式多 | `.pdf` / `.tex` | `assets/latex/cnipa/` |
| **手写 Word** | 只想改现成文档 | `.docx` | — |
| **一键整包** | 一次要齐「分部件 Word + LaTeX 源 + PDF + 自检报告」 | 一整个输出目录 | `scripts/build_all.py`（见 §五） |

无论走哪条，**先用 Markdown 把内容写出来**：`scripts/check_patent.py` 的机械自检只认 Markdown 草稿，它是内容层的第一道闸门。

```bash
python scripts/check_patent.py 我的申请.md --init       # 生成带占位提示的骨架
python scripts/check_patent.py 我的申请.md              # 逐项自检
python scripts/check_patent.py 我的申请.md --strict     # 警告也算失败（提交前用）
python scripts/check_patent.py 我的申请.md --json       # 给工具/CI 用的结构化输出
```

`--init` 生成整份申请文件的说明书骨架。骨架是**形式**骨架，技术内容必须自己写。
权利要求书与摘要各有专项骨架，见 `assets/claims-template.md` 与 `assets/abstract-template.md`。

---

## 二、LaTeX 模板：`assets/latex/cnipa/`

一份可直接编译的发明专利申请文件模板（`main.tex` + `refs.bib`）。它的结构恰好对应法定顺序：

```
说明书摘要  →  权利要求书  →  说明书  →  说明书附图  →  引证文件
```

### 2.1 关键宏命令（改内容时只需要用这几个）

| 命令 | 用途 | 示例 |
|---|---|---|
| `\parttitle{说明书摘要}` | **一级部件**标题；同时向目录/顺序检查登记 | `\parttitle{权利要求书}` |
| `\sect{技术领域}` | 说明书**五部分**的小标题 | `\sect{具体实施方式}` |
| `\spara{…}` | 说明书正文段落（**自动递增 `[0001]` 式段号**） | `\spara{本发明涉及……}` |
| `\npara{…}` | 不带段号的普通段落 | `\npara{注：……}` |
| `\claim{1}{…}` | 一项权利要求（编号 + 正文） | `\claim{2}{根据权利要求 1 所述的……}` |
| `\mref{1}` | 附图标记，渲染为**（1）** | `壳体\mref{1}` |
| `\cmpfile{key}` | 引证文件（渲染为 `\cite`，key 对应 `refs.bib`） | `\cmpfile{examplepatent}` |

- 文档类：`\documentclass[12pt,a4paper,fontset=windows]{ctexart}`。
- 版式：`geometry` 为 **A4、上 25 / 左 25 / 右 15 / 下 15 mm**——与《专利审查指南》第五部分第一章 4.1～4.3 一致，**不要随手改**（`scripts/check_latex.py` 会核对这四项）。
- 引证文件：`\bibliographystyle{unsrt}` + `\bibliography{refs}`，并把 `\refname` 重定义为「引证文件」。

### 2.2 编译（顺序不能错）

```bash
xelatex main.tex
bibtex  main
xelatex main.tex
xelatex main.tex
```

- **必须用 XeLaTeX**：模板基于 `ctex`，用 pdfLaTeX 会在字体上直接失败。
- 第二遍 xelatex 是为了让 `\ref` / 目录收敛，第三遍是为了让引用与页码稳定。
- 用 `latexmk -xelatex main.tex` 也可以，但要确认它跑满了 bibtex 与两遍 xelatex。

### 2.3 中文字体

- 模板默认 `fontset=windows`（调用 Windows 自带宋体/黑体），Windows 上开箱即用。
- **在 Linux/CI 上编译时不要改仓库里的模板**：`scripts/check_latex.py` 会**在临时副本里**把 `fontset=windows` 换成 TeX 发行版自带的 `fandol` 再编译，仓库文件一个字不动。
- 如果本地报 `The font "SimSun" cannot be found`，把 `fontset` 改成 `auto` 或 `fandol`，**不要手写字体文件名**。

---

## 三、LaTeX 自检：`scripts/check_latex.py`

它做两件事：**真编译**（在系统临时目录的副本里，仓库不留 `.aux/.log/.pdf`）+ **查 7 类结构性合规**（查 `.tex` 源码，不查 PDF）。

```bash
python scripts/check_latex.py                 # 本机没装 TeX 就跳过（退出码 0）
python scripts/check_latex.py --require       # 缺 TeX 视为失败（CI 用这个）
python scripts/check_latex.py --self-test     # 只测解析逻辑，不需要装 TeX
python scripts/check_latex.py --keep          # 保留临时目录，便于翻 .log
python scripts/check_latex.py --keep-fontset  # 不替换字体集，编译仓库原件
python scripts/check_latex.py --tex-dir DIR   # 把 DIR 加到 PATH 最前面找引擎
```

7 类结构检查：

1. **一级部件顺序**：说明书摘要 → 权利要求书 → 说明书 → 说明书附图 → 引证文件；
2. **说明书五部分顺序**：技术领域 → 背景技术 → 发明内容 → 附图说明 → 具体实施方式；
3. **版式**：A4、上 25 / 左 25 / 右 15 / 下 15 mm；
4. **权利要求**：编号从 1 起连续、首项为独立权利要求、从属只引用在前的权利要求；
5. **摘要字数**：不超过 300 字（含标点）；
6. **措辞**：摘要/说明书不得出现「如权利要求……所述」，权利要求不得出现「如说明书……所述」「如图……所示」，以及商业性宣传用语；
7. **附图标记**：正文用到的每个标记都要在「附图标记说明」里列出。

> 这些检查**只做机械校验**，不评价新颖性、创造性和保护范围。法条原文以 `references/` 为准。

---

## 四、Markdown 草稿 → Word：`scripts/make_docx.py`

纯标准库拼 OOXML，**不依赖 Word / pandoc / LibreOffice**，同样的输入得到**逐字节相同**的输出。

```bash
python scripts/make_docx.py 我的申请.md              # 输出 我的申请.docx
python scripts/make_docx.py 我的申请.md -o 申请文件.docx
python scripts/make_docx.py 我的申请.md --para-number # 正文段落自动加 [0001]
python scripts/make_docx.py --inspect 申请文件.docx    # 只体检，不生成
python scripts/make_docx.py --self-test
```

- 支持的 Markdown 子集：`#/##/###` 标题、普通段落、`- ` 无序项、`1. ` 有序项、`| a | b |` 表格、`**粗体**`、`*斜体*`、`` `等宽` ``。**故意只做这些**，避免半吊子实现。
- 排版参数集中在脚本里的 `LAYOUT`，那是**代理实务常见排版**，不是法定强制值；强制性要求见 `references/format.md`。
- 退出码：`0` 通过、`1` 失败、`2` 用法错误。

---

## 五、一键路径：`scripts/gen_draft.py` 与 `scripts/build_all.py`

前面几节是「一步一件产出」。这两个脚本把手工步骤串成两条一键路径，**仍然复用** `check_patent.py` / `check_latex.py` 的规则，不另立一套标准。

### 5.1 交底书 → 草稿：`scripts/gen_draft.py`

```bash
python scripts/gen_draft.py --template > 交底书.md          # 打印交底书填空模板
python scripts/gen_draft.py 交底书.md -o 我的申请.md         # 生成草稿（并立刻自检一遍）
python scripts/gen_draft.py 交底书.md -o 我的申请.md --strict      # 「需要复核」也算失败
python scripts/gen_draft.py 交底书.md -o 我的申请.md --allow-gaps  # 明知有洞，先出一版
```

- 交底书用 `##` 分节，节名容错（`技术领域`／`所属技术领域`、`背景技术`／`现有技术`……）。
- 输出就是 §一 说的那份 Markdown 草稿，可直接接 `check_patent.py` 与 §四 的 `make_docx.py`。
- **不编造技术内容**：交底书没给的一律留成显式缺口（`（请补：…）`）并逐条列出，有缺口时退出码为 1；`--allow-gaps` 只改退出码、不改内容。

### 5.2 草稿 → 整包：`scripts/build_all.py`

```bash
python scripts/build_all.py 我的申请.md -o 输出目录                 # Word + LaTeX + 自检报告
python scripts/build_all.py 我的申请.md -o 输出目录 --pdf           # 额外真编译 PDF
python scripts/build_all.py 我的申请.md -o 输出目录 --no-para-number # 电子申请：关掉说明书段号
python scripts/build_all.py 我的申请.md -o 输出目录 --keep-fontset   # 不替换 fontset，编仓库原件
```

产出：`01-说明书摘要.docx` / `02-权利要求书.docx` / `03-说明书.docx` / `04-说明书附图.docx`（**按 CNIPA 电子申请的部件分别成文**）、四个部件各自的 `src/*.md`、`latex/main.tex` + `latex/refs.bib`、`自检报告.txt` / `自检报告.json`、`manifest.json`（相对路径 + 字节数 + SHA-256）。

- 报告分四节：草稿机械自检（`check_patent.py`）、LaTeX **版式结构**自检（`check_latex.py` 的结构性检查，**不编译**）、生成说明、**仍需人工处理的事项**。
- `--pdf` 时用本机 xelatex 走 `xelatex → bibtex → xelatex ×2`，并把 `fontset=windows` 换成 `fandol` 再编（Linux 上也能过）；没有 `\cite` 时 bibtex 的非零退出码属正常噪音。
- 退出码：`2` 用法或输入问题（缺发明名称、缺权利要求书或说明书），`1` 自检/结构/编译不过，`0` 通过。
- **"一键"只省手工，不省判断**：报告不掩盖问题，缺附图、引证文件为空等都会明写出来。

---

## 六、常见问题

| 症状 | 原因 | 处理 |
|---|---|---|
| `File 'ctexart.cls' not found` | 没装 `ctex` 宏包 | 装完整 TeX 发行版（TeX Live / MiKTeX），不要只装最小集合 |
| 中文变成方框/乱码 | 用了 pdfLaTeX，或字体集选错 | 改用 `xelatex`；`fontset` 用 `windows`（Windows）或 `fandol`（Linux） |
| `Citation 'xxx' undefined` | 没跑 bibtex，或 key 不在 `refs.bib` | 按 xelatex → bibtex → xelatex ×2 顺序重跑 |
| 引用/页码是 `??` | xelatex 遍数不够 | 再跑一遍 xelatex |
| `make_docx.py` 报"用法错误"（退出码 2） | 参数或输入路径不对 | 先跑 `python scripts/make_docx.py --self-test` 确认脚本本身正常 |
| 转换后的 docx 版式与期望不符 | `LAYOUT` 是常见排版而非法定值 | 按 `references/format.md` §3 手工调整，或改用 LaTeX 模板 |
| check_patent 报"附图标记在正文用了但没列出" | 删改后没同步 | 按 `references/drawings.md` §3 补全「附图标记说明」 |
| `build_all.py` 报"用法错误"（退出码 2） | 草稿缺「发明名称」，或缺「权利要求书 / 说明书」 | 先跑 `python scripts/check_patent.py 我的申请.md` 看缺哪个部件；部件不齐打不出包 |
| `build_all.py --pdf` 打印 `bibtex 退出码 1` | 正文里没有 `\cite`（引证文件为空） | 属**正常噪音**，脚本按「生成说明」处理、不影响退出码；补了 `refs.bib` 与 `\cmpfile` 后自然消失 |
| `gen_draft.py` 退出码 1，但生成的草稿看着能读 | 交底书有字段没给，草稿里留了 `（请补：…）` | 读它打印的缺口清单，补交底书后重跑；确要先出一版再加 `--allow-gaps` |

---

## 七、待核实项

- **`LAYOUT` 中的具体字号、行距取值**是否与《专利审查指南》第五部分第一章 5.2 的"字高不低于 3.5 毫米、行距 2.5 毫米至 3.5 毫米"逐项对应，未逐字核对；转换后如需严格达标，请以指南原文为准（见 `references/sources.md`）。
- **引证文件（参考文献）在法定申请文件中的地位**：法定组成文件为请求书、说明书、说明书摘要、权利要求书（`references/format.md` §2）；本模板把"引证文件"单列为一个一级部件，是**版式惯例**而非独立法定文件，此处未核实其装订顺序要求。
