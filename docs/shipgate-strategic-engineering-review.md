# Shipgate Strategic Engineering Review

> 历史说明：本文是 2026-06-09 基于 `0.11.0` / commit `ef58a57`
> 的战略审计快照。当前 `main` 已经实现 GitHub Check Run、Actions
> annotations、`verifier.json` 和 PR capability-review comment；文中将这些
> 能力标为"缺失"的段落应按历史记录阅读，而不是当前实现状态。

> 审计日期：2026-06-09 · 审计对象：`ThreeMoonsLab/agents-shipgate` · commit `ef58a57` · 版本 `0.11.0`
>
> 证据标注约定：**【仓库实证】** 直接观察到；**【意图明确但未完成】** 有代码/文档意图但未落地；**【缺失/未实现】** 经搜索确认不存在；**【战略建议】** 基于明确论点的建议。

---

## 0. Executive Summary

**一句话结论：这是一个工程纪律罕见地好、但需求验证严重不足的产品。最大的风险不在代码里，而在那个只有表头、没有一行数据的 `benchmark/results/2026-W2-baseline.csv` 里。**

五个核心判断：

1. **工程资产是真实的、稀缺的。** 静态-only 不变量由 AST 合约测试强制（`tests/test_adapter_static_only.py`）、单一决策引擎（`release_decision.decision`）、不可抑制的 trust-root 检查、severity floor、sigstore 签名 + Trusted Publishing + SBOM 的发布链。全套测试（≈2,100 项）一次通过。这一层"deterministic verifier + reward-hacking 抵抗"的纪律，在 agent 工具生态里几乎没有同类。
2. **最大的弱点是"供给远超已验证的需求"。** 仓库已构建了六种 agent 接入面（AGENTS.md 块、Claude Code skill、Codex skill/plugin、Cursor rule、slash command、PR template）、GitHub Action（50 个 outputs）、adoption harness、governance benchmark、营销文案——但 adoption benchmark 结果文件是空的【仓库实证】，已知的真实 pilot 只有一次，且返回了最不可行动的裁决 `insufficient_evidence`（项目记录：2026-06-01 Stripe pilot）。
3. **`insufficient_evidence` 是当前的产品杀手。** 阈值硬编码（≥50% 低置信工具、>3 source warnings，`ci/release_decision.py:27-28`），真实世界的 dynamic-toolkit 工厂模式必然触发它，而它给用户的下一步是"提供更好的输入"——这是把工作推回给用户，不是产品价值。
4. **定位需要一次校准而非推翻。** 当前的 wedge 是"AI agent 项目的 tool-surface 合并门禁"——这个市场今天很小。但 trust-root 检查、`codex_boundary`（1,473 行）和最近的 "Phase 2 GitHub PR gate" 提交表明引擎已经长出了更大的那个方向：**治理任何被 coding agent 修改的仓库里的能力/权限漂移**（MCP 配置、CI 权限、agent 指令文件、workflow 变更）。这才是与 "agent-native verification layer" 使命对齐、且每个用 Claude Code/Codex 的团队都有的痛点。
5. **下一个里程碑不该是新功能，而是证据。** 跑出真实的 adoption baseline 数据、跑通三个 design partner、把 `insufficient_evidence` 修成可行动的——在这三件事完成前，任何新表面积都是在加重已经过载的合约。

---

## 1. Evidence Base

### 检查环境

