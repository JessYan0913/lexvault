# lexvault — 自建多法域法律库 + MCP 查询系统

从公开法律数据源抓取法规正文、拆条入库（SQLite + FTS5），并通过 MCP 提供条文级检索。

## 快速开始

```bash
# 1. 建库
python3 - <<'EOF'
from lexvault.core.store import Store
s = Store("db/lexvault.db"); s.connect(); s.ensure_schema(); s.close()
EOF

# 2. 拉取首批数据（关键词引导）
python3 scripts/update.py --keyword 民法典 --max-docs 1

# 3. 增量更新（最近 7 天公布/修改的法规）
python3 scripts/update.py --dry-run   # 预览
python3 scripts/update.py             # 实际入库

# 4. 启动 MCP 服务
python3 -m lexvault.mcp.server
```

## 目录结构

```
db/schema.sql          建表 SQL（6 业务表 + FTS5(trigram) + 触发器 + 视图）
lexvault/core/         数据模型协议 + 入库/查询层（Store）
lexvault/adapters/     数据源适配器（npc 全国人大库 / ofd_reader 正文兜底）
lexvault/mcp/          MCP 查询服务（FastMCP，本地库为主 + 在线兜底）
scripts/update.py      定时增量更新脚本（日期窗口 + 幂等 upsert + 审计）
deploy/README.md       部署指南（本地 / GitHub Actions / Turso / 公网）
.github/workflows/     GitHub Actions 定时更新示例
tests/                 冒烟 / 适配器 / MCP 端到端测试
```

## 核心特性

- **多法域兼容**：统一 `DocumentRecord` 协议，新增法域只需实现一个适配器。
- **版本可追溯**：`document_versions` 保存历史版本，`is_current` 标记现行文本。
- **条文级检索**：FTS5 trigram 分词（≥3 字用 MATCH，2 字词自动回退 LIKE）。
- **幂等入库**：按 `(jurisdiction_id, doc_key)` upsert，重复运行安全。
- **抓取审计**：每次运行写 `source_records`，可回溯更新历史。

## 测试

```bash
python3 tests/test_smoke_ingest.py   # 数据模型 + 入库层
python3 tests/test_npc_adapter.py    # NPC 适配器（含真实民法典 1260 条）
python3 tests/test_mcp_server.py     # MCP 端到端（需 mcp 依赖）
```

详见 [deploy/README.md](deploy/README.md) 部署与运维说明。
