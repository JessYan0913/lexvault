# lexvault — 自建多法域法律库 + MCP 查询系统

从公开法律数据源抓取法规/制裁名单正文、拆条入库（SQLite + FTS5），并通过 MCP 提供条文级全文检索。

## 数据源（已接入）

| 法域 | 内容 | 适配器 | 拉取脚本 |
| --- | --- | --- | --- |
| cn | 宪法、法律、行政法规、司法解释（全国人大库 flk.npc.gov.cn） | `lexvault/adapters/npc.py` | `scripts/fetch_laws.py` / `fetch_major.py` |
| us | 美国联邦法规 eCFR（涉外核心 91 parts：金融制裁/出口管制/ITAR/CITES/银行） | `lexvault/adapters/ecfr.py` | `scripts/fetch_ecfr.py` |
| us_code | 美国法典 US Code（涉外核心 5 Title：50 战争/22 外交/15 商业/31 金融/19 关税，9187 sections） | `lexvault/adapters/uscode.py` | `scripts/fetch_uscode.py` |
| us_ofac | OFAC 合并制裁名单（美国财政部 SLS，19776 实体） | `lexvault/adapters/ofac.py` | `scripts/fetch_ofac.py` / `scripts/refresh_sanctions.py` |
| eu_sanctions | 欧盟合并金融制裁名单（opensanctions eu_fsf 全量，6128 实体，含 EU-UKR 俄罗斯制度） | `lexvault/adapters/eu_sanctions.py` | `scripts/import_eu_opensanctions.py` / `scripts/refresh_sanctions.py` |
| uk_sanctions | 英国制裁名单（UK OFSI，6338 实体） | `lexvault/adapters/uk_sanctions.py` | `scripts/fetch_uk_sanctions.py` / `scripts/refresh_sanctions.py` |
| eu | 欧盟涉外核心法规（EUR-Lex：制裁/双重用途出口管制/GDPR/反胁迫/外国补贴） | `lexvault/adapters/eurlex.py` | `scripts/fetch_eurlex.py` |

## 快速开始

```bash
# 1. 建库（若 db 不存在）
python3 - <<'EOF'
from lexvault.core.store import Store
s = Store("db/lexvault.db"); s.connect(); s.ensure_schema(); s.close()
EOF

# 2. 拉取中国主要法典（按关键词引导）
python3 scripts/fetch_major.py --list                     # 查看内置关键词
python3 scripts/fetch_major.py --keywords 宪法,刑法        # 按关键词拉取

# 3. 全量拉取某分类（法律 120 / 行政法规 210 / 司法解释 320,330,340）
python3 scripts/fetch_laws.py --codes 120 --dry-run       # 预览待拉清单
python3 scripts/fetch_laws.py --codes 210 --since 2010-01-01 --limit 50   # 限日限条数
python3 scripts/fetch_laws.py --codes 320,330,340         # 全量

# 4. 拉取 OFAC 制裁名单
python3 scripts/fetch_ofac.py --dry-run                   # 预览
python3 scripts/fetch_ofac.py                             # 入库

# 5. 拉取欧盟涉外核心法规
python3 scripts/fetch_eurlex.py --dry-run                # 预览
python3 scripts/fetch_eurlex.py                          # 入库

# 6. 拉取美国法典 US Code 涉外核心 Title
python3 scripts/fetch_uscode.py --dry-run              # 预览
python3 scripts/fetch_uscode.py --year 2024           # 入库 2024 版

# 7. 启动 MCP 服务
python3 run_mcp.py

# 8. 批量制裁名单筛查（企业出海尽调）
python3 scripts/screen_sanctions.py --input clients.csv --output report.csv   # 默认查 OFAC + EU + UK
python3 scripts/screen_sanctions.py --input clients.csv --jurisdiction us_ofac # 只查 OFAC

# 10. 拉取英国制裁名单
python3 scripts/fetch_uk_sanctions.py --dry-run

# 9. 拉取欧盟制裁名单
python3 scripts/fetch_eu_sanctions.py --dry-run
```

## 目录结构

```
db/lexvault.db            SQLite 库（legal_documents / document_versions / document_sections / jurisdictions + FTS5(trigram)）
run_mcp.py                MCP 服务入口（stdio，供 mcp.json / 客户端调用）
lexvault/core/            数据模型协议 + 入库/查询层（Store）
lexvault/adapters/        npc 全国人大库 / ofac 制裁名单
lexvault/mcp/             MCP 查询服务（FastMCP，本地库为主 + 在线兜底）
scripts/fetch_major.py    关键词拉取主要法典
scripts/fetch_laws.py     分类全量拉取（去重/排除废止/限日期/限条数/幂等）
scripts/fetch_ofac.py     OFAC 制裁名单入库
deploy/README.md          部署指南（本地 / GitHub Actions / Turso / 公网）
tests/                    冒烟 / 适配器 / MCP 端到端测试
```

## 核心特性

- **多法域兼容**：统一 `DocumentRecord` 协议，新增法域只需实现一个适配器。
- **版本可追溯**：`document_versions` 保存历史版本，`is_current` 标记现行文本。
- **条文级检索**：FTS5 trigram 分词（≥3 字用 MATCH，2 字词自动回退 LIKE，兜底同时查 heading+body）。
- **幂等入库**：按 `(jurisdiction_id, doc_key)` upsert，重复运行安全。
- **抓取审计**：每次运行写 `source_records`，可回溯更新历史。
- **频率控制**：拉取脚本内置限速（翻页间隔 + 每部间隔 + 失败退避），避免给对方网站造成压力。

## MCP 工具

- `search_local` — 本地全文检索（可按法域过滤）
- `get_document` / `get_sections` — 按文档取元数据/条文
- `list_documents` / `stats` — 库内文档与统计
- `online_npc_search` / `online_npc_fetch` — 全国人大库在线兜底

## 测试

```bash
python3 tests/test_smoke_ingest.py   # 数据模型 + 入库层
python3 tests/test_npc_adapter.py    # NPC 适配器（含真实民法典 1260 条）
python3 tests/test_mcp_server.py     # MCP 端到端（需 mcp 依赖）
```

详见 [deploy/README.md](deploy/README.md) 部署与运维说明。