- 工作目录：`/Users/pengfeihu/code/shipgate/.claude/worktrees/objective-greider-dff29c`（git worktree）
- Commit：`ef58a574586318eeb0583852d84b757e2c6abb93`，分支 `claude/objective-greider-dff29c`（基于 `main`），`git status` 干净
- 近期提交：`2f3e58a` Add Phase 2 GitHub PR gate support、`ba97ac7` Codex-compatible local boundary check (#189)、`383b2e1` Standardize capability locks (#187)

### 运行的命令（全部成功，无失败）

| 命令 | 结果 |
|---|---|
| `git rev-parse HEAD` / `git status --short` / `git log` | 见上 |
| `find` 全仓库文件清单（src/ 含 ~200 个 Python 文件，tests/ 121 个测试文件） | 完整 |
| `PYTHONPATH=src .venv/bin/python -m pytest tests/ -x -q` | **exit 0，≈2,100 项测试通过，4 项 skip，无失败** |
| `curl https://pypi.org/pypi/agents-shipgate/json` | **PyPI 最新版 0.11.0**，与 `pyproject.toml:7` 一致（早期 pilot 中 pipx 拉到旧版 0.8.0 的问题已解决） |
| `ls examples/golden-prs/ plugins/ skills/ .agents/plugins/` | README 引用的路径全部存在，无断链 |
| `cat benchmark/results/2026-W2-baseline.csv` | **仅有 CSV 表头 `model,prompt,archetype,variant,score,...`，零数据行**【仓库实证】 |

### 阅读的关键文件（节选）

`README.md`(748 行)、`AGENTS.md`(568 行)、`ROADMAP.md`、`STABILITY.md`、`pyproject.toml`、`action.yml`(416 行)、`shipgate.yaml`、`docs/category.md`、`docs/design-partner-verifier-pilot.md`、`docs/trust-model.md`、`docs/autofix-policy.md`、`src/agents_shipgate/cli/main.py`、`cli/scan/orchestrator.py`、`cli/verify/orchestrator.py`、`ci/release_decision.py`、`ci/exit_policy.py`、`core/capability_delta.py`、`core/codex_boundary.py`、`checks/registry.py`、`checks/verify*.py`、`inputs/protocol.py`、`schemas/manifest/root.py`、`schemas/verifier.py`、`schemas/agent_result_v1.py`、`tests/test_adapter_static_only.py`、`tests/test_self_approval_signal.py`、`tests/test_severity_override_floor.py`、`.github/workflows/{ci,release,agents-shipgate,adoption-harness}.yml`、`tools/shipgate-detect.py`、`llms.txt`、`.well-known/agents-shipgate.json`、`docs/triggers.json`。

另检索了 `policy / rule / gate / verify / agent / MCP / permission / capability / trust_root / insufficient_evidence / self_approval / FastMCP` 等关键词在 `src/` 内的全部命中。

---

## 2. Current Repo State

### Shipgate 今天实际上是什么【仓库实证】

一个 **Python 3.12 / Typer / Pydantic v2** 的 CLI + GitHub Action：**对"AI agent 的工具能力面"做静态、确定性的 PR 合并门禁**。核心循环：

```
PR 改了 agent 能做什么
  → agents-shipgate verify --base origin/main --head HEAD
  → base/head 双扫描 + capability delta + trust-root 检查
  → report.json.release_decision.decision  （唯一决策引擎：blocked | review_required | insufficient_evidence | passed）
  → verifier.json.merge_verdict            （它的确定性投影：mergeable | human_review_required | insufficient_evidence | blocked | unknown）
  → fix_task 路由：机械修复 → coding agent；权限缺口 → 人
```

### 已实现 vs 仅暗示

**已实现（成熟）**：
- 完整 CLI（20+ 子命令：`scan / verify / init / detect / doctor / explain / fixture / baseline / apply-patches / attest / capability / trigger / install-hooks / bootstrap / feedback` 等，`cli/main.py:35-141`）
- 13 个静态输入 adapter（MCP 导出、OpenAPI、OpenAI SDK/API、Anthropic API、Google ADK、LangChain、CrewAI、n8n、Codex config/plugin 等，`inputs/protocol.py`）
- 16 个内建检查 + 6 个 Tier B verify 检查（`checks/registry.py:48-74`），entry-point 插件机制（默认关闭）
- capability fact / lock / diff 模型（`core/capability_delta.py`、`docs/capability-standard.md`），语义方向分类（broadened/narrowed/mixed）
- GitHub Action（composite，50 个 outputs，sticky PR comment，SARIF 输出）
- 六种 agent 接入面渲染器（`cli/discovery/agent_instructions/renderers/`）
- 发布链：ruff + 85% 覆盖率门槛 + pip-audit + SBOM + sigstore 签名 + uv Trusted Publishing（`.github/workflows/release.yml`）

**意图明确但未完成**：
- Workflow-evidence flywheel：`feedback capture` 首版已发，但 raw-bundle replay 和 `scenario replay` harness 未完成（`ROADMAP.md:35-46`）
- Pre-emptive authority surface（`verify --preview` 让 agent 在行动前知道不能碰什么）——roadmap 第 3 项，部分存在
- Adoption benchmark：harness、matrix、runner 文档齐全，**但结果 CSV 为空**

**缺失/未实现**：
- MCP server 模式（Shipgate 自己作为 MCP server 被 agent 调用）——`rg "FastMCP|mcp.server" src/` 零命中
- Claude Code PreToolUse 级别的事中拦截（现有 hooks 是事后 advisory，`cli/install_hooks.py`）
- 跨仓库/组织级策略继承（manifest 无 `extends:`/`imports:`）

### 成熟与早期的分界

**成熟**：决策引擎、capability diff、静态信任模型、测试纪律、发布工程、schema 版本管理。
**早期/欠确定**：真实用户证据、`insufficient_evidence` 的实际可用性、agent 自发发现率（未测量）、面向新用户的认知负担（748 行 README、26 个 report-schema 版本文件、7+ 种输出 artifact）。

---

## 3. Engineering Architecture Review

### 总体评价：A-。架构与使命对齐，纪律执行到位，少数模块需要拆分。

**分层清晰【仓库实证】**：`inputs/`（adapter，Protocol 契约）→ `core/`（domain + findings + capability）→ `checks/`（规则）→ `ci/`（决策）→ `report/ packet/`（渲染）→ `cli/`（编排）。scan 管线 9 个命名阶段，带稳定的 `_perf` 计时点（`cli/scan/orchestrator.py:22-136`）；状态用不可变 dataclass 传递，findings 不在管线中途被原地修改。

**"一个决策引擎"在代码层面是真的**：`ci/release_decision.py:build_release_decision()` 是唯一裁决来源；`schemas/verifier.py` 的 `merge_verdict` 是其投影；`ci/exit_policy.py` 是唯一退出码映射；`cli/verify/fix_task.py` 的修复路由是规则式的。配套强制机制：`core/check_ids.py:14` 的 `UNSUPPRESSIBLE_FINDING_CATEGORIES`（verify/codex_boundary 类检查不可被 manifest 抑制）、`CheckMetadata.floor_severity`（降级有硬下限，越级降级需显式 acknowledgement + 过期时间，`tests/test_severity_override_floor.py` 共 68 个用例）。

**扩展性**：adapter 与 check 都走 entry-point（`pyproject.toml:96-109`），第三方插件默认关闭（`AGENTS_SHIPGATE_ENABLE_PLUGINS=1` 才开），载入有 4 道验证门（`checks/plugin_validation.py`）。

**代码质量信号**：`rg "TODO|FIXME|HACK" src/` 零命中；全面 Pydantic v2 + `Literal` 类型；`extra="forbid"` 严格校验加 `difflib` 字段名建议（`config/loader.py`）。

### 架构问题（按严重度）

1. **`core/codex_boundary.py` 1,473 行、54 个函数的 god module**【仓库实证】。它同时管网络 profile、MCP 自动批准、hooks、skill、agent 指令削弱——这恰恰是未来最重要的方向（见 §7），现在就该拆成 `codex_boundary/{network,mcp,hooks,instructions}.py`，否则下一步扩展（Claude Code、Cursor 的 host 配置）会把它推到 3,000 行。
2. **`cli/verify/orchestrator.py:run_verify()` 287 行**：base 扫描准备、diff 收集、verifier artifact 组装应拆分。
3. **Schema 蔓延**：`docs/` 下 25 个 `report-schema.v0.x.json` + `schemas/manifest/` 26 个文件。冻结旧版本是对的，但 0.x 阶段每月 bump schema（v0.17→v0.25 仅几个月）说明 report 顶层块增长过快——每个新的 verifier-cycle 概念（`heuristics_filter`、`reviewer_summary`、`human_ack`、`capability_runtime_evidence`…）都成为顶层块。**【战略建议】** 在 v1.0 前做一次顶层块的合并收敛，否则 1.0 的稳定承诺会锁死一个过宽的表面。
4. **决策逻辑的"分布式风险"**：裁决虽然单源，但贡献裁决输入的逻辑分散在 scan 阶段 5-8、verify 编排、各 check 模块。建议引入一个显式的 `DecisionEngine` 协议把 `release_decision + merge_verdict + exit_code + fix_task` 的契约固化成一处可审计的接口。
5. **性能边界合理**：latency budget 测试（`tests/test_latency_budget.py`，预算在 `benchmark/perf/budgets.yaml`）+ 10MiB 单文件上限（`inputs/common.py:18`）。缺整体扫描预算（见 §8）。

**结论：当前架构完全可以支撑 agent-native 的下一步，瓶颈不在架构。**

---

## 4. Policy / Verification Model Review

### 模型回答任务清单的逐项判定

| 能力 | 判定 | 证据 |
|---|---|---|
| 策略是否声明式、确定性、可审计 | **是（局部）** | manifest `policies/permissions/checks` + YAML policy packs（`schemas/policy_pack.py`）；匹配语义 AND-of-ORs，确定性排序 |
| 能否在 agent 行动**前**评估 | **部分** | `verify --preview` 与 `trigger` 提供 pre-flight；但没有事中（PreToolUse）拦截点【缺失】 |
| 能否解释为何通过/失败 | **是，且超出行业水平** | 每个 finding 带 `check_id / recommendation / evidence / provenance_kind / 双源 source`；`release_decision.contribution_rules[]` 解释每条 finding 落入 blocker/review/excluded 的规则名（`ci/release_decision.py:245-267`）；`explain-finding` 命令 |
| 区分低/高风险变更 | **是** | capability effect（read/write/financial/code_execution）、`high_risk`、语义方向 broadened/narrowed |
| 表达最小权限 | **部分** | `permissions.scopes` + `is_broad_scope()` 启发式（`core/heuristics.py:31-49`）；但 scope 是自由字符串，无 schema 化的权限模型 |
| 表达 review 要求 | **部分** | per-tool `require_approval_for_tools`；**无 per-path、无 CODEOWNERS 路由、无指定 reviewer**【缺失】 |
| 检测能力扩张 | **是（核心强项）** | capability fact 按 identity hash 配对，effect/authority/control hash 任一变化触发 `changed` + 方向判定（`core/capability_delta.py`） |
| 检测 MCP/tool 权限变更 | **部分** | `.mcp.json` 仅作为 trust-root 文件被"碰了就标记"；`codex_boundary` 对 `.codex/config.toml` 有语义级检查；`.claude/settings.json`、`.cursor/mcp.json` 无语义 diff【部分缺失，详见 §7】 |
| 检测 CI/CD、secrets、依赖、网络、部署风险 | **窄** | CI gate 删除 = critical blocker（`checks/verify_ci_gate.py`）；secrets 仅输出端 redaction；**依赖变更、workflow 权限扩张、deploy 配置不在模型内**【缺失】 |
| 机器可读输出 | **是，过剩** | report.json (schema v0.25)、verifier.json、SARIF、agent_result_v1、attestation、capability lock、feedback export |

### 决策算法（实证伪代码，`ci/release_decision.py:35-214`）

```
for finding in findings:
    suppressed                              → excluded
    blocks_release 且非 baseline-matched     → blocker (policy_block_new)
    severity ∈ blocker层 且非 baseline-matched → blocker (severity_block_new)
    severity ∈ {C,H,M} 或 requires_human_review → review_item
    其余                                     → excluded

blockers 非空                                → blocked
低置信工具 ≥ ceil(0.5×tools) 或 warnings > 3  → insufficient_evidence   ← 硬编码阈值
review_items 非空 或 warnings > 0            → review_required
否则                                         → passed
```

### 三个实质性弱点

1. **`insufficient_evidence` 的阈值不可配置且语义太钝**【仓库实证 + 项目记录】。`_LOW_CONFIDENCE_TOOL_RATIO = 0.5`、`_MAX_TOLERATED_SOURCE_WARNINGS = 3` 写死在 `ci/release_decision.py:27-28`。唯一一次真实 pilot（2026-06-01，Stripe `stripe/ai` PR #232）正是栽在这里：dynamic-toolkit 工厂让静态提取置信度崩塌，裁决退化为"证据不足"。这个裁决对用户的含义是"Shipgate 看不懂你的仓库"，第一印象即流失。修复方向不是调阈值，而是**让低置信场景产出具体的、可机械执行的 next_action**（如"检测到 config-bound toolkit 工厂，运行 `shipgate init --suggest-inventory` 生成工具清单骨架"）。
2. **策略表达力天花板**：匹配谓词是平铺的 AND-of-ORs，**无条件嵌套**（无法表达"financial 且 amount > 1000 才需 approval"）、无 ABAC 式任意属性表达式、无策略继承。当前对单仓库够用，对组织级采购是硬伤。
3. **review 只有"要人看"一档**：deny/warn/require-review/allow 四态中，"warn but pass"没有独立通道（非阻断 finding 全部落入 `review_required`）。这放大了噪音问题——advisory 模式下一切都是 review item，团队会习惯性忽略。

### 强项必须点名

- **Baseline + 抑制本身被审计**：baseline 审计日志（`core/baseline_audit.py`，JSONL）、抑制对 trust-root 类检查无效、`policy_weakened`/`trust_root_touched` 任一为真时即使裁决 mergeable 也强制 `can_merge_without_human=false`（`tests/test_self_approval_signal.py:107-131`）。**这是整个仓库最有护城河价值的 200 行逻辑。**
- 确定性贯彻到 hash：capability ID 只含 identity（路径/行号变化不抖动 ID），全部 JSON `sort_keys=True`，attestation 刻意去掉 wall-clock。

---

## 5. Agent-Native Workflow Review

### 一个 agent 今天如何遇到 Shipgate

发现链是仓库里设计最完整的部分【仓库实证】：

1. **目标仓库已 init 过** → AGENTS.md/CLAUDE.md 管理块、`.claude/skills/`、`.cursor/rules/`、`.agents/skills/`（Codex）—— 六个渲染器（`cli/discovery/agent_instructions/renderers/`），managed-block 幂等更新。
2. **目标仓库没 init 过** → 这是断点。agent 必须事先知道 Shipgate 存在。仓库准备了 `llms.txt`、`.well-known/agents-shipgate.json`、`docs/triggers.json`（machine-readable 触发表，有合约测试）、零安装检测器 `tools/shipgate-detect.py`（stdlib-only，curl | python3）——但这些只对"已经在浏览 Shipgate 仓库的 agent"有效。**冷启动发现完全依赖 (a) 人类引入，或 (b) agent 训练语料/搜索覆盖。**
3. **运行时机**：`docs/triggers.json` 的 44 条规则 + `trigger` 命令给出 run/skip 裁决——这是同类工具中独有的"该不该跑我"协议，设计正确。
4. **解读输出**：读序协议明确（verifier.json 先于 report.json），`first_next_action` 带 `{actor, kind, command, why}`，错误带结构化 `next_action`（`AGENTS_SHIPGATE_AGENT_MODE=1`，`docs/errors.json`）。
5. **修复**：`fix_task` 确定性路由 + `apply-patches`（dry-run 默认、manifest 目录围栏、SHA 校验）+ `docs/agent-autofix-boundary.md` 明文列出 agent 永远不许自断言的六类证据（approval/confirmation/idempotency/broad-scope/prohibited-action/trace）。**自动修复边界的设计是行业级范本。**

### 三个关键缺口

1. **没有 MCP server 模式**【缺失/未实现】。`rg "FastMCP|mcp.server" src/` 零命中。后果：agent 无法在会话内以工具调用的方式问"这个改动会过门禁吗？"——必须 shell out 到 CLI 并解析文件。对 Claude Code 这无伤大雅（Bash 顺手），但对受限工具环境的 agent（无 shell 的 PR bot、IDE 内嵌 agent）这是接入硬墙。一个薄的 MCP wrapper（`shipgate_preview_diff`、`shipgate_verify`、`shipgate_explain_finding` 三个工具）即可补上，且与静态-only 不变量不冲突（server 本地起、不出网）。
2. **事中（pre-action）拦截不存在**【缺失/未实现】。现有 Claude Code hooks（`install-hooks --target claude-code`）是 Edit/Write 之后的 advisory 检查 + Stop 时全量 verify。Roadmap 第 3 项（pre-emptive authority surface）方向正确但只解决"让 agent 提前知道边界"，不解决"agent 越界时被拦"。**【战略建议】** 用 Claude Code 的 PreToolUse hook 实现真正的拦截：对 Edit/Write 目标路径做 trust-root 匹配，命中 `.github/workflows/*`、`shipgate.yaml`、`.mcp.json` 等保护面时返回 deny + 解释。这把 Shipgate 从"PR 时间的门禁"升级为"agent 循环内的边界"，且实现成本低（trust-root glob 表已存在于 `checks/verify.py`）。
3. **adoption 假设未经测量**【仓库实证】。harness 与 benchmark 的设计文档非常完善（24 个付费 cell 的矩阵、100 分 rubric、W2→W4 计划），但 `benchmark/results/2026-W2-baseline.csv` 零数据。所有"agent 会发现并使用 Shipgate"的断言目前都是未验证假设。**这应该是本周就做的事，先于一切新功能。**

---

## 6. GitHub / CI / PR Integration Review

### 现状【仓库实证】

- **Action**：composite（`action.yml`，416 行），verify 模式默认，`shipgate_version` 可锁 PyPI 版本，50 个 outputs（`decision / merge_verdict / can_merge_without_human / trust_root_touched / policy_weakened / capability_changes_* / trigger_action` 等），sticky PR comment（分页搜索 marker，6000 字符截断），artifact 上传，SARIF 输出对接 code scanning。
- **示例齐全**：`examples/github-actions/` 含 advisory、block-on-blocked、require-mergeable、baseline、SARIF、multi-config 等配方；自家仓库 dogfood（`.github/workflows/agents-shipgate.yml`，advisory + `fail_on_decisions: block`）。
- **决策策略**：`fail_on_decisions` 输入支持按 `block`/`require_review` 失败——这就是"blocking vs advisory"的正确分层。

### 缺口与下一个集成里程碑

1. **没有 GitHub Check Run / annotations**【缺失】。现在的呈现是 PR comment + SARIF。SARIF 路径要求仓库开 code scanning（私有仓库需 GHAS 付费），comment 是单条大块文本。**【战略建议】** 下一个里程碑应是原生 **Check Run**：per-finding 的行级 annotation（capability 变更指向 manifest/工具定义的具体行）、`merge_verdict` 作为 check conclusion、`neutral` conclusion 对应 `review_required`。这让 branch protection 能直接 require 这个 check，而不需要用户自己写 `fail_on_decisions` 逻辑。
2. **没有 risk label / reviewer 路由**【缺失】。outputs 里已有 `trust_root_touched`、`capability_changes_added` 等信号，但没有现成的"贴 `agent-capability-change` label + 按 CODEOWNERS 请求 reviewer"的官方配方。这是十行 workflow 的事，却是 reviewer 体验的关键一截——建议作为 `examples/github-actions/09-risk-labels-and-reviewers.yml` 补上，长期并入 Action 本体。
3. **噪音控制依赖用户自律**：advisory 模式 + 每 PR 一条 comment，对非 agent 改动的 PR 依赖 `trigger` 的 skip 裁决。skip 时是否完全静默（不发 comment、不建 check）需要在 Action 里做成显式保证并写进文档——"无关 PR 零噪音"应该是营销级承诺。
4. **base ref 获取是已知的用户坑**：verify 从不 fetch（信任模型决定），需要 `fetch-depth: 0`。文档已反复强调，但 Action 可以在 base 不可用时输出更明确的修复指引（已有 `merge_verdict: unknown` + exit 2 的行为，可加 `next_action: "set fetch-depth: 0"` 到 step summary）。

---

## 7. MCP / Tool Permission Governance Review

**这是战略上最重要的一节。**

### 现状盘点

| 面 | 现状 | 证据 |
|---|---|---|
| MCP 导出（agent 项目声明的工具面） | 一等公民，语义级 | `inputs/mcp.py`，wildcard 工具面有专门 finding |
| `.codex/config.toml` / hooks / plugin | 语义级检查 | `core/codex_boundary.py`：network mode=full、MCP auto-approve-write（critical）、app connector 自动批准、AGENTS.md 削弱、CI gate hook 移除 |
| `.mcp.json` | 仅 trust-root "被碰即标记" | `checks/verify.py` glob 表，无字段级 diff |
| `.claude/settings.json`（permissions/hooks/MCP） | **不在模型内** | trust-root glob 表无此项【缺失】 |
| `.cursor/mcp.json`、VS Code MCP 配置 | **不在模型内** | 同上【缺失】 |
| GitHub workflow `permissions:` 扩张、新 secrets 引用、`pull_request_target` | **不在模型内** | 仅"CI gate 文件被删"有检查【缺失】 |
| package.json scripts / pre-commit 命令变更 | **不在模型内** | 【缺失】 |
| 环境变量/secrets 暴露 | 仅输出端 redaction | `core/privacy.py`，不是对 diff 的治理 |

### 判断

`codex_boundary` 证明了引擎完全有能力对 **coding-agent host 配置**做语义级能力 diff——它只是目前只对 Codex 做了。`.mcp.json`、`.claude/settings.json` 里的一行改动（新 MCP server、`permissions.allow` 加一条 `Bash(*)`）就是真实的能力扩张事件，今天 Shipgate 只能说"trust root 被碰了，请人看"。

### 提议的具体模型：Capability Diff for Agent Hosts【战略建议】

把现有 `CapabilityFactV1`（identity/effect/authority/controls/evidence + 七类 hash）从"agent 项目的工具"推广到"coding-agent host 的权限授予"：

```yaml
# 每条 host 配置授予 = 一个 capability fact
identity:  {host: claude_code | codex | cursor, kind: mcp_server | permission_rule | hook | env}
effect:    {network: bool, filesystem: read|write, shell: bool, scope_pattern: "Bash(*)"}
authority: {auto_approved: bool, env_passthrough: [..], transport: stdio|http}
controls:  {requires_confirmation: bool}
```

diff 语义复用现有 broadened/narrowed/mixed。策略层复用 policy packs：

```yaml
rules:
  - id: ORG-MCP-NO-NEW-SERVER-WITHOUT-REVIEW
    match: {host_capability: {kind: mcp_server, change: added}}
    severity: high
    block: false        # require-review
  - id: ORG-NO-WILDCARD-BASH-ALLOW
    match: {host_capability: {kind: permission_rule, scope_pattern: "Bash(*"}}
    severity: critical
    block: true
```

**"permission boundary review"** = 把上述 host capability lock 存入 `.agents-shipgate/host-capabilities.lock.json`，PR 时 diff，扩张走 `human_review_required`，缩小自动 `mergeable`。所有基础设施（lock、diff、verdict、attestation）都已存在，这是推广不是新建。

这一步同时回答了 §10 的定位问题：它把目标用户从"开发 AI agent 产品的团队"（小）扩展到"用 coding agent 改代码的团队"（所有人）。

---

## 8. Security / Least-Privilege Review

### 总体：强。这是仓库最可信的部分。

**已落地的防线【仓库实证】**：

1. **静态-only 是合约不是口号**：AST 扫描器禁止 `exec/eval/__import__/compile/runpy/subprocess/importlib`（`.metadata`/`.resources` 除外）及别名规避；例外按 `(path, line, snippet, rationale)` 逐条钉死在 `ALLOWED_EXCEPTIONS`，行号漂移即测试失败（`tests/test_adapter_static_only.py`）。CI 把它放在全量测试之前 fail-fast（`ci.yml` step 7）。
2. **Reward-hacking 防御**：trust-root finding 不可抑制；`policy_weakened || trust_root_touched` ⇒ 强制人审；severity floor 不可越（越级降级需带过期时间的 acknowledgement，过期即 ConfigError 而非 warning）。
3. **Auto-fix 围栏**：dry-run 默认、`--confidence high` 默认、ManualPatch 无条件过滤、目标文件必须 `relative_to(manifest_dir)`（违者 exit 5）、SHA 漂移即拒绝、approval/confirmation/idempotency 证据**永久**禁止自动修复（`docs/autofix-policy.md`）。
4. **供应链**：Actions 全部 SHA-pin、uv Trusted Publishing（OIDC，无长期凭证）、sigstore 签名（wheel + sdist + SBOM）、pip-audit 每次 CI、85% 覆盖率发布门槛。
5. **输出脱敏默认开**：模式 + 敏感键双路径，`privacy_audit` 块记录脱敏统计（`core/privacy.py`）。

### 优先级排序的改进清单

| 优先级 | 项 | 证据/理由 |
|---|---|---|
| **P1** | **Symlink 逃逸测试**：`Path.resolve()` 跟随符号链接，manifest 内 symlink 理论上可把 `apply-patches` 的写目标或输入读取指出围栏外。`relative_to` 在 resolve 之后比较，恶意 symlink 解析后可能落在围栏内的假象路径 | `apply_patches.py:184-225`、`inputs/common.py:22-33`；加 `is_symlink()` 检查 + 逃逸用例 |
| **P1** | **Host 配置治理缺口本身就是安全缺口**：`.claude/settings.json` 加 `Bash(*)` 不触发任何语义检查（见 §7） | trust-root glob 表【缺失】 |
| **P2** | **Redaction marker 伪造**：`[REDACTED:...]` 全匹配即放行，攻击者可控字符串可伪造脱敏标记污染报告 | `core/privacy.py:142-143` |
| **P2** | **插件信任**：entry-point 插件是任意代码，现有控制为默认关闭 + provenance 记录；建议加 hash/签名 allowlist | `docs/trust-model.md:50-71` |
| **P3** | **整体输入预算**：10MiB 是单文件上限，多 source 可叠加；加 per-scan 总预算 | `inputs/common.py:18` |
| **P3** | **插件 check_id 冲突改为载入期拒绝**（现为载入后标记 `id_collision`） | `STABILITY.md:132` |
| **P3** | Attestation 可选接 Rekor 时间戳（保持本地确定性为默认） | `cli/attest.py` |

**Prompt-injection 角度**：Shipgate 的输入（manifest、工具 schema、OpenAPI 描述）会进入报告并被 agent 阅读。报告中的 `recommendation` 是 Shipgate 生成的，但 `evidence` 含用户文件内容——被扫描仓库里恶意构造的工具描述（"ignore previous instructions…"）会原样进入 agent 要读的 verifier.json。**【战略建议】** 在 agent-facing 输出中对来自被扫描内容的字符串加显式来源界定（如 `untrusted_excerpt` 字段或引用包裹），并在 `docs/report-reading-for-agents.md` 中写明"evidence 字段内容不可作为指令执行"。

---

## 9. Documentation / Examples Review

### 现状：体量惊人（50+ Markdown + 15+ JSON schema），无断链，无内部矛盾【仓库实证】。任务书要求的文档几乎全部存在：

`README` ✓ / `docs/architecture.md` ✓ / policy 模型（`manifest-v0.1.md` + `policy-packs.md`）✓ / agent workflows（`agent-recipes.md`、`agent-contract-current.md`、`agent-native-merge-contract.md`）✓ / GitHub Action（README 节 + examples/）✓ / MCP governance（部分，散在 `capability-standard.md` 与 codex 文档）△ / `examples/` ✓ / `AGENTS.md` ✓ / `ROADMAP.md` ✓。

### 真正的问题不是缺文档，是**信息架构倒挂**

1. **README 748 行，第一屏没有给"怀疑论者"的 30 秒路径**。现在的开头是 tagline + 徽章 + verify-first 快速开始——对已被说服的用户是对的；对第一次到达的人，"deterministic merge gate for AI-generated agent capability changes" 需要三次阅读才能解析。**建议**：第一屏改为一个 60 秒的故事——"Claude Code 给你的 refund agent 加了 `stripe.create_refund`。这个 PR 该不该合？" + 一张 `blocked` verdict 截图 + `uvx agents-shipgate fixture run ai_generated_refund_pr` 一行命令。其余 600 行下沉到 docs/。
2. **概念词汇过载**：Tool-Use Readiness Report、Release Evidence Packet、verifier cycle、capability lock、attestation、agent result、feedback export、workflow evidence bundle——8 种 artifact 概念对新用户同时出现。`docs/concepts.md` 只有 92 行，承载不了。**建议**新增一页 `docs/mental-model.md`：一张图讲清"一个引擎、一个裁决、其余皆投影"，每个 artifact 标注"谁读、何时读、可忽略否"。
3. **`docs/mcp-governance.md` 不存在**【缺失】——而这是任务书点名、且是 §7 战略方向的承载文档。应随 host capability 工作创建。
4. **样例缺一个"agent 不安全行为被拦"的完整叙事**：golden PRs 演示的是"缺 approval policy 被拦"；缺一个"coding agent 试图删除 Shipgate CI / 放宽 manifest 被 SHIP-VERIFY-* 拦下"的端到端 fixture——这恰恰是差异化卖点，应做成 `fixture run agent_weakens_gate` 并放进 README 第一屏故事。
5. **文档维护成本风险**：schema 版本注记散布在 README、STABILITY、agent-contract-current、llms.txt、.well-known 多处（项目记忆中已有 bump checklist 应对）。建议把"哪些文件随 schema bump 必改"做成 `scripts/check-contract-sync.py` 合约测试，替代人肉清单。

---

## 10. Product Positioning Review

### 直说

**当前定位（"the deterministic merge gate for AI-generated agent capability changes"）精确、诚实、且太窄。**

它要求目标用户同时满足三个条件：(a) 在开发 tool-using AI agent 产品；(b) 用 coding agent 写 PR；(c) 团队已经意识到 tool-surface 漂移是发布风险。三者交集在 2026 年中是一个很小的集合——这解释了为什么 pilot 难找、benchmark 没数据。`docs/category.md` 试图创建 "Tool-Use Readiness" 类目，但类目创建需要分发力量，小团队烧不起。

### 谁是第一用户？

诚实的答案排序：
1. **平台/安全工程师，所在团队大量使用 Claude Code/Codex**（不一定在做 agent 产品）——他们的痛是"agent 改了 CI 权限/加了 MCP server/动了 workflow，没人看见"。这个痛**今天每周都在发生**，且没有现成工具。
2. 正在做 production tool-using agent 的团队（当前定位的目标）——痛是真的但人群小、且往往有自建审查。
3. AI coding agent 本身作为"用户"——长期正确，但 agent 不会自发采用没有进入其训练语料/指令的工具（empty CSV 是证据）。

### 建议的定位校准【战略建议】

不推翻，做**同心圆扩展**：

> **内核（不变）**：deterministic capability-diff 引擎 + 不可削弱的 trust root。
> **环 1（现有）**：AI agent 项目的 tool-surface 合并门禁。
> **环 2（建议 6 周内启动，§7 的 host capability 工作）**："**当 coding agent 改变它自己或你的 agent 能做什么时，Shipgate 决定能不能合。**" 覆盖 `.mcp.json`、`.claude/settings.json`、workflow permissions、agent 指令文件——所有用 coding agent 的仓库都适用。

一句话压力测试：对一个没听过 Shipgate 的工程师说"Semgrep 看代码漏洞，Shipgate 看**能力变更**——你的 AI 改了它能碰什么的时候，有人盘问它"——这句比 "Tool-Use Readiness" 在 5 秒内可懂。

### 与邻类的区别（为什么不是 X）

- **不是 linter**：裁决对象是能力 delta 不是代码风格；带 merge 权限语义（human authority routing）。
- **不是 OPA/policy-as-code 通用引擎**：OPA 给你表达式语言让你自己建模；Shipgate 自带 agent 能力的领域模型（capability facts、trust root、autofix boundary）。代价是表达力天花板（§4），换来的是开箱即用 + 不可被 agent 绕过。
- **不是 Semgrep**：Semgrep 匹配代码模式；Shipgate 比对**声明的权限面**随 PR 的语义变化，并对"修改门禁本身"免疫。
- **不是 branch protection/CODEOWNERS**：那是路由谁来看；Shipgate 产出**看什么、为什么、能不能不看人**。两者应集成（§6 建议）而非竞争。

---

## 11. Adoption / Distribution Strategy

### 现实约束

小团队、零已记录的 adoption 数据、一次部分失败的 pilot、PyPI 0.11.0 已就绪、Action 已上 marketplace、分发面（六种 kit、Codex marketplace、llms.txt）已建完。**分发基建不缺，缺的是被分发的证据（proof）。**

### 90 天战术（按序）

**第 1-2 周：制造证据。**
1. 跑掉 W2 baseline benchmark（矩阵和 runner 都现成），**把真实数据填进空 CSV 并公开**。即使分数难看也比空文件强——"我们测了 agent 自发发现率，结果是 X" 本身就是稀缺内容。
2. 修 `insufficient_evidence`（§4 弱点 1），让 Stripe-类仓库的第一次运行产生可行动结果，然后回去把那个 pilot 救活。
3. 做 `fixture run agent_weakens_gate`（§9 建议 4）并录一个 90 秒视频：Claude Code 试图删 CI 门禁 → `blocked` + 人审路由。这是唯一一个看一遍就懂护城河的 demo。

**第 3-6 周：10 个真实用户。**
4. design-partner 漏斗换抓手：不再问"你在做 agent 产品吗"，改问"你的团队用 Claude Code/Codex 吗？想看它这个月改了哪些能力面吗？"——用 §7 的 host capability 扫描做免安装的 **`shipgate audit --host`** 一次性报告（零配置、只读、出一页 Markdown），作为获客钩子。
5. 内容：两篇有数据的文章——《我们让 8 种 coding agent 在没有提示的情况下找安全门禁，结果如下》（benchmark 数据）+《一个 AI 生成的 PR 如何悄悄拿到 refund 权限》（golden PR 叙事）。投 r/ExperiencedDevs、HN、AI engineering 社区。安全社区角度单独写 trust-root/reward-hacking 设计文（这是安全人群会转发的内容）。
6. 把 Action 的 marketplace listing 信息密度提到与 README 第一屏一致（截图 + 一行接入）。

**第 7-12 周：让 agent 替你分发。**
7. 给 Anthropic/OpenAI 的 agent 文档生态投 PR/提案：Claude Code 的 hooks 示例库、Codex plugin 目录——成为"官方示例里出现的那个门禁"。
8. 100 stars 的路径不是求 star，是上面 5 的两篇内容 + HN 一次首页。准备好仓库的"着陆 30 秒"（§9 建议 1）再发。
9. **从 agent 工作流收集反馈而非只收人类反馈**：`feedback capture`（roadmap 项 2）已有首版——把每个 design partner 的 verify 前后对、verdict 转移、`suspected_gate_bypass` 信号变成 replayable scenario，公开脱敏后的 governance case 数量作为周指标。

---

## 12. Competitive / Adjacent Landscape

| 邻类 | 代表 | Shipgate 应学什么 | Shipgate 不应变成什么 |
|---|---|---|---|
| Linting / static analysis | ruff, ESLint | 零配置首跑体验 | 通用代码质量工具 |
| 安全扫描 | Snyk, Trivy | 免安装 audit 获客（§11.4） | CVE 数据库竞争 |
| Policy-as-code | OPA/Conftest | 组织级策略分发模型（policy packs v2 可借鉴 bundle 机制） | 通用策略表达式引擎——领域模型才是差异化 |
| 代码模式扫描 | Semgrep | 规则市场/registry 的社区飞轮 | 用 capability 模型去匹配任意代码模式 |
| 供应链评分 | OpenSSF Scorecard | "一个分数 + 徽章"的传播力——考虑 `shipgate badge` | 静态打分器（裁决必须保持 PR 粒度） |
| MCP 安全 | mcp-scan 等新生工具 | **这是最近的碰撞区**：谁先定义 "MCP 配置变更治理" 谁拿走类目 | 运行时 MCP gateway（明确非目标，`docs/category.md`） |
| Agent runtime 护栏 | guardrails 框架、LLM firewall | 互补叙事：Shipgate 管 merge 前，他们管运行时 | 运行时执行层 |
| GitHub 原生 | branch protection, CODEOWNERS | 深度集成（Check Run、reviewer 路由） | 被一个 GitHub 原生功能"顺手做了"——**防御靠 trust-root 语义深度，GitHub 不会做 reward-hacking 抵抗** |

**应该成为**：coding-agent 时代的"能力变更裁决层"——窄、深、不可绕过。
**不应成为**：第 14 个静态分析器、运行时代理、或通用 policy 引擎。

---

## 13. Recommended Roadmap

| Timeframe | Priority | Initiative | Why it matters | Complexity | Acceptance criteria |
|---|---|---|---|---|---|
| 立即（1-2 周） | P0 | 跑 W2 adoption baseline 并公开数据 | 一切 agent-native 假设目前零证据；空 CSV 是信誉负债 | 低（harness 已建好） | `benchmark/results/` 含 ≥16 cell 真实分数；README 链接结果 |
| 立即（1-2 周） | P0 | `insufficient_evidence` 可行动化：config-bound/dynamic-factory 检测 + 具体 next_action（含 inventory 骨架生成） | 唯一真实 pilot 的失败模式；首跑体验决定留存 | 中 | Stripe 型仓库（dynamic toolkit）首跑产出具体修复命令而非"提供更好的输入"；pilot 复跑得到非 IE 裁决 |
| 立即（1-2 周） | P1 | `fixture run agent_weakens_gate` + 90 秒 demo 视频 + README 第一屏重构 | 护城河（trust root）目前没有一个 30 秒可懂的展示 | 低 | 新 fixture 展示 SHIP-VERIFY-CI-GATE-REMOVED → blocked → human 路由；README 首屏 ≤60 行进入第一个命令 |
| 立即（1-2 周） | P1 | Symlink 逃逸修复 + 测试；redaction marker 伪造修复 | §8 P1/P2 安全项，修复成本低 | 低 | 新增逃逸/伪造用例全绿；`is_symlink()` 检查落地 |
| 近期（4-6 周） | P0 | **Host capability governance**：`.claude/settings.json`、`.mcp.json`、`.cursor/*`、workflow `permissions:` 进入 capability fact 模型（§7） | 把目标市场从"做 agent 的团队"扩展到"用 agent 的团队"；与 codex_boundary、Phase 2 PR gate 方向一致 | 中-高 | host capability lock + diff 落地；新 MCP server / `Bash(*)` allow / workflow 权限扩张触发 `human_review_required`；codex_boundary 拆分为子包 |
| 近期（4-6 周） | P1 | `shipgate audit --host` 零配置一次性报告（获客钩子） | design-partner 漏斗当前抓手太窄 | 低-中（复用 host capability 扫描） | 在无 shipgate.yaml 的仓库一条命令产出一页 Markdown 能力面盘点 |
| 近期（4-6 周) | P1 | GitHub Check Run + 行级 annotations + risk label/reviewer 路由配方 | comment+SARIF 不足以进 branch protection 的主流路径 | 中 | merge_verdict 映射 check conclusion；finding 行级标注；`examples/.../09-risk-labels.yml` |
| 近期（4-6 周） | P2 | 三个 design partner 跑完 verifier pilot（runbook 已有） | 产品方向需要真实 PR 流校准 | 低（运营为主） | 3 份 redacted feedback artifact 入库为 governance case |
| 中期（2-3 月） | P1 | Claude Code PreToolUse 拦截 hook（trust-root 保护面的事中 deny） | 从"PR 门禁"升级为"agent 循环内边界"；竞品无人有此位 | 中 | agent 编辑保护面文件被 hook 拦截并收到解释 + 正确流程指引；可一键安装 |
| 中期（2-3 月） | P1 | 薄 MCP server 模式（preview/verify/explain 三工具，本地、不出网） | 无 shell 的 agent 环境接入硬墙；分发面进 MCP 目录生态 | 中 | Claude Code 经 MCP 调用 `shipgate_preview_diff` 得到与 CLI 字节一致的裁决投影 |
| 中期（2-3 月） | P2 | Policy packs v2：条件谓词（嵌套 AND/OR + 数值比较）+ 组织级 pack 分发 | 当前表达力天花板挡住平台团队采购（§4） | 中-高 | "financial 且 amount>1000 须 approval" 可声明；pack 可从 org repo 引用并锁 hash |
| 中期（2-3 月） | P2 | Report 顶层块收敛 + v1.0 schema RC | 25 个 schema 版本/顶层块膨胀将锁死 1.0 承诺 | 中 | 顶层块数量收敛后冻结 RC；迁移指南 |
| 长期（6-12 月) | P1 | Attestation 消费端：跨仓库 capability registry + deploy 系统对接 | 从"PR 工具"变成"组织的能力变更账本"——平台级护城河 | 高 | 多仓库 attestation 聚合查询："过去 30 天谁给哪个 agent 加了什么权限，谁批的" |
| 长期（6-12 月） | P2 | 托管面（dashboard/org baseline/SSO），按既有承诺独立于 OSS 核心 | 商业化载体（README 定价立场已预留） | 高 | 与 OSS 边界清晰；design partner 转付费 ≥1 |

