# lexvault 部署文档

自建法律库（SQLite + FTS5）+ MCP 查询系统的部署指南。覆盖三种典型部署形态：本地、GitHub Actions 定时更新 + Turso 云库、公网 MCP 服务。

---

## 1. 架构总览

```
┌─────────────┐   scripts/update.py    ┌─────────────────────┐
│ 全国人大库   │ ─────────────────────▶ │  SQLite (db/*.db)   │
│ flk.npc.gov │  搜索→详情→docx→拆条    │  FTS5(trigram)       │
└─────────────┘                        └──────────┬──────────┘
                                                  │ 查本地为主
                                                  ▼
┌────────────────────────────────────────────────────────────┐
│ lexvault-mcp（FastMCP）                                    │
│   search_local / get_document / get_sections /              │
│   list_documents / stats   +  online_npc_search/fetch 兜底  │
└────────────────────────────────────────────────────────────┘
```

- **采集层**：`lexvault/adapters/npc.py`（全国人大库）；可新增其他法域适配器（欧盟/美国/香港…），统一输出 `DocumentRecord` 协议。
- **入库层**：`lexvault/core/store.py`（按 doc_key 幂等 upsert，FTS 同步触发器）。
- **查询层**：`lexvault/mcp/server.py`（FastMCP，查本地库为主，在线 NPC 兜底）。
- **定时更新**：`scripts/update.py`（按 gbrq 日期窗口增量拉取，幂等写入 + 审计）。

---

## 2. 本地快速开始

```bash
# 0) 准备 Python（需 3.10+，SQLite ≥ 3.34 以支持 trigram）
python3 --version

# 1) 初始化库（建表）
python3 - <<'EOF'
from lexvault.core.store import Store
s = Store("db/lexvault.db"); s.connect(); s.ensure_schema(); s.close()
print("schema ready")
EOF

# 2) 拉取首批数据（关键词引导）
python3 scripts/update.py --keyword 民法典 --max-docs 1

# 3) 增量更新（最近 7 天公布/修改的法规）
python3 scripts/update.py --dry-run   # 预览
python3 scripts/update.py             # 实际入库

# 4) 启动 MCP 服务（stdio）
LEXVAULT_DB=db/lexvault.db python3 run_mcp.py
```

### 客户端接入 MCP（Claude / Cursor / 任意 MCP 客户端）

在 MCP 客户端配置里注册：

```json
{
  "mcpServers": {
    "lexvault": {
      "command": "python3",
      "args": ["/Users/yanheng/Documents/学习/涉外法律/lexvault/run_mcp.py"],
      "cwd": "/绝对路径/lexvault",
      "env": { "LEXVAULT_DB": "/绝对路径/lexvault/db/lexvault.db" }
    }
  }
}
```

工具清单：`search_local`（FTS 条文检索）、`get_document`、`get_sections`、`list_documents`、`stats`、`online_npc_search`、`online_npc_fetch`。

---

## 3. 定时增量更新（GitHub Actions 示例）

`scripts/update.py` 已支持日期窗口增量（`gbrq` 区间过滤）与幂等 upsert，天然适合在 CI 里定期跑。

### 3.1 在 GitHub Actions 中定时更新 SQLite 库文件并推回仓库

```yaml
# .github/workflows/update-lexvault.yml
name: update-lexvault

on:
  schedule:
    - cron: "0 22 * * *"     # 每天 22:00 UTC（北京时间 06:00）
  workflow_dispatch:         # 支持手动触发

jobs:
  update:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Run incremental update
        run: |
          python3 scripts/update.py --db db/lexvault.db --days 7

      - name: Commit & push updated db
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add db/lexvault.db
          git commit -m "chore: update lexvault db $(date -u +%F)" || echo "no changes"
          git push
```

> 注意：本仓库 `.gitignore` 默认忽略 `db/*.db`；如需用此方案，请取消忽略（在 `.gitignore` 中删除 `db/*.db` 两行，或 `git add -f db/lexvault.db`）。

### 3.2 更新到 Turso（云 SQLite，多端共享）

