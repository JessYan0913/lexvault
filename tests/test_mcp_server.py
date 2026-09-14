"""MCP 查询服务端到端验证：启动 stdio server → 调用 search_local / stats。

用法（cwd 无关，从任何目录运行均可）：
    /Users/yanheng/Documents/学习/涉外法律/lexvault/.venv/bin/python3 \
      /Users/yanheng/Documents/学习/涉外法律/lexvault/tests/test_mcp_server.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER = [
    sys.executable,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run_mcp.py"),
]


async def main() -> int:
    params = StdioServerParameters(
        command=SERVER[0],
        args=SERVER[1:],
        env={**os.environ, "LEXVAULT_DB": os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "lexvault.db")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("✅ MCP server 初始化成功")

            # 1. 列出工具
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print(f"✅ 工具列表 ({len(names)}): {', '.join(names)}")

            # 2. search_local 本地检索（3 字词 FTS）
            res = await session.call_tool("search_local", {"query": "居住权", "limit": 2, "jurisdiction": "cn"})
            txt = res.content[0].text if hasattr(res.content[0], "text") else str(res.content)
            data = json.loads(txt)
            assert data["ok"] is True, data
            assert len(data["data"]) >= 1, data
            print(f"✅ search_local('居住权') -> {len(data['data'])} 条")
            for item in data["data"][:2]:
                print(f"   · {item['doc_title']} | {item['section_no']} | {item['body'][:40]}")

            # 3. search_local 2 字词（LIKE 兜底）
            res2 = await session.call_tool("search_local", {"query": "抵押", "limit": 2, "jurisdiction": "cn"})
            data2 = json.loads(res2.content[0].text)
            assert data2["ok"] is True and len(data2["data"]) >= 1, data2
            print(f"✅ search_local('抵押') -> {len(data2['data'])} 条（LIKE 兜底）")

            # 4. stats
            res3 = await session.call_tool("stats", {})
            data3 = json.loads(res3.content[0].text)
            assert data3["ok"] is True
            print(f"✅ stats -> {data3['data']}")

            # 5. list_documents
            res4 = await session.call_tool("list_documents", {"jurisdiction": "cn", "limit": 5})
            data4 = json.loads(res4.content[0].text)
            print(f"✅ list_documents(cn) -> {len(data4['data'])} 部: "
                  f"{', '.join(d['title'] for d in data4['data'][:5])}")

    print("\n✅ MCP 查询服务端到端验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
