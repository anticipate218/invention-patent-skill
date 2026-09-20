# 变更记录

本文件记录 `invention-patent-skill` 的版本变更。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

> **关于法律依据的版本**：本技能追踪的法律文本是《专利法》（2020 年修正）、
> 《专利法实施细则》（2023 年修订，2024 年 1 月 20 日起施行）与《专利审查指南》
> （以 CNIPA 官网现行公布版本为准）。法律修订会改变条号与规则，届时按
> `CONTRIBUTING.md` §1.3 的流程发新版并在下方记录**依据文件与生效日期**。

---

## [1.0.0] - 2026-09-20

首个公开版本。

### 新增

- **`SKILL.md`** —— 技能主控：三条铁律（不编造技术方案 / 权利要求与说明书互相兜住 /
  不越位）、第 0～8 步工作流（确认阶段 → 路由 → 提炼发明点 → 写权利要求书 → 写说明书
  → 摘要与附图 → 机械自检 → 格式产出 → 答复审查意见）、Gotchas、反面模式与参考文件索引。
- **`references/`（11 篇）** —— 按需加载的详细资料：
  - `claims.md`：权利要求书的结构、编号格式、独立/从属权利要求撰写、引用规则、
    清楚措辞、保护范围策略与自查表。
  - `specification.md`：说明书五部分与法定顺序、技术领域/背景技术/发明内容/
    具体实施方式、撰写要求与用语限制、序列表、充分公开。
  - `format.md`：发明名称、申请文件组成与法定顺序、格式与提交、省略规则、
    摘要、不得出现的内容、形式缺陷高发清单。
  - `drawings.md`：附图编号、附图标记、**括弧按部件分别判断**、一致性、
    形式限制、摘要附图、制图规范。
  - `examination.md`：授权条件总览、新颖性、创造性三步法、实用性、客体审查、
    涉及计算机程序与 AI 算法特征的发明、充分公开与以说明书为依据。
  - `prosecution.md`：审查意见通知书类型、答复结构与写作要求、**修改的三条铁律**、
    创造性答复的论证套路、常见意见对照表。
  - `procedure.md`：流程主干与法定期限、提前公布、优先权、分案、期限计算、
    答复期限与延长、办理登记、复审与无效。
  - `drafting-playbook.md`：从交底书到申请文件的全流程、发明点提炼、检索、
    权利要求布局、说明书撰写策略、常见误区。
  - `checklists.md`：提交前后逐项自查清单。
  - `templates.md`：三条产出路线对比、LaTeX 模板、编译答疑、转 Word。
  - `sources.md`：法规现行版本、检索工具、引用规范与免责声明。
- **`scripts/check_patent.py`** —— Markdown 申请文件机械自检，**9 类检查**：部件齐全、
  发明名称、说明书结构、摘要、权利要求、附图标记、措辞、序列表、附图。
  每条问题带 `basis` 字段指向 `references/` 对应小节。支持 `--init` / `--json` /
  `--strict` / `--self-test`（51 项固件测试）。
- **`scripts/check_latex.py`** —— 真编译 CNIPA LaTeX 模板并体检：硬错误、未解析引用与
  交叉引用、字体替换、页数下限，以及一级部件顺序、说明书五部分顺序、版式参数、
  权利要求编号与引用等结构性约束。支持 `--require` / `--only` / `--keep` /
  `--keep-fontset` / `--tex-dir` / `--timeout` / `--self-test`（74 项固件测试）。
- **`scripts/make_docx.py`** —— Markdown 草稿转 Word（纯标准库生成 OOXML），
  支持 `-o` / `--para-number` / `--inspect` / `--self-test`（28 项固件测试）。
- **`scripts/install_skill.py`** —— 把技能装进宿主技能目录：自动定位技能根、
  默认拒绝覆盖、`--force` 只肯删本技能、装完自校验。支持 `--list-targets` /
  `--target` / `--into` / `--from-zip` / `--download` / `--dry-run` / `--self-test`
  （21 项固件测试）。
- **`scripts/validate_skill.py`** —— 技能自身结构校验（frontmatter 字段白名单、
  `name` 与目录一致、description/compatibility 长度、正文行数、文件引用是否存在、
  索引完整性、路径风格），支持 `--strict`。
- **`assets/`** —— `patent-outline.md`（整体填空骨架）、`claims-template.md`
  （权利要求书片段）、`abstract-template.md`（摘要片段 + 300 字格式红线）、
  `cheatsheet.md`（一页纸速查）、`latex/cnipa/`（可直接编译的 LaTeX 模板，
  版式对齐《专利审查指南》第五部分第一章 4.1～5.6；含 `main.tex`、`refs.bib`
  与 `assets/latex/README.md` 编译与排错指南）。
- **`examples/`** —— `draft-example.md`：一份完整的虚构发明申请文件范例
  （1 项独权 + 6 项从权、摘要 184 字），通过 `check_patent.py --strict`；
  `examples/README.md` 说明读法、验证命令与**不能照抄**的部分。
- **`evals/`** —— `trigger-queries.json`（35 条触发用例，含 near-miss 负例）、
  `evals.json`（12 条行为用例，含反幻觉与越位边界断言）、`evals/README.md`
  （触发率阈值与行为评测方法）。
- **`INSTALL.md`** —— 写给 AI 助手看的安装说明（「一句话安装」链接的目标文档）。
- **`CONTRIBUTING.md`** —— 贡献指南：来源可信度标注 `[官方]`/`[半官方]`/`[社区]`、
  「官方未公开」必须写明、规则变更流程、不接受的贡献。
- **`.github/workflows/ci.yml`** —— 持续集成：结构校验、四个脚本的固件测试、
  范例回归、脚本仅依赖标准库的 AST 扫描、JSON 合法性、路径风格，
  以及独立的 LaTeX 作业真编译模板。

### 说明

- 本技能**只覆盖发明专利**。实用新型与外观设计的差异在 `SKILL.md` 第 0 步中说明边界。
- 所有脚本只用 Python 标准库（`check_latex.py` 会调用本机 TeX 命令，属子进程而非依赖），
  非交互、CI 友好。
- 本技能不判断新颖性/创造性/保护范围，也不替代执业专利代理师；每条输出都提示
  **机械校验通过 ≠ 法律合规**。
