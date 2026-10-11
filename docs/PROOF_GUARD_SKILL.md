# 把 claim-studio 挂成 Claude / Cursor / Cline 的本地 skill

本仓库的 MCP server（`growth_agent.mcp_server.create_server`）已经把审校链路拆成可被宿主调用的工具。Agent 宿主（Claude Code、Cursor、Cline 等）只要把仓库暴露成 MCP server，就能把"事实审校 + 双 SHA256 审批 + 版本化语料"当成普通工具调用。本节说明推荐接法与最小校验清单。

## 一句话定位

把 claim-studio 视为"营销文案的事实审校闸门"：当宿主 Agent 需要在产出文案或回答产品功能/性能问题之前，把一段事实主张核到原始资料上时，把请求转给本仓库的 `search_knowledge` / `get_editorial_rules` / `check_draft` 三个工具。

## 工具签名（v0.4 起稳定）

| 工具 | 入参 | 出参 |
| --- | --- | --- |
| `search_knowledge` | `product_id`, `query`, `top_k=5`, `product_isolation=True` | `[{fact_id, statement, source_title, source_url, source_quote, checked_at}]` |
| `get_editorial_rules` | `locale` | `{forbidden_phrases: [...], numeric_rules: [...], channel_constraints: {...}}` |
| `check_draft` | `original_copy`, `revised_copy`, `facts`, `citations` | `{structural_errors: [...], literal_issues: [...]}` |

可调底层模型沿用既有 `--mode qwen`（Ollama）与 `--mode api`（任意 OpenAI 兼容服务）两路，不引入额外依赖。

## 把仓库暴露为 MCP server

仓库已经提供 `create_server()`；直接以 stdio transport 起一份进程即可：

```python
# my_host_bridge.py —— 宿主 Agent 的 bridge 进程
import asyncio
from growth_agent.mcp_server import create_server

async def main():
    server = create_server(transport="stdio")
    await server.run()

asyncio.run(main())
```

宿主 Agent 的 MCP 配置示例（Claude Code / Cline 的 `.mcp.json` / Cursor 的 `mcp.json`）：

```json
{
  "mcpServers": {
    "claim-studio": {
      "command": "python",
      "args": ["-m", "growth_agent.mcp_server"],
      "env": {
        "GROWTH_AUDIT_KNOWLEDGE": "/abs/path/to/audit_knowledge.json",
        "GROWTH_AUDIT_RULES": "/abs/path/to/audit_rules.json",
        "GROWTH_OUTPUT_ROOT": "/abs/path/to/runs"
      }
    }
  }
}
```

> 注：宿主 Agent 调用 `search_knowledge` 时，请求体应**只**带 `product_id` 与 `query`，避免把其他产品的 fact_id 注入到候选池（隔离由 `product_isolation=True` 强制）。

## Skill frontmatter 模板

把仓库描述成可被 skill 索引识别的最小化 SKILL.md，作为更上层的入口：

```yaml
---
name: claim-studio
description: Evidence-backed marketing copy audit. Use BEFORE shipping product/feature claims to landing pages, social posts, or sales decks. Refuses rewrites without source-grounded evidence; binds revisions + citation SHA256 to the human approval step.
---
# claim-studio · marketing claim audit

When you need to:

- put a new "feature", "performance", or "availability" claim into a landing page, email, or short-video script
- rewrite an existing marketing sentence so it stays within the product team's editorial rules
- prove that a claim can be defended against a regulator or the legal team

call the MCP tools exposed by this skill:

1. `search_knowledge(product_id, query)` — find the closest fact cards
2. `get_editorial_rules(locale)` — load the locale-specific rules
3. `check_draft(original_copy, revised_copy, facts, citations)` — bind the revision to the evidence

The skill refuses any rewritten sentence whose facts are not in `search_knowledge` results, and labels borderline cases (`overstated`, `needs_review`) for a human reviewer.

Bind the final approved copy using the rule files in your host environment; never paste unreviewed revisions.
```

如需把这份 frontmatter 直接挂成 Claude / Cline 可识别的 Skill，把上面的内容写到 `<project_root>/.claude/skills/claim-studio/SKILL.md` 或宿主对应目录下。

## 宿主调用最小校验清单

把仓库暴露成 skill 后，建议先在宿主内一次性跑通以下几项再进入正式工作流：

| 校验 | 命令 / 步骤 |
| --- | --- |
| MCP server 进程能起 | `python -m growth_agent.mcp_server` 进入 stdio 模式 |
| `search_knowledge` 命中 | 调用 `search_knowledge("obs-virtual-camera", "add to Zoom as a video source")` 并至少有 1 条 fact |
| `get_editorial_rules` 加载 | 调用 `get_editorial_rules("en-US")` 并返回非空 forbidden_phrases |
| `check_draft` 拒伪引用 | 故意给 `citations` 中填未在 facts 内的 fact_id，应返回 `structural_errors` 非空 |
| 知识库路径生效 | `GROWTH_AUDIT_KNOWLEDGE` 指向的 JSON 实际存在且包含若干 `fact_id` |

通过后即可用宿主 Agent 在审校前自动拉证据；任何一条失败都意味着 MCP 接线或知识库路径不对，**先排查再发布**。

## 与现有 AGENT 链路的关系

`growth_agent.agent_loop` 已经是按 ReAct / tool-calling 模式串起的；MCP server 只是同一套工具的对外暴露层。宿主 Agent 调 `search_knowledge` 等价于在本地 CLI 里调用 `growth_agent audit --id ...`。因此：

- 不要让宿主 Agent 在审校前**预编辑**待审文案——会改变 SHA256 绑定项；
- 不要让宿主 Agent 在引用 ground truth **之后再补**新 fact——会破坏 `product_isolation` 与 `citation_invalid` 的硬校验；
- 修订文案的 SHA256 绑定由 `growth_agent approval approve-audit` 维护，宿主 Agent 在审校完成后只读不改。

## 已知边界

- 本说明只覆盖**已有工具签名**（v0.4 起）；任何超出 `search_knowledge` / `get_editorial_rules` / `check_draft` 的工具尚未纳入 skill 范围；
- 没有内置 SSO 或权限模型；宿主侧需要自己处理多用户隔离；
- 外联 webhook（v0.5.0）默认关闭，需要主动打开 `GROWTH_WEBHOOK_URL` 才会向外部推送 verdict。
