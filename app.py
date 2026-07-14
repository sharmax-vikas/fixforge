"""Convenience launcher for FixForge.

Use `uvicorn fixforge.main:app --reload` during development, or run this file
for a single-process local server.
"""

import os

import uvicorn


if __name__ == "__main__":
    uvicorn.run("fixforge.main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")), reload=False)