---

## 14. Concrete Next Engineering Plan

按依赖顺序的前六步（不实施，仅规划）：

1. **修 `insufficient_evidence`**
   - 文件：`ci/release_decision.py`（IE 分支输出结构化 `evidence_gaps[]`：每个低置信工具 → 缺什么输入 → 哪条命令补）；`inputs/_python_framework.py` + 各 adapter（识别 config-bound/factory 模式并产出 `SHIP-EVIDENCE-DYNAMIC-FACTORY` finding 替代静默降置信）；新命令 `init --suggest-inventory`（生成 tool-inventory 骨架 JSON）。
   - 测试：以 Stripe pilot 形态构造 fixture（dynamic toolkit 工厂），断言裁决附带可执行 next_action；现有 IE 用例不回归。
   - 风险：阈值语义变化可能影响既有用户的裁决分布——保持阈值不动，只增强输出，零裁决回归。

2. **Host capability governance（环 2）**
   - 新模块：`inputs/agent_hosts/{claude_code,cursor,mcp_json,github_workflows}.py`（解析 host 配置为 `HostCapabilityFact`，复用 `schemas/capabilities.py` 加 `subject_kind: "host_grant"`）；`core/codex_boundary.py` 拆为 `core/host_boundary/` 子包并把 Codex 作为其中一个 host。
   - Schema：`docs/host-capability-schema.v0.1.json`；lock 文件 `.agents-shipgate/host-capabilities.lock.json`。
   - 检查：`SHIP-HOST-MCP-SERVER-ADDED`、`SHIP-HOST-PERMISSION-BROADENED`、`SHIP-HOST-WORKFLOW-PERMISSIONS-EXPANDED`、`SHIP-HOST-SECRET-REF-ADDED`，全部归入不可抑制类目。
   - 文档：新建 `docs/mcp-governance.md`（任务书点名缺失项）。
   - 测试：每 host 一组 golden 配置对（before/after）断言 diff 方向与裁决；trust-root glob 表扩充的回归测试。

