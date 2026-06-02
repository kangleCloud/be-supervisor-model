"""本地调试入口。"""
from __future__ import annotations

import uvicorn


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=18880, reload=False)
