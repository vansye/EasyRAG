"""让 `python -m app` 可用。

README 的启动步骤写的是 `python -m app`，而一个包不带 __main__.py
是无法被 -m 执行的（报 `No module named app.__main__`）。与其把文档
改成更长的 uvicorn 命令，不如让最直觉的那条命令成立。

想要自定义 host/port/reload 时仍可直接用：
    uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
"""

from __future__ import annotations

import uvicorn

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def main() -> None:
    uvicorn.run("app.main:app", host=DEFAULT_HOST, port=DEFAULT_PORT)


if __name__ == "__main__":
    main()
