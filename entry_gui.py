"""批量解压控制台 · 双击入口（供 PyInstaller 打包为 exe 使用）

行为：
1. 探测 8765 起的端口：若已有本服务在跑（/api/meta 校验通过）则直接复用；
2. 否则在本线程启动服务（batch_extract.serve）；
3. 服务就绪后用系统默认浏览器打开页面；
4. 常驻运行，直到进程被结束（托盘无图标；关掉浏览器不影响服务，再次双击会复用）。
"""
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

import batch_extract as bx

PORT_START = 8765
PORT_RANGE = 20
READY_TIMEOUT = 15.0


def _probe(port: float) -> bool:
    """端口上是否为「本服务」：GET /api/meta 校验 schema 字段。"""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/meta", timeout=1.5) as resp:
            data = resp.read().decode("utf-8", "replace")
        return bx.SCHEMA in data and '"formats"' in data
    except Exception:
        return False


def _pick_port() -> int:
    for offset in range(PORT_RANGE):
        port = PORT_START + offset
        if _probe(port):
            return port          # 已有实例，复用
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port      # 空闲端口
    return PORT_START            # 全被占用（且都不是本服务）：退回默认端口


def main() -> int:
    port = _pick_port()
    if not _probe(port):
        threading.Thread(target=bx.serve, args=("127.0.0.1", port), daemon=True).start()
        deadline = time.time() + READY_TIMEOUT
        while time.time() < deadline and not _probe(port):
            time.sleep(0.15)
        if not _probe(port):
            return 1             # 服务未能就绪
    # 就绪后打开系统默认浏览器；失败不致命（用户可手动访问）。
    # 自动化验证可设 BX_NO_BROWSER=1 跳过。
    if os.environ.get("BX_NO_BROWSER", "").strip() not in ("1", "true", "yes"):
        try:
            webbrowser.open(f"http://127.0.0.1:{port}/", new=2)
        except Exception:
            pass
    # 常驻：serve 在 daemon 线程里，这里阻塞主线程
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
