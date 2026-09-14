#!/usr/bin/env python3
"""lexvault MCP 启动入口（不依赖 cwd 的绝对路径入口）。

用法：
    # stdio（默认，TheThing 等客户端按需拉起）
    /path/to/lexvault/.venv/bin/python3 /path/to/lexvault/run_mcp.py

    # HTTP streamable transport（服务器部署，外部智能体经 URL 调用）
    LEXVAULT_TRANSPORT=http /path/to/lexvault/.venv/bin/python3 /path/to/lexvault/run_mcp.py
    LEXVAULT_HOST=0.0.0.0 LEXVAULT_PORT=8000 LEXVAULT_TRANSPORT=http ...

    # SSE transport
    LEXVAULT_TRANSPORT=sse ...

无论从哪个工作目录启动，都会先把 lexvault 项目根目录加入 sys.path，
再运行 MCP 服务，避免 `-m lexvault.mcp.server` 因 cwd 不对导致
ModuleNotFoundError / Connection closed。
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lexvault.mcp.server import mcp  # noqa: E402

if __name__ == "__main__":
    transport = os.environ.get("LEXVAULT_TRANSPORT", "stdio")
    if transport == "http":
        # FastMCP 内部读 self.settings.host/port 绑 uvicorn，运行时改 settings 即可
        mcp.settings.host = os.environ.get("LEXVAULT_HOST", "127.0.0.1")
        mcp.settings.port = int(os.environ.get("LEXVAULT_PORT", "8000"))
        print(f"[lexvault-mcp] HTTP streamable transport on "
              f"http://{mcp.settings.host}:{mcp.settings.port}/mcp ...")
        mcp.run(transport="streamable-http")
    elif transport == "sse":
        mcp.settings.host = os.environ.get("LEXVAULT_HOST", "127.0.0.1")
        mcp.settings.port = int(os.environ.get("LEXVAULT_PORT", "8001"))
        print(f"[lexvault-mcp] SSE transport on "
              f"http://{mcp.settings.host}:{mcp.settings.port}/sse ...")
        mcp.run(transport="sse")
    else:
        mcp.run()
