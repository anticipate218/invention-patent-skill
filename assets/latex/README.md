# LaTeX 模板：中国发明专利申请文件（CNIPA）

一套**开箱即用、已在 Windows + MiKTeX 上实际编译通过**的发明专利申请文件模板。

| 目录 | 内容 | 编译器 |
|---|---|---|
| `cnipa/` | `main.tex` + `refs.bib` | **XeLaTeX**（不能用 pdfLaTeX） |

目录里**只有两个文件**：`main.tex` + `refs.bib`（`main.pdf` 是编译产物，供比对版式用）。
没有共享 preamble、不需要 `\input` 多文件结构——拷进工作目录就能编译。

> **边界声明**：模板里的技术内容（「一种示例装置」）全部是**演示用示例**，不是真实申请。
> 排版参数照《专利审查指南》第五部分第一章 4.1～5.6 设置；规则依据见 `references/format.md`。
> 本模板**不是**国知局的官方格式文件，正式提交前请用 CNIPA 官方渠道核对，并建议交由执业代理师复核。

---

## 一、编译

```bash
xelatex -interaction=nonstopmode main.tex
bibtex  main
xelatex -interaction=nonstopmode main.tex
xelatex -interaction=nonstopmode main.tex
```

一行版：`latexmk -xelatex main.tex`（需要系统里有 `perl`；MiKTeX 自带的 `latexmk` 是包装脚本，
没有 Perl 会报 `MiKTeX could not find the script engine 'perl'`——这时直接用上面四行）。

- **必须 XeLaTeX**：模板基于 `ctex`，靠 XeLaTeX 调用系统字体；用 pdfLaTeX 会直接报错。
- 四步缺一不可：`bibtex` 处理引证文件，之后两遍 xelatex 让引用、页码、`lastpage` 收敛。
- 自动脚本 `python scripts/check_latex.py --require` 内部跑的就是这四步。

### 在 Overleaf 上编译

1. 上传 `main.tex` 与 `refs.bib`（同一层目录）。
2. `Menu → Compiler` 选 **XeLaTeX**。
3. **必须改字体**：把 `fontset=windows` 改成 `fontset=fandol`（Overleaf/Linux 没有 Windows 自带字体）。
4. 参考文献要跑一次 BibTeX，或把 Recompile 下拉设为 "Recompile from scratch"。

---

## 二、依赖宏包

全部是 TeX Live / MiKTeX **完整版自带**宏包，模板不依赖任何外部图片文件（附图位置是占位框）。

| 用途 | 宏包 |
|---|---|
| 中文排版 | `ctex`（`ctexart`） |
| 版面 | `geometry` |
| 页眉页脚 | `fancyhdr`、`lastpage` |
| 数学 | `amsmath`、`amssymb` |
| 三线表与表格 | `booktabs`、`array` |
| 图 | `graphicx` |
| 列表 | `enumitem` |
| 超链接 | `hyperref` |
| 参考文献 | BibTeX 样式 `unsrt` |

精简安装缺宏包时：

```bash
mpm --install=ctex,geometry,fancyhdr,lastpage,amsmath,amssymb,booktabs,array,graphicx,enumitem,hyperref
```

---

## 三、宏命令（改内容只需要用这几个）

| 命令 | 用途 | 示例 |
|---|---|---|
| `\parttitle{说明书摘要}` | **一级部件**标题（三号黑体居中） | `\parttitle{权利要求书}` |
| `\sect{技术领域}` | 说明书**五部分**的小标题（小四黑体顶格） | `\sect{具体实施方式}` |
| `\spara{…}` | 说明书正文段落，**自动递增 `[0001]` 式段号** | `\spara{本发明涉及……}` |
| `\npara{…}` | 不带段号的普通段落 | `\npara{注：……}` |
| `\claim{1}{…}` | 一项权利要求（编号 + 正文） | `\claim{2}{根据权利要求 1 所述的……}` |
| `\mref{1}` | 附图标记，渲染为**（1）** | `壳体\mref{1}` |
| `\cmpfile{key}` | 引证文件（渲染为 `\cite`，key 对应 `refs.bib`） | `\cmpfile{examplepatent}` |

一级部件的排列顺序 **照抄即可**，不要调换：

```
说明书摘要  →  权利要求书  →  说明书  →  说明书附图  →  引证文件
```

正文结构与法条依据：`references/format.md` §2、`references/claims.md`、`references/specification.md`。

---

## 四、两个草稿开关（正式提交前必须确认状态）

模板里有两个草稿开关，**默认状态见下表**，正式提交前必须逐项确认：

| 开关 | 位置 | 为什么必须关 |
|---|---|---|
| `\paranumbertrue` | 第 59 行附近 | 官方《关于规范提交专利电子申请的指引》要求**说明书不应添加任何形式的段落编号**——新申请 XML 的段号由系统自动生成。段号只在草稿阶段方便按段答复审查意见。改成 `\paranumberfalse`。 |
| `\showtocfalse` | 第 51 行附近 | 申请文件**不要求目录**。模板默认已关；若为了自查临时改成 `\showtoctrue`，**提交前记得改回来**。 |

另外，题头的「发明专利申请文件（草稿）」占位段、`\draftapplicant` / `\draftdocket` 元信息，
正式提交时都应删除或替换为真实内容。

---

## 五、版式参数与依据

`geometry` 设为 **A4、上 25 / 左 25 / 右 15 / 下 15 mm**（外加 `headsep=5mm`、`footskip=8mm`），
与《专利审查指南》第五部分第一章一致：