[Turso](https://turso.tech) 提供与 SQLite 兼容的云端数据库，免费额度适合个人库。

```yaml
# .github/workflows/update-lexvault-turso.yml
name: update-lexvault-turso

on:
  schedule:
    - cron: "0 22 * * *"
  workflow_dispatch:

jobs:
  update:
    runs-on: ubuntu-latest
    env:
      TURSO_DATABASE_URL: ${{ secrets.TURSO_DATABASE_URL }}   # libsql://xxx.turso.io
      TURSO_AUTH_TOKEN:   ${{ secrets.TURSO_AUTH_TOKEN }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }

      - name: Install turso CLI
        run: |
          curl -sSfL https://get.turso.tech | sh
          echo "$HOME/.turso" >> "$GITHUB_PATH"

      - name: Update local db
        run: python3 scripts/update.py --db /tmp/lexvault.db --days 7

      - name: Push to Turso
        run: turso db shell "$TURSO_DATABASE_URL" < /dev/null 2>/dev/null || true
```

Turso 同步 SQLite 的推荐做法是使用 `turso db shell` 执行 SQL，或使用 [libsql 的 dump/restore](https://docs.turso.tech/)。更简便：本地 `sqlite3 db/lexvault.db .dump` 后管道进 Turso shell。读者可按需挑选，核心是 `scripts/update.py` 的幂等性保证重复执行安全。

### 3.3 部署公网 MCP 服务（Oracle Cloud / Render / Fly.io）

`lexvault/mcp/server.py` 是 stdio 服务，可直接本地运行；如需局域网/公网共享，可包一层 SSE/HTTP transport（FastMCP 原生支持）：

```python
# 可选：以 SSE 模式暴露（供远程客户端连接）
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "sse":
        mcp.run(transport="streamable-http", host="0.0.0.0", port=8765)
    else:
        mcp.run()
```

部署要点（Oracle Cloud 免费 ARM / Render 免费实例均可）：

1. 安装 Python 3.12 + 本项目（`pip install -r requirements.txt`）。
2. 准备 `db/lexvault.db`（本地生成后 scp 上传，或启动时从 Turso/GitHub 拉取）。
3. 用 `systemd` 或 Render 进程管理器常驻：`python3 run_mcp.py sse`。
4. 反向代理 8765 端口（Caddy/Nginx）并配 HTTPS。
5. 客户端配置 `"url": "https://your-host/mcp"`、`"transport": "streamable-http"`。

---

## 4. 新增法域适配器

统一协议在 `lexvault/core/models.py`：

- `LegalDocument`：一部法规的元数据（doc_key 幂等键、title、doc_type、status、issuing_body、publish_date、effective_date、source_url、metadata）。
- `DocumentVersion`：版本（version_no、effective_from、is_current）。
- `DocumentSection`：条文（section_no、body、level_path）。
- `DocumentRecord`：三者打包，交给 `Store.save_record()` 一次性写入。

实现一个适配器只需三步：

```python
class EUAdapter:                    # lexvault/adapters/eu.py
    name = "eu"
    jurisdiction_code = "eu"
    def run(self, **kw) -> AdapterResult:   # 返回 DocumentRecord 列表
        ...
```

然后 `scripts/update.py` 增加一个 `--adapter eu` 分支即可（或按关键词/日期窗口各自实现）。

---

## 5. 数据链路说明（已实测）

| 环节 | 接口 | 说明 |
| --- | --- | --- |
| 搜索列表 | `POST /law-search/search/list` | 支持关键词、`gbrq` 公布日期区间过滤；返回 bbbs/title/flxz/gbrq/sxrq/sxx/zdjgName |
| 详情 | `GET /law-search/search/flfgDetails?bbbs=` | 元数据 + 条文树结构（title 仅条目标题，无正文） |
| 正文下载 | `POST /law-search/download/batch` | `[{"bbbs":..., "format":"docx"}]` → OBS 签名 URL（约 1 小时有效） |
| 拆条 | `split_sections()` | 按“第X条”切分 docx 文本；民法典实测 1260 条 |

- 详情接口的 `content` 树叶子无正文字段，正文必须走 docx 下载（与 lawgent 旧结论“公开拿不到正文”不同，**正文可公开获取**）。
- 下载链接是临时签名 URL，需在详情返回后尽快下载；适配器内部已串行处理。

---

## 6. 运维 / 常见问题

**Q: FTS 检索中文为什么有时搜不到？**
FTS5 的 trigram 分词器按 3 字滑窗索引；≥3 字的词用 `MATCH`，2 字词（如“抵押”）自动回退 `LIKE`。`Store.search_local` 已内置该逻辑。

**Q: 更新脚本重复执行会重复入库吗？**
不会。`save_record` 按 `(jurisdiction_id, doc_key)` upsert，幂等；每次运行写一条 `source_records` 审计。

**Q: 怎么回看历史更新记录？**
```sql
SELECT run_id, status, docs_fetched, docs_inserted, started_at, finished_at
FROM source_records ORDER BY started_at DESC LIMIT 20;
```

**Q: 首次全量建库怎么做？**
用关键词逐批拉取，或放宽日期窗口：`python3 scripts/update.py --keyword 法律 --days 3650 --max-docs 500`（注意 NPC 单页上限 20，脚本会自动翻页）。
