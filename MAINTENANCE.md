# lexvault 数据刷新维护手册

涉外 MCP 法律库的数据维护指南。所有拉取脚本均为**幂等**（按 doc_key upsert，
重复运行安全），内置限速，不会给对方网站造成压力。

## 数据源与刷新周期

| 法域 | 内容 | 来源 | 官方更新频率 | 建议刷新 |
| --- | --- | --- | --- | --- |
| cn | 宪法/法律/行政法规/司法解释 | flk.npc.gov.cn | 随时（新法公布） | 每月 |
| us | 美国 CFR（eCFR） | eCFR API | 每日 | 每周 |
| us_code | 美国法典 US Code | govinfo | 每年（重编） | 每季度或年度 |
| us_ofac | OFAC 制裁名单 | SLS CSV | 每周五（通常） | 每周 |
| eu | 欧盟核心法规 | EUR-Lex | 随时 | 每月 |

## 刷新命令

```bash
cd /Users/yanheng/Documents/学习/涉外法律/lexvault

# cn 全量增量（按分类，已入库自动跳过）
python3 scripts/fetch_laws.py --codes 210 --since 2010-01-01   # 行政法规
python3 scripts/fetch_laws.py --codes 320,330,340              # 司法解释
python3 scripts/fetch_major.py --keywords 宪法,刑法,民法典      # 主要法典

# us eCFR（涉外核心 65 parts）
python3 scripts/fetch_ecfr.py

# us_code（5 个涉外核心 Title，默认 2024 版）
python3 scripts/fetch_uscode.py --year 2024
python3 scripts/fetch_uscode.py --title 26   # 如需补充 Title 26 税法等

# us_ofac 制裁名单（每周快照）
python3 scripts/fetch_ofac.py

# eu EUR-Lex（7 部涉外核心法规）
python3 scripts/fetch_eurlex.py
```

## Cron 模板（可选启用）

> 注意：自动定时拉取外部网站请先确认对方无禁止条款，并保持脚本内限速。
> 默认建议只在工作日执行，避免误伤节假日对方维护窗口。

```cron
# 每周五 02:00 刷新 OFAC 制裁名单（官方通常周五更新）
0 2 * * 5 cd /Users/yanheng/Documents/学习/涉外法律/lexvault && .venv/bin/python3 scripts/fetch_ofac.py >> logs/ofac_refresh.log 2>&1

# 每周一 03:00 刷新 eCFR
0 3 * * 1 cd /Users/yanheng/Documents/学习/涉外法律/lexvault && .venv/bin/python3 scripts/fetch_ecfr.py >> logs/ecfr_refresh.log 2>&1

# 每月 1 日 04:00 刷新 cn 分类
0 4 1 * * cd /Users/yanheng/Documents/学习/涉外法律/lexvault && .venv/bin/python3 scripts/fetch_laws.py --codes 210,320,330,340 >> logs/cn_refresh.log 2>&1

# 每季度 1 日 05:00 刷新 US Code（注意 govinfo 年度版本切换）
0 5 1 1,4,7,10 * cd /Users/yanheng/Documents/学习/涉外法律/lexvault && .venv/bin/python3 scripts/fetch_uscode.py --year 2025 >> logs/uscode_refresh.log 2>&1
```

启用方式（macOS/Linux）：`crontab -e` 粘贴模板；首次建议先手动跑一遍脚本确认无报错。

## 验证刷新是否生效

```bash
# 查看库统计（文档数/条文数/法域分布）
python3 - <<'EOF'
import sys, os; sys.path.insert(0, '.')
from lexvault.core.store import Store
s = Store('db/lexvault.db'); c = s.connect()
for r in c.execute("""SELECT j.code, count(DISTINCT d.id) docs, count(s.id) secs
  FROM jurisdictions j LEFT JOIN legal_documents d ON d.jurisdiction_id=j.id
  LEFT JOIN document_sections s ON s.document_id=d.id
  GROUP BY j.code ORDER BY docs DESC""").fetchall():
    print(f"  {r['code']}: {r['docs']} 部 / {r['secs']} 条")
print("库大小: %.1f MB" % (os.path.getsize('db/lexvault.db')/1024/1024))
s.close()
EOF

# 抽查检索
python3 - <<'EOF'
import sys; sys.path.insert(0, '.')
from lexvault.core.store import Store
s = Store('db/lexvault.db'); s.connect()
for q in ["制裁", "export control", "IEEPA", "BANCO NACIONAL DE CUBA"]:
    hits = s.search_local(q, limit=1)
    print(f"  '{q}' -> {hits['total']} 命中")
s.close()
EOF
```

## 备份

SQLite 单文件，直接复制即可：

```bash
cp db/lexvault.db db/lexvault-$(date +%Y%m%d).db
```

注意：库当前约 329MB，建议定期备份 + 必要时 `VACUUM` 回收空间：
```bash
python3 -c "import sqlite3; sqlite3.connect('db/lexvault.db').execute('VACUUM')"
```

## 常见问题

- **eCFR part 404**：部分 part 号是保留号（31CFR 537/538/595、15CFR 731/733/737），
  清单已剔除；新遇到 404 说明该 part 在当年版本无正文，可从清单移除。
- **US Code 版本**：govinfo 每年发布新版本包（USCODE-{year}-titleN），换年后
  需用 `--year` 指定新年度；库内旧版本仍在 document_versions 可回溯。
- **OFAC 实体变化**：每周快照差异体现在 section 增删，upsert 自动处理；
  若有实体从名单移除，旧 section 仍留库（保留历史），如需严格镜像可加清理逻辑。
- **限速原则**：脚本已内置间隔，请勿自行调小；大规模首次拉取建议分批+监控。