3. **GitHub Check Run 集成**
   - `action.yml` 增 `check_run: true` 输入；新 `scripts/github_check_run.py`（Checks API，annotation ≤50/批）；`report/sarif.py` 的 region 信息复用为 annotation 行号。
   - 验收：blocked PR 显示红色 check + 行级标注；branch protection 可 require。

4. **PreToolUse hook**
   - `cli/install_hooks.py` 增 `--target claude-code-pretooluse`；hook 脚本读 trust-root glob 表（从 `checks/verify.py` 提为共享数据文件 `docs/trust-roots.json`，双端消费），Edit/Write 目标命中保护面即返回 deny + `next_action`。
   - 测试：hook 脚本单测（无需真实 Claude Code）；glob 表双端一致性合约测试。

5. **薄 MCP server**
   - 新 `src/agents_shipgate/mcp_server/`（可选 extra `[mcp]`，依赖 `mcp` SDK）；三工具 `preview_diff / verify / explain_finding`，全部进程内调用现有编排器，输出为 verifier.json 投影。
   - 静态-only 合约：MCP server 进 `ALLOWED_EXCEPTIONS` 审计（本地 stdio，不出网），并在 trust-model.md 注明。

6. **README/docs 信息架构重构**
   - README 收敛至 ~150 行（故事 + 一条命令 + verdict 表 + 三个出口链接）；新 `docs/mental-model.md`；其余内容下沉。`scripts/check-contract-sync.py` 替代人工 schema-bump checklist。

