# -*- coding: utf-8 -*-
r"""一键启动器：启动/复用本地服务 -> 等待就绪 -> 在「外部浏览器」打开页面。

由 start.bat 调用；也可直接运行：
    python launch.py                     # 启动或复用服务 + 打开系统默认浏览器
    python launch.py --no-browser        # 只起服务，不开浏览器（自动化/排障用）
    python launch.py --status            # 查看端口状态
    python launch.py --stop              # 安全停止本项目的服务
    python launch.py --port 8877         # 换端口
    python launch.py --browser "C:\Program Files\Google\Chrome\Application\chrome.exe"

================================================================================
一、输入 / 输出约定
================================================================================
输入：命令行参数 + 环境变量
  --port        服务端口，默认 8765（start.bat 顶部 PORT 可编辑）
  --browser     指定外部浏览器 exe 绝对路径；留空用系统默认浏览器
  --no-browser  只起服务不打开浏览器
  --status      只查询状态，不改进程
  --stop        停止本项目的服务
  --wait        等待服务就绪的秒数，默认 15
  BX_BROWSER    环境变量形式的浏览器路径，等价于 --browser（start.bat 可编辑）

输出：控制台中文提示；退出码 0=成功 / 1=启动或停止失败 / 2=端口被其它程序占用。
服务进程的 stdout/stderr 重定向到 %TEMP%\batch-extractor-server.log，便于排障。

================================================================================
二、页面 <-> 程序的参数传递与结果返回约定
================================================================================
  启动：subprocess.Popen([python, batch_extract.py, --serve, --host, 127.0.0.1,
                         --port, <port>])，DETACHED_PROCESS 脱离 bat 控制台。
  就绪判定：GET http://127.0.0.1:<port>/api/meta -> JSON 中
            schema == batch-extract-summary/v1 且 formats 为数组，才算「我们的服务」。
            只靠端口连通是不够的：别的程序占用端口时必须明确拒绝，不能误连或误杀。
  打开：服务就绪后才调用 os.startfile(url) / webbrowser.open(url)，
        走系统默认「外部浏览器」（ShellExecute），不是 IDE 内置预览面板。
        若先打开浏览器再起服务，浏览器会停在连接失败页且不会自动重试。
  停止：netstat -ano 解析 LISTENING 行的 PID -> 再次校验 /api/meta 属于本服务
        -> taskkill /PID <pid> /F。校验失败时绝不终止任何进程。

================================================================================
三、异常处理分支
================================================================================
  A 端口未监听                 -> 启动新服务；进程提前退出则报日志路径
  B 端口被本服务占用           -> 直接复用，不重复启动；随后仍打开浏览器
  C 端口被其它程序占用         -> 明确报错，退出码 2，不启动也不终止
  D 等待就绪超时               -> 报错 + 日志路径，退出码 1
  E 指定浏览器不存在/启动失败  -> 退回系统默认浏览器；仍失败则打印 URL 供手动访问
  F 未解析到 PID / taskkill 失败 -> 明确报错，不做任何猜测性终止
================================================================================
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import webbrowser
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER_SCRIPT = HERE / "batch_extract.py"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765
LOG_FILE = Path(tempfile.gettempdir()) / "batch-extractor-server.log"

try:
    # 与服务端共用同一个 schema 常量，避免两边漂移
    sys.path.insert(0, str(HERE))
    import batch_extract as _bx
    SCHEMA = _bx.SCHEMA
except Exception:  # 服务端文件缺失时仍可查询端口状态
    SCHEMA = "batch-extract-summary/v1"


def url_of(port: int) -> str:
    return "http://%s:%d/" % (HOST, port)


def meta_url(port: int) -> str:
    return "http://%s:%d/api/meta" % (HOST, port)


def probe(port: int, timeout: float = 1.0):
    """返回 (端口是否有进程监听, 是否是本项目的服务)。纯网络探测，不改状态。"""
    try:
        with urllib.request.urlopen(meta_url(port), timeout=timeout) as resp:
            raw = resp.read(8192).decode("utf-8", "replace")
        try:
            data = json.loads(raw)
            ours = data.get("schema") == SCHEMA and isinstance(data.get("formats"), list)
        except (ValueError, AttributeError):
            ours = False
        return True, ours
    except Exception:
        sock = socket.socket()
        sock.settimeout(timeout)
        try:
            return sock.connect_ex((HOST, port)) == 0, False
        finally:
            sock.close()


def listening_pids(port: int):
    """从 netstat -ano 解析处于 LISTENING 且本地端口匹配的 PID 列表（Windows）。"""
    if os.name != "nt":
        return []
    try:
        out = subprocess.check_output(["netstat", "-ano", "-p", "TCP"],
                                      text=True, errors="replace", timeout=8)
    except Exception:
        return []
    pids = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[-2].upper() != "LISTENING":
            continue
        if not re.search(r":%d$" % port, parts[1]):
            continue
        pid = parts[-1]
        if pid.isdigit() and pid != "0" and pid not in pids:
            pids.append(pid)
    return pids


def print_status(port: int) -> int:
    live, ours = probe(port)
    if ours:
        print("[运行中] 本项目服务已就绪：%s（PID %s）" %
              (url_of(port), ", ".join(listening_pids(port)) or "未知"))
    elif live:
        print("[占用] 端口 %d 上有其它程序在监听，/api/meta 不是本项目服务。" % port)
    else:
        print("[未运行] 端口 %d 没有服务在监听。" % port)
    return 0


def start_service(port: int):
    """启动脱离控制台的服务进程，返回 Popen 对象。"""
    flags = 0
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：
        # 服务不附加到 bat 的控制台，关闭黑色窗口不会影响已打开的页面。
        flags = 0x00000008 | 0x00000200
    log_handle = open(LOG_FILE, "ab", buffering=0)
    cmd = [sys.executable, str(SERVER_SCRIPT), "--serve",
           "--host", HOST, "--port", str(port)]
    proc = subprocess.Popen(cmd, cwd=str(HERE), stdin=subprocess.DEVNULL,
                            stdout=log_handle, stderr=subprocess.STDOUT,
                            creationflags=flags, close_fds=True)
    print("[启动] 服务进程 PID=%d，日志：%s" % (proc.pid, LOG_FILE))
    return proc


def wait_ready(port: int, proc, seconds: float) -> bool:
    """轮询 /api/meta，直到本项目服务就绪或服务进程提前退出。"""
    deadline = time.time() + max(1.0, seconds)
    while time.time() < deadline:
        live, ours = probe(port, timeout=0.8)
        if ours:
            return True
        if proc is not None and proc.poll() is not None:
            print("[错误] 服务进程提前退出（退出码 %s），详见日志：%s" %
                  (proc.poll(), LOG_FILE))
            return False
        time.sleep(0.2)
    print("[错误] 等待 %.0f 秒服务仍未就绪，详见日志：%s" % (seconds, LOG_FILE))
    return False


def open_external_browser(url: str, browser: str = "") -> bool:
    """在系统默认「外部浏览器」打开；可指定 exe。绝不打开 IDE 内置预览。"""
    chosen = (browser or os.environ.get("BX_BROWSER", "") or "").strip()
    if chosen:
        exe = Path(chosen).expanduser()
        if exe.is_file():
            try:
                subprocess.Popen([str(exe), url], close_fds=True)
                print("[浏览器] 已用指定浏览器打开：%s" % exe)
                return True
            except OSError as exc:
                print("[警告] 指定浏览器启动失败：%s，改用系统默认浏览器" % exc)
        else:
            print("[警告] 指定浏览器不存在：%s，改用系统默认浏览器" % exe)
    startfile = getattr(os, "startfile", None)
    if startfile is not None:
        try:
            startfile(url)  # Windows ShellExecute -> 系统默认浏览器
            print("[浏览器] 已在系统默认浏览器打开：%s" % url)
            return True
        except OSError as exc:
            print("[警告] 默认浏览器打开失败：%s，尝试备用方式" % exc)
    try:
        if webbrowser.open(url, new=2):
            print("[浏览器] 已在外部浏览器打开：%s" % url)
            return True
    except Exception as exc:
        print("[警告] 备用方式打开失败：%s" % exc)
    print("[提示] 未能自动打开浏览器，请手动访问：%s" % url)
    return False


def launch(port: int, browser: str, no_browser: bool, wait: float) -> int:
    live, ours = probe(port)
    proc = None
    if live and not ours:
        print("[错误] 端口 %d 已被其它程序占用（/api/meta 不属于本项目）。" % port)
        print("       换端口：start.bat --port %d" % (port + 1))
        return 2
    if ours:
        print("[复用] 服务已在运行：%s（不重复启动）" % url_of(port))
    else:
        if not SERVER_SCRIPT.is_file():
            print("[错误] 找不到服务脚本：%s" % SERVER_SCRIPT)
            return 1
        proc = start_service(port)
        if not wait_ready(port, proc, wait):
            return 1
        print("[就绪] %s" % url_of(port))

    if not no_browser:
        open_external_browser(url_of(port), browser)
    else:
        print("[跳过] --no-browser：本次不打开浏览器")
    print("[完成] 页面地址：%s" % url_of(port))
    print("[提示] 服务在后台常驻；start.bat --status 查状态，start.bat --stop 停止。")
    return 0


def stop_service(port: int) -> int:
    live, ours = probe(port)
    if not live:
        print("[状态] 端口 %d 没有服务在监听，无需停止。" % port)
        return 0
    if not ours:
        print("[拒绝] 端口 %d 由其它程序占用，/api/meta 校验未通过，未终止任何进程。" % port)
        return 2
    pids = listening_pids(port)
    if not pids:
        print("[警告] 未解析到 LISTENING 进程 PID，未做任何终止。地址：%s" % url_of(port))
        return 2
    ok = True
    for pid in pids:
        result = subprocess.run(["taskkill", "/PID", pid, "/F"],
                                capture_output=True, text=True, errors="replace")
        if result.returncode == 0:
            print("[停止] 已终止服务进程 PID=%s" % pid)
        else:
            ok = False
            print("[失败] 无法终止 PID=%s：%s" %
                  (pid, (result.stdout or result.stderr or "").strip()[:200]))
    if ok:
        for _ in range(20):
            if not probe(port, timeout=0.3)[0]:
                break
            time.sleep(0.1)
        print("[完成] 服务已停止，端口 %d 已释放。" % port)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="批量解压控制台一键启动器：起服务 + 外部浏览器打开页面")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="服务端口，默认 8765")
    parser.add_argument("--browser", default="", help="指定外部浏览器 exe 绝对路径，留空用系统默认")
    parser.add_argument("--no-browser", action="store_true", help="只起服务，不打开浏览器")
    parser.add_argument("--status", action="store_true", help="只查询服务状态")
    parser.add_argument("--stop", action="store_true", help="停止本项目的服务")
    parser.add_argument("--wait", type=float, default=15.0, help="等待服务就绪的秒数，默认 15")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        print("[错误] 端口必须在 1~65535 之间：%d" % args.port)
        return 1
    if args.stop:
        return stop_service(args.port)
    if args.status:
        return print_status(args.port)
    return launch(args.port, args.browser, args.no_browser, args.wait)


if __name__ == "__main__":
    raise SystemExit(main())