| 项 | 标准 | 依据 |
|---|---|---|
| 规格 | 297 毫米 × 210 毫米（A4） | 指南第五部分第一章 4.2 |
| 上 / 左 / 右 / 下页边距 | 25 / 25 / 15 / 15 毫米 | 指南第五部分第一章 4.3 |
| 字体 | 宋体、仿宋体或楷体，不得草体 | 指南第五部分第一章 5.2 |
| 字高 | 不低于 3.5 毫米 | 指南第五部分第一章 5.2 |
| 行距 | 2.5 毫米至 3.5 毫米 | 指南第五部分第一章 5.2 |
| 页码 | 各文件**分别**用阿拉伯数字顺序编号，页脚居中 | 指南第五部分第一章 5.6 |
| 字体颜色 | 黑色 | 指南第五部分第一章 5.5 |

**不要随手改这四项页边距**：`scripts/check_latex.py` 会逐项核对。
`--keep-fontset` 时模板里 `fontset=windows` 生效，`--keep-fontset` 之外脚本会在**临时副本**里
换成 `fandol` 再编译，仓库文件一个字节都不动。

> 「分别连续」四个字要记住：说明书一套页码、权利要求书另一套页码，不要全书从头编到尾。

---

## 六、常见编译错误

| 现象 | 原因 | 解决 |
|---|---|---|
| `File 'ctexart.cls' not found` | 没装 `ctex` 宏包 | 装完整 TeX 发行版，不要只装最小集合 |
| `! Font \... not loadable` / `Font "SimSun" cannot be found` | 没有 Windows 字体的系统上用了 `fontset=windows` | 改成 `fontset=fandol`（Linux/macOS/Overleaf）或 `auto`；**不要手写字体文件名** |
| 中文变方框/乱码 | 用 pdfLaTeX 编了中文 | 换 XeLaTeX |
| `Unicode character ... not set up for use with LaTeX` | pdfLaTeX 文档里放了中文 | 同上 |
| `Citation 'xxx' undefined`、正文引用显示 `[?]` | 没跑 `bibtex`，或 key 不在 `refs.bib` | 按 xelatex → bibtex → xelatex ×2 重跑 |
| `I found no \citation commands` | 正文里没有任何 `\cite` | 补 `\cmpfile{...}` 后再跑 |
| 页码「共 ? 页」 | `lastpage` 需要第二遍 | 再编译一到两遍 |
| `Missing number, treated as zero` | 在 `\ifnum` 后直接用了 `\value{...}` | 模板已改用 `\thespecpara`（第 66 行注释有说明） |
| `Overfull \hbox ... too wide` | 某行太宽 | 不影响编译；用 `\sloppy` 或调整换行 |
| `security risk: running with elevated privileges` | MiKTeX 检测到管理员权限运行 | 提示性警告，不影响结果 |
| 目录里一堆 `.aux/.log/.out/.bbl` | LaTeX 中间文件 | 正常；只提交最终的 `main.pdf` |

---

## 七、验证记录

**不用信这段文字——自己跑一条命令就能复现**：

```bash
python scripts/check_latex.py                 # 本机没装 TeX 就跳过（退出码 0）
python scripts/check_latex.py --require       # 缺 TeX 视为失败（CI 用这个）
python scripts/check_latex.py --keep          # 保留临时目录，便于翻 .log
python scripts/check_latex.py --keep-fontset  # 不替换字体集，编译仓库原件
python scripts/check_latex.py --self-test     # 不需要装 TeX，只测解析逻辑
```

`check_latex.py` 在系统临时目录里**另建副本**编译，**不在仓库内编译**（所以仓库里永远不出现
`.aux/.log/.pdf`），然后逐项核对 7 类结构性合规。

本仓库在 **Windows + MiKTeX（XeTeX）** 上的实测结果：

| 项 | 结果 |
|---|---|
| 编译 | 通过（exit 0） |
| 页数 | **4 页**，A4 |
| PDF 体积 | 约 **153 700 B**（每次编译有几十字节抖动，来自时间戳与 PDF ID） |
| 硬错误 / 未定义引用 | 0 |
| Overfull / Underfull | 0 / 0 |
| 字体 | 临时副本已把 `fontset=windows` 换成 `fontset=fandol`（1 处） |

7 类结构检查全部 ✓：

```
✓ 一级部件顺序：说明书摘要 → 权利要求书 → 说明书 → 说明书附图 → 引证文件
✓ 说明书五部分：技术领域 → 背景技术 → 发明内容 → 附图说明 → 具体实施方式
✓ 版式：A4 210×297mm；top=25mm、left=25mm、right=15mm、bottom=15mm
✓ 权利要求：共 5 项，独立 1 项，编号 1–5
✓ 摘要字数：188 字（上限 300）
✓ 措辞：无被禁引用语；启发式宣传语清单 6 项全未命中
✓ 附图标记：7 个，正文与说明一致
```

`.github/workflows/ci.yml` 的 `latex` job 装 TeX Live 后跑 `python scripts/check_latex.py --require`：
**`--require` 表示「没装 TeX 就报错退出」，绝不允许因为环境缺引擎就静默跳过**。

> PDF 体积每次编译会变几十字节（时间戳 + PDF ID），页数则完全稳定。若你的数字与上表差
> 几十字节，属正常抖动。

---

## 八、相关文档

- 申请文件的形式要求（纸张、页边距、字体、页码、各部分顺序）：`references/format.md`
- 权利要求撰写规则：`references/claims.md`
- 说明书五部分的写法：`references/specification.md`
- 附图绘制要求：`references/drawings.md`
- Markdown 版申请文件骨架（可与本模板对照填写）：`assets/patent-outline.md`
- 权利要求 / 摘要骨架：`assets/claims-template.md`、`assets/abstract-template.md`
- 一页速查：`assets/cheatsheet.md`
- 模板与工具链说明：`references/templates.md`