**总体风险与取舍**：(a) 环 2 扩张与 "no more adapters" 非目标的张力——host 配置解析不是框架 adapter，是 trust-root 论点的直接延伸，应在 ROADMAP 里明文区分以免原则被稀释；(b) 表面积继续增长 vs 收敛——上述 1/3/4/5 都是对既有裁决的新投影，不新增裁决语义，符合"无第二裁决"原则；(c) PreToolUse/MCP server 引入宿主生态耦合——以共享数据文件（trust-roots.json）而非代码耦合控制半径。

---

## 15. Open Questions

1. **环 2 的命名与边界**：host capability governance 发布时是 Shipgate 的新模式，还是独立子命令品牌（`shipgate host`）？影响类目叙事。
2. **W2 benchmark 若分数极低**（agent 完全不自发发现），是否把资源从"discovery 优化"转向"人类安装、agent 服从"的路径？需要数据后决策。
3. **policy packs v2 的表达式语言选型**：自研受限谓词 vs 嵌入 CEL——后者表达力强但引入解释器信任面，与静态-only 哲学的相容性需要论证。
4. **商业化时点**：README 承诺核心永久 OSS；托管面启动的触发条件是什么（N 个付费意向？环 2 验证？）——当前未定义。
5. **`shipgate` 与 `agents-shipgate` 双命名**（`pyproject.toml` 两个 script 入口）长期收敛到哪个？品牌一致性 vs 兼容成本。
6. **Stripe pilot 的后续**：IE 修复后是否还有重启窗口？design-partner 漏斗的当前真实状态仓库内不可见。

