# invention-patent-skill

[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Agent Skill](https://img.shields.io/badge/Agent%20Skill-spec%20compliant-blue.svg)](https://agentskills.io/specification)
[![CI](https://github.com/anticipate218/invention-patent-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/anticipate218/invention-patent-skill/actions/workflows/ci.yml)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](scripts/check_patent.py)

一个给 AI 助手用的 **发明专利申请技能**（Agent Skill）：把**技术交底书**变成一份形式合规的发明专利申请文件，并在收到审查意见后**在允许的边界内**正确修改与答复。

> 它不是一个"自动生成专利"的工具，而是一位**把法条、细则与审查指南钉在每一步上的撰写脚手架 + 形式自检器**：帮你提炼发明点、布局权利要求、把说明书写到"能够实现"，并在提交前逐项卡住那些会被补正的形式缺陷。

**English**: An agent skill for drafting Chinese invention patent applications — claims, specification, abstract and drawings — plus office-action response review, grounded in the currently effective Patent Law, Implementing Regulations and Examination Guidelines. It is a drafting scaffold and formal-compliance checker, **not** a substitute for a licensed patent attorney.

---

## 目录

- [快速开始](#快速开始)
- [为什么需要它](#为什么需要它)
- [它能做什么、不能做什么](#它能做什么不能做什么)
- [法律依据的版本问题（必读）](#法律依据的版本问题必读)
- [下载与安装](#下载与安装)
- [工作流：从交底书到申请文件](#工作流从交底书到申请文件)
- [工具与产出物](#工具与产出物)
- [质量保障](#质量保障)
- [仓库结构](#仓库结构)
- [最常被引错的五处规则](#最常被引错的五处规则)
- [与同类项目的关系](#与同类项目的关系)
- [免责声明](#免责声明)
- [License](#license)

---

## 快速开始

**第 0 步：装上它**（初次使用，详见[下载与安装](#下载与安装)）

最省事的方式是**把这句话发给你正在用的 AI 助手，让它自己装**：

> 请阅读并按 <https://raw.githubusercontent.com/anticipate218/invention-patent-skill/main/INSTALL.md> 的说明，把 `invention-patent-skill` 这个技能安装到我当前使用的助手环境里；装完告诉我装到了哪个路径、是哪个版本、以及怎么开始用。

想自己动手的话，一行命令也行：

```bash
git clone https://github.com/anticipate218/invention-patent-skill.git ~/.agents/skills/invention-patent-skill
```

或者用技能包自带的安装器（会自动挑位置、丢掉 `.git/`、装完自校验）：

```bash
python scripts/install_skill.py --list-targets   # 先看装哪儿合适
python scripts/install_skill.py --target auto
```

**第 1 步：生成申请文件骨架**

```bash
python scripts/check_patent.py --init 我的申请.md
```

骨架里有全部必备章节与填写提示（提示写在 HTML 注释里，**不计入摘要字数、也不触发措辞检查**）。

**第 2 步：把交底书里的内容填进去**

顺序建议：技术领域 → 背景技术 → 发明内容 → **权利要求书** → 具体实施方式 → 摘要 → 附图说明。先定权利要求、再写说明书，比反过来省力得多。

**第 3 步：边写边自检**

```bash
python scripts/check_patent.py 我的申请.md --strict
```

**示例输出**（退出码 0 即可）：

```
发明名称：一种传送带异物视觉检测方法
权利要求：7 项
说明书小节：技术领域 → 背景技术 → 发明内容 → 附图说明 → 具体实施方式
摘要字数：184

没有发现问题。
结论：机械校验全部通过。
```

**第 4 步（可选）：排成正式版式**

```bash
python scripts/make_docx.py 我的申请.md -o 我的申请.docx      # 转 Word
```

或者用 LaTeX 模板 `assets/latex/cnipa/main.tex`（版式已按审查指南的纸张/页边距/字高/行距要求做好，编译步骤见 `assets/latex/README.md`）。

> 以上脚本都**不需要安装任何依赖**（纯 Python 标准库），也不需要联网。唯一的外部依赖是可选的：想真的把 LaTeX 模板编译成 PDF，才需要本机有 TeX 发行版。

---

## 为什么需要它

专利文件的失分点和数学建模完全相反：**不是"想法不够好"，而是形式细节与撰写边界出错**。这些错误绝大多数是可预防的：

1. **权利要求的形式错误会直接被补正**：编号跳号、第一项不是独立权利要求、从属权利要求引用在后的权项、**多项从属引用另一项多项从属**（细则第 25 条第 2 款）、一项权项里出现两个句号、权项里写了"如图……所示"。
2. **说明书与权利要求对不上**：权项里的特征在说明书里找不到依据（专利法第 26 条第 4 款），或说明书把方案写得太笼统导致"本领域技术人员无法实现"（第 26 条第 3 款）。
3. **摘要与名称的硬规则容易引错依据**：300 字上限在《专利审查指南》里，**不在**细则第 26 条；名称 25/60 字的出处也有两处。
4. **答复审查意见时改超范围**：专利法第 33 条是红线，**摘要不属于原始记载内容**——从摘要里捞特征补进权项是典型超范围。
5. **条号版本混乱**：现行《专利法实施细则》是 2023 年修订版，网上大量资料仍用旧编号。

本技能把这些"规则知识"固化成**可执行的流程 + 可运行的自检脚本**，让助手在每一步都按现行规则要求你，并在不确定的地方明确说"未核实"而不是编一个数字。

---

## 它能做什么、不能做什么

### 能做

- 把技术交底书整理成**发明点 + 必要技术特征 + 可选特征**的分层结构。
- 撰写/改写**权利要求书**（独权前序+特征、从权阶梯、多项从属的正确写法）。
- 撰写/改写**说明书**五部分，检查用语限制（不得有引用语、商业性宣传用语）与充分公开。
- 撰写**摘要**、指定摘要附图，检查 300 字上限。
- 检查**附图与附图标记**的双向一致性、编号规范、以及**括号按部件区分的规则**。
- 对已有草稿跑**9 类机械自检**，每条问题给出 `references/` 里的依据小节。
- 生成 **LaTeX / Word** 正式版式产出，并真编译验证模板还能编过。
- 整理**审查意见答复**的结构、修改的三条铁律、创造性三步法的论证套路。
- 查**流程与法定期限**（优先权、分案、答复期限与延长、登记、复审与无效）。

### 不能做（明确的边界）

| 不做 | 为什么 |
|---|---|
| **判断某件申请能不能授权** | 授权结论由审查员个案裁量；本技能只讲规则与判断框架 |
| **给出新颖性/创造性结论** | 需要完整检索与对比文件分析，且属专业判断 |
| **替代执业专利代理师** | 本技能是脚手架与自检工具，不是法律意见 |
| **编造技术方案、参数或实验数据** | 编出来的实施例要么公开不充分，要么后续修改构成超范围；**公开不可逆** |
| **联网检索专利数据库** | 检索需要用户自己做，工具入口见 `references/sources.md` |
| **出具规避审查的"技巧"** | 例如隐瞒在先技术、拆分申请规避单一性审查 |
| **覆盖实用新型与外观设计** | 本技能只覆盖发明专利；两者的差异在 `SKILL.md` 第 0 步说明 |

---

## 法律依据的版本问题（必读）

本技能引用的法律文本与版本：

| 依据 | 版本 | 说明 |
|---|---|---|
| 《中华人民共和国专利法》 | 2020 年修正 | 现行 |
| 《中华人民共和国专利法实施细则》 | **2023 年修订，2024 年 1 月 20 日起施行** | 条号与旧版**不同**。例如"权利要求书"现在是**第 22 条**，"说明书"是**第 20 条** |
| 《专利审查指南》 | 以 CNIPA 官网现行公布版本为准 | 本技能引用到部分、章、节号，如"第二部分第二章 2.2.6" |

> ⚠️ **最容易踩的坑**：网上大量模板与教程仍按 2010 年版细则编号（把权利要求书写成第 19 条、把说明书写成第 17 条）。照抄会引错法条，答复审查意见时很难看。

所有依据均来自公开文本，**以 CNIPA 官网最新公布为准**。发现规则变更请按 `CONTRIBUTING.md` §1.3 提 Issue（需附官方链接与生效日期）。

---

## 下载与安装

技能遵循 [Agent Skills 开放标准](https://agentskills.io/specification)：一个目录 + 一个 `SKILL.md`（含 YAML frontmatter）。目录名必须与 frontmatter 里的 `name` 一致（本仓库已满足）。

### 1. 最省事：一句话让你的 AI 助手自己装

把下面这句话（连同链接一起）发给你正在用的 AI 助手：

> 请阅读并按 <https://raw.githubusercontent.com/anticipate218/invention-patent-skill/main/INSTALL.md> 的说明，把 `invention-patent-skill` 这个技能安装到我当前使用的助手环境里；装完告诉我装到了哪个路径、是哪个版本、以及怎么开始用。

[`INSTALL.md`](INSTALL.md) 是**专门写给 AI 助手看的**：怎么拿到技能包、怎么确定技能根目录、怎么校验，还有一张「不要做」的清单。

**如果你的助手抓不了网页链接**，让它从 Release 包装：

> 请到 <https://github.com/anticipate218/invention-patent-skill/releases/latest> 下载最新的 `invention-patent-skill-v*.zip`，解压后把里面的 `invention-patent-skill` 整个文件夹放到你（助手自己）的技能目录里——**目录名不要改**。

### 2. 装到哪里（各宿主的技能目录）

| 宿主 / 约定 | 技能目录（安装后应形如 `<技能根>/invention-patent-skill/SKILL.md`） |
|---|---|
| **DSH**（项目级） | `<项目根>/.dsh/skills/invention-patent-skill` |
| **DSH**（用户级） | `~/.dsh/skills/invention-patent-skill`（设置过 `$DSH_HOME` 时以它为准） |
| **Agent Skills 通用约定**（项目级） | `<项目根>/.agents/skills/invention-patent-skill` |
| **Agent Skills 通用约定**（用户级） | `~/.agents/skills/invention-patent-skill` |
| **Claude Code**（项目级 / 用户级） | `<项目根>/.claude/skills/…` / `~/.claude/skills/…` |

`<项目根>` = 从当前目录向上找到的最近一个含 `.git` 的目录；找不到就用当前目录。

> **上表只列了能核实的路径。** 其它宿主的技能目录各不相同，本仓库**故意不写死猜测值**——猜错的代价是"装成功了但永远不被扫描"，比装不上更难查。请让助手去读它自己的文档，或问它"你之前装的技能放在哪个目录"，然后用 `--into` 指定。

### 3. 用自带安装器装（推荐）

```bash
python scripts/install_skill.py --list-targets            # 先看有哪些位置、哪个已存在、已装的是哪个版本
python scripts/install_skill.py --target auto             # 装到自动挑出的位置
python scripts/install_skill.py --target auto --dry-run   # 只看会做什么，不动磁盘
python scripts/install_skill.py --target dsh-user         # 显式指定
python scripts/install_skill.py --into "~/.agents/skills"  # 给技能根：自动补一层 invention-patent-skill
python scripts/install_skill.py --from-zip invention-patent-skill-vX.Y.Z.zip
python scripts/install_skill.py --download                # 拉最新 Release 的 ZIP 再装（唯一联网的动作）
python scripts/install_skill.py --self-test               # 固件测试：不联网、不碰真实技能目录
```

三条安全约定，值得知道：

- **默认拒绝覆盖**已存在的技能目录（想覆盖得显式 `--force`）；
- `--force` **只肯删"确实是本技能"的目录**——目标里必须有 `name: invention-patent-skill` 的 `SKILL.md`；
- 装完自动用包内的 `scripts/validate_skill.py --strict` 校验一遍，不通过就报错退出。

### 4. 依赖

| 用途 | 需要什么 |
|---|---|
| **文档与自检脚本**（`scripts/`、`references/`、`assets/`） | 只有 **Python 3.9+**，纯标准库 |
| **真编译 LaTeX 模板**（可选） | 本机装有 TeX 发行版（MiKTeX 或 TeX Live），并有 `xelatex` 与 `bibtex` |

除安装器的 `--download` 这一个开关外，所有脚本都不需要联网，也没有任何交互式提示——都能在 CI 里非交互运行。

### 5. 装完先验证一下（30 秒）

```bash
cd invention-patent-skill
python scripts/validate_skill.py . --strict    # 期望：0 个错误，0 个警告
python scripts/check_patent.py --self-test     # 期望：51/51 通过
python scripts/check_latex.py --self-test      # 期望：74/74 通过（不需要 TeX）
python scripts/make_docx.py --self-test        # 期望：28/28 通过
python scripts/install_skill.py --self-test    # 期望：21/21 通过
python scripts/check_patent.py examples/draft-example.md --strict   # 期望：退出码 0
```

`validate_skill.py` 报错通常意味着**目录名被改过**（必须叫 `invention-patent-skill`）或者文件没下全。

### 6. 怎么更新、怎么卸载

```bash
# 更新：拿到新版（git pull 或换一个新 ZIP）后重装，加 --force 覆盖自己的旧版本
git pull
python scripts/install_skill.py --target auto --force

# 卸载：技能就是一堆文件，没有后台进程、不写注册表、不改宿主配置
rm -rf ~/.agents/skills/invention-patent-skill
```

### 7. 装完没生效？按这个顺序查

1. **目录名**是否正好是 `invention-patent-skill`——改了名不会报错，只会静默失效。
2. **层级**是否是 `<技能根>/invention-patent-skill/SKILL.md`（`SKILL.md` 必须在技能目录**顶层**）。
3. **位置**是否真的是宿主扫描的那个根目录（别猜，见[第 2 节](#2-装到哪里各宿主的技能目录)）。
4. **是否要重启**：DSH 会持续监视技能根目录，**新增/改名/删除技能在下一个技能目录快照就会生效，不需要重启**；其它宿主以它自己的文档为准。
5. **是否被别的技能抢了触发**：把话说得更明确一点——开头加一句「用 invention-patent-skill 来做…」。

---

## 工作流：从交底书到申请文件

技能把这件事拆成 9 步（`SKILL.md` 第 0～8 步）：

| 步 | 做什么 | 关键点 |
|---|---|---|
| 0 | 确认**处于哪一段**、**保护客体**、**交付物** | 只有交底书 / 已定稿要自检 / 收到审查意见，三件事完全不同 |
| 1 | 判断流程位置并路由到对应参考文档 | 见 `SKILL.md` 第 1 步的对照表 |
| 2 | 从交底书**提炼发明点** | 先判断交底书够不够；缺的信息列成问题清单去问，**不猜** |
| 3 | 写**权利要求书** | 独权前序+特征；从权只能引用在前；多项从属不得作为另一项多项从属的基础 |
| 4 | 写**说明书**五部分 | 顺序法定：技术领域 → 背景技术 → 发明内容 → 附图说明 → 具体实施方式 |
| 5 | 写**摘要**与处理**附图** | 摘要 ≤300 字；标记括号按部件区分 |
| 6 | 跑**机械自检** | `check_patent.py --strict`，逐条读 `basis` |
| 7 | 产出**正式版式** | LaTeX 模板或 Word；改模板后跑 `check_latex.py` |
| 8 | **答复审查意见** | 先定缺陷类型，再定修改范围；三条铁律不能破 |

### 答复审查意见的三条铁律

1. **不得超出原说明书和权利要求书记载的范围**（专利法第 33 条；指南第二部分第八章 5.2.1.1）。**摘要不属于原始记载内容**。
2. **修改必须针对通知书指出的缺陷**（细则第 57 条第 3 款；指南第二部分第八章 5.2.1.2）。
3. **不得做五种不予接受的修改**（指南第二部分第八章 5.2.1.3）。

创造性答复用**三步法**（专利法第 22 条第 3 款）：确定最接近的现有技术 → 确定区别特征与实际解决的技术问题 → 判断对本领域技术人员是否显而易见。主张"公知常识"要有举证。

---

## 工具与产出物

### 自检脚本 `scripts/check_patent.py`（9 类检查）

```bash
python scripts/check_patent.py 我的申请.md --strict     # 警告也当失败
python scripts/check_patent.py 我的申请.md --json       # 机器可读
python scripts/check_patent.py --init 我的申请.md       # 生成填空骨架
python scripts/check_patent.py --self-test              # 脚本自身固件测试
```

| 检查 | 抓什么 |
|---|---|
| 部件齐全 | 摘要 / 权利要求书 / 说明书 / 附图 是否都在 |
| 发明名称 | 字数上限、是否含商标/型号/宣传用语 |
| 说明书结构 | 五部分是否齐全、顺序是否正确、是否都有小标题 |
| 摘要 | 300 字上限、是否有标题、是否含宣传用语 |
| 权利要求 | 编号跳号、第一项是否为独权、从属是否引用在后、多项从属嵌套、句号数、"如图……所示"、插图、开放式列举用语（"等等""例如"） |
| 附图标记 | 正文用了但没列出、列出但没用到、双向一致性 |
| 措辞 | 引用语（"如权利要求……所述"）、商业性宣传用语、不确定用语 |
| 序列表 | 提到序列但缺序列表部分 |
| 附图 | 图号连续性、是否用工程蓝图 |

> **脚本只做机械校验。** 它不判断新颖性、创造性、保护范围，也不判断权项是否得到说明书支持——那些必须人工（或代理师）复核。

### 现成片段（`assets/`）

| 文件 | 用途 |
|---|---|
| `assets/patent-outline.md` | 整体填空骨架（含全部章节与提示） |
| `assets/claims-template.md` | 权利要求书片段：1 独权 + 4 从权（含一项多项从属）+ 硬约束表 + ❌/✅ 对照 |
| `assets/abstract-template.md` | 摘要片段 + 五要素清单 + 格式红线 |
| `assets/cheatsheet.md` | 一页纸速查（可打印） |

### 正式版式产出

| 目标 | 用什么 |
|---|---|
| PDF（打印/提交） | `assets/latex/cnipa/main.tex`，编译步骤见 `assets/latex/README.md` |
| Word | `python scripts/make_docx.py 我的申请.md -o 输出.docx` |
| Markdown | 直接用草稿 |

LaTeX 模板已按《专利审查指南》**第五部分第一章 4.1～5.6** 的版式要求做好：A4（297×210 mm）、上/左/右/下页边距 25/25/15/15 mm、字体字高不低于 3.5 mm、行距 2.5～3.5 mm、黑色、页码连续。**电子申请要把段号开关关掉**——见 `assets/latex/README.md` §4。

### 完整范例

`examples/draft-example.md` 是一件虚构发明「一种传送带异物视觉检测方法」的完整申请文件：**1 项独权 + 6 项从权**、摘要 184 字，通过 `--strict`。读法、验证命令与**不能照抄**的部分见 `examples/README.md`。

---

## 质量保障

这个仓库不只有文档，还带多层可自动运行的检查（CI 每次提交都会跑）：

| 层 | 命令 | 检查什么 |
|---|---|---|
| 结构 | `python scripts/validate_skill.py . --strict` | frontmatter 字段白名单、`name` 与目录一致、description/compatibility 长度、正文行数、**文件引用是否存在**、未索引文件、Windows 风格路径 |
| 自检工具 | `python scripts/check_patent.py --self-test` | **51 项**固件：9 类检查的"该报的报、不该报的不报"两侧都测（含 HTML 注释不计字数、正文裸标记不算"没用到"、单字"等"的开放式列举与"等间距"这类固定词的区分） |
| 范例回归 | `python scripts/check_patent.py examples/draft-example.md --strict` | 完整范例必须始终能过 |
| 安装器 | `python scripts/install_skill.py --self-test` | **21 项**固件：复制时确实丢掉 `.git`/`__pycache__`、已存在时先拒绝再 `--force`、**非本技能的目录一律不删**、`--dry-run` 不写盘、带/不带顶层前缀的 ZIP 都能解、`auto` 挑选顺序、**指向别人的技能目录时拒绝**、带 UTF-8 BOM 的 `SKILL.md` 仍可识别 |
| Word 生成 | `python scripts/make_docx.py --self-test` | **28 项**固件：Markdown 解析、OOXML 转义、A4 与页边距、段落编号续号、**逐字节可复现**、坏包被拒 |
| 依赖边界 | 见 `.github/workflows/ci.yml` | AST 扫描 `scripts/*.py`，禁止引入 requests/numpy/lxml 等外部依赖 |
| 模板真编译 | `python scripts/check_latex.py --require`（CI）/ `--self-test`（本机无需 TeX） | 在临时目录里真的编译模板（`xelatex → bibtex → xelatex ×2`），核对硬错误、未解析引用、字体替换、页数下限，以及**一级部件顺序**、**说明书五部分顺序**、**版式参数**、**权利要求编号与引用** |
| 触发与行为评测 | 见 `evals/` | **35 条**触发查询（正例 + near-miss 负例）测 description 触发率；**12 条**行为用例含**反幻觉断言**（不得编造条号或页码；不得越位断言可授权/侵权） |

其中 `validate_skill.py` 对所有 Agent Skill 作者都有用：它专门拦"跨工具分发时会硬报错"的 frontmatter 问题（比如多写了非标准字段）。

---

## 仓库结构

```
invention-patent-skill/
├── SKILL.md                      # 主控：三条铁律 + 第 0～8 步 + Gotchas + 参考文件索引
├── references/                   # 按需加载的详细资料（渐进式披露第二/三层）
│   ├── drafting-playbook.md      # 从交底书到申请文件：全流程、发明点提炼、检索、布局
│   ├── claims.md                 # 权利要求书：结构、编号、独权/从权、引用规则、清楚措辞
│   ├── specification.md          # 说明书：五部分、三要素、用语限制、序列表、充分公开
│   ├── format.md                 # 名称、文件组成与顺序、格式、摘要、不得出现的内容
│   ├── drawings.md               # 附图与附图标记（含括号按部件区分）
│   ├── examination.md            # 新颖性/创造性/实用性/客体审查（含算法与 AI）
│   ├── prosecution.md            # 审查意见答复与修改边界
│   ├── procedure.md              # 流程与法定期限（优先权、分案、复审无效）
│   ├── checklists.md             # 提交前后逐项自查清单
│   ├── templates.md              # 三条产出路线、模板与编译答疑
│   └── sources.md                # 法规现行版本、检索工具、引用规范
├── scripts/
│   ├── check_patent.py           # 申请文件机械自检（9 类检查，纯标准库）
│   ├── check_latex.py            # 真编译 LaTeX 模板并体检结构性约束
│   ├── make_docx.py              # Markdown → Word（纯标准库生成 OOXML）
│   ├── install_skill.py          # 装进宿主技能目录（默认不覆盖 / 装完自校验）
│   └── validate_skill.py         # 技能结构校验（frontmatter / 篇幅 / 文件引用）
├── assets/
│   ├── patent-outline.md         # 可填空申请文件骨架
│   ├── claims-template.md        # 权利要求书片段
│   ├── abstract-template.md      # 摘要片段
│   ├── cheatsheet.md             # 一页纸红线速查
│   └── latex/
│       ├── README.md             # 编译四步、宏包、版式参数、报错表
│       └── cnipa/                # 可直接编译的申请文件模板（main.tex + refs.bib）
├── examples/
│   ├── draft-example.md          # 完整范例：一种传送带异物视觉检测方法
│   └── README.md                 # 范例读法、验证命令与不能照抄的部分
├── evals/                        # 触发评测与行为用例（含 near-miss 负例）
├── .github/workflows/ci.yml      # 持续集成
├── CONTRIBUTING.md               # 贡献指南（来源可信度标注、规则变更流程）
├── INSTALL.md                    # 给 AI 助手看的安装说明
├── CITATION.cff                  # 引用元数据
├── CHANGELOG.md
├── README.md
└── LICENSE
```

设计上遵循 Agent Skills 的**渐进式披露**原则：`SKILL.md` 只放"每次都要用到"的核心流程（正文 194 行），详细资料放进 `references/` 由助手按需读取。

---

## 最常被引错的五处规则

这五条是本技能在整理资料时反复遇到的错引，在这里集中说明：

| # | 常见错引 | 正确依据 |
|---|---|---|
| 1 | 摘要 300 字上限出自"细则第 26 条" | 《专利审查指南》**第一部分第一章 4.5.1** 与**第二部分第二章 2.4**。细则第 26 条只讲摘要应当写明什么 |
| 2 | 申请文件版式（纸张/页边距/字高/行距/页码）出自"第一部分第一章"或"第五部分**第二章**" | 《专利审查指南》**第五部分第一章「专利申请文件及手续」4.1～5.6**（4.1 纸张、4.2 规格、4.3 页边、5.1～5.6 书写规则）。现行指南的**第五部分第二章是「专利费用」**，不要把两者的章号弄反 |
| 3 | 权利要求书/说明书的细则条号沿用 2010 年版 | 现行细则：**权利要求书 = 第 22 条**（编号格式 22.2、术语与禁引用语 22.3、附图标记加括号 22.4），**说明书 = 第 20 条** |
| 4 | "正文提到附图标记就要加括号" | **按部件区分**：具体实施方式**不加**（指南第二部分第二章 2.2.6，且标记要跟在技术名称后面）、权利要求书**必须加**（细则第 22 条第 4 款）、摘要**应当加**（指南第二部分第二章 2.4）、附图说明列举**不加** |
| 5 | 多项从属权利要求的限制出自"细则第 25 条第 3 款" | **细则第 25 条第 2 款** |

细节与原文见 `references/format.md`、`references/claims.md`、`references/drawings.md`。

---

## 与同类项目的关系

写作本技能时参考并致谢以下公开资料（各自版权归原作者）：

- [agentskills.io](https://agentskills.io/specification) —— Agent Skills 开放标准（格式规范与写作方法论的依据）。
- 国家知识产权局（CNIPA）官网公布的《专利法》《专利法实施细则》《专利审查指南》现行文本与公开问答。
- 本仓库的姊妹项目 [math-modeling-skill](https://github.com/anticipate218/math-modeling-skill) —— 本技能的目录结构、脚本风格（纯标准库 + 固件测试 + CI 真编译）与文档体例沿用了它的做法。

**本仓库不含任何他人的申请文件或代理机构内部资料。** 所有范例（`examples/draft-example.md`）均为为演示而虚构的技术方案。若你认为某处引用不当，欢迎提 issue。

---

## 免责声明

本项目**不隶属于**国家知识产权局或任何代理机构，规则整理仅供撰写与自检参考。

- 本技能**不是法律意见**，也**不替代执业专利代理师**。
- 所有法律、细则、指南与费用信息均**以 CNIPA 官网最新公布为准**；法律修订会改变条号与规则。
- 用户依据本技能生成的文件**必须自行核对**，建议在正式提交前交由有资质的专利代理机构或代理人复核。
- **机械校验通过 ≠ 法律合规**：脚本只查形式，不判断新颖性、创造性、保护范围与支持性。
- 因使用本技能产生的任何法律或商业后果，作者不承担责任。

---

## License

[MIT](LICENSE) © 2026 anticipate218