---

## 16. Final Verdict

- **最强资产**：trust-root / reward-hacking 抵抗的那一层——不可抑制检查、severity floor、self-approval 强制人审、确定性 capability diff、由 AST 合约测试钉死的静态-only 信任模型。这是别人需要重走一年纪律才能复制的部分。
- **最大弱点**：需求证据。空的 benchmark CSV、单一且部分失败的 pilot、为尚未到来的用户建好的六种接入面。工程在以产品已验证的方式运转，而产品还没被验证。
- **最重要的战略修正**：从"做 AI agent 的团队的工具面门禁"扩展到"**用 coding agent 的团队的能力变更门禁**"（host capability governance）。引擎已经为此长好了，市场大两个数量级，且与既有护城河同根。
- **最高杠杆的工程改动**：修 `insufficient_evidence` 的可行动性。它同时解锁 pilot 复活、首跑体验、和"verdict 永远给出下一步"的产品承诺。
- **最佳采用楔子**：`shipgate audit --host` 零配置一次性报告 + "agent 试图削弱门禁被拦"的 90 秒 demo。
- **建议的下一个里程碑**：**"v0.12 — Evidence"**：真实 benchmark 数据公开 + IE 可行动化 + 三个 design partner 跑完 + host capability 首版。不是更多表面积。
- **残酷的诚实评估**：今天的 Shipgate，对绝大多数团队是 nice-to-have——因为它守护的事故（agent 越权合并）大多数团队还没经历过，而它的安装与概念成本不低。但它押的方向——coding agent 写大部分 PR、人类只裁决权限边界——正在以季度为单位变成现实。**它拥有成为 must-have 基础设施层的全部工程要件，唯独还没有完成从"为那个世界建好了"到"被那个世界需要着"的惊险一跃。** 未来 90 天把证据补上，这个判断就会改写；继续堆表面积，它会成为一个被引用、被尊敬、但没人用的优雅仓库。

---

*报告基于 commit `ef58a57`。所有文件路径与行号以该 commit 为准。*
