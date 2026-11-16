# -*- coding: utf-8 -*-
r"""为「批量解压控制台」生成完整分发归档包（零第三方依赖，Python 3.8+）。

用法：
    python make_package.py
    python make_package.py --output D:\out\batch-extractor.zip
    python make_package.py --docs <额外文档路径> [<更多路径> ...]

输入 / 输出约定
--------------------------------------------------------------------------
输入：
  --output   归档 zip 路径；默认在项目父目录生成 batch-extractor-<日期>.zip
  --docs     额外收进包内 docs/ 的已有文档（可重复）；不存在则跳过并记入清单
  --no-manifest  不生成 MANIFEST.txt（一般不用）

输出：一个 zip 归档，顶层目录固定为 batch-extractor/，解压后双击 start.bat 即用。
  包内包含：核心程序 / 启动器 / 前端 / 自测 / 本脚本 / 全部试用样例 /
  额外文档 / MANIFEST.txt（逐文件字节数 + SHA256 + 用途，便于核对完整性）。
  显式排除：__pycache__/、*.pyc、output/（运行时产物）、归档自身。

结果返回约定
--------------------------------------------------------------------------
控制台打印：包路径、包字节数、包 SHA256、文件数、原始总字节数、排除项说明。
退出码：0=生成成功；1=缺少必要源码或写包失败。
异常分支：单个文档缺失 -> 跳过并在 MANIFEST 标注；单个文件读取失败 ->
  计入 MANIFEST 的 "READ_FAILED" 并继续，不中断整包；输出路径不可写 -> 报错退出。
==========================================================================
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOP = "batch-extractor"

# 打包白名单：显式列举，避免把运行时产物（output/）或字节码缓存带进分发包。
REQUIRED = [
    "batch_extract.py", "launch.py", "start.bat", "selftest.py",
    "make_package.py", "web/index.html",
]
SAMPLE_GLOB = "samples/input/*"

PURPOSE = {
    "batch_extract.py": "核心库 + CLI + 本地 HTTP 服务（零第三方依赖）",
    "launch.py": "一键启动器：端口复用 / 后台服务 / 外部浏览器 / 安全停止",
    "start.bat": "双击入口（GBK + chcp 936），定位 Python 并调用 launch.py",
    "selftest.py": "确定性自测：135 项断言，退出码 0 即全部通过",
    "make_package.py": "本打包脚本，可随时重新生成完整归档",
    "web/index.html": "前端操作页面（单文件 HTML/CSS/JS）",
    "MANIFEST.txt": "本清单：逐文件字节数 / SHA256 / 用途",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def purpose_of(rel: str) -> str:
    if rel in PURPOSE:
        return PURPOSE[rel]
    if rel.startswith("samples/input/"):
        name = Path(rel).name
        if name.startswith("7z."):
            return "试用样例：真实 7z.001~7z.004 分卷（含中文路径文件）"
        if name == "demo_locked.7z":
            return "试用样例：加密包，密码 demo123（含中文路径文件）"
        if name == "demo_broken.zip":
            return "试用样例：损坏包，用于验证 corrupt 分类"
        if name == "demo_ok.zip":
            return "试用样例：正常包，用于验证目录层级与中文名"
        return "试用样例"
    if rel.startswith("docs/"):
        return "项目文档副本（架构与工程知识 / 工作日志）"
    return "分发包附随文件"


def collect_files():
    """返回 [(包内相对路径, 源文件绝对路径)]，按路径排序，确定性。"""
    paths = []
    for rel in REQUIRED:
        p = HERE / rel
        if not p.is_file():
            raise FileNotFoundError("缺少必要源码：%s" % p)
        paths.append((rel, p))
    for p in sorted(HERE.glob(SAMPLE_GLOB)):
        if p.is_file():
            paths.append((p.relative_to(HERE).as_posix(), p))
    return sorted(set(paths))


def build_manifest(entries, skipped_docs, failed_reads):
    lines = [
        "批量解压控制台 · 完整分发包清单",
        "生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "顶层目录：%s/" % TOP,
        "文件数：%d（含本清单）" % (len(entries) + 1),
        "内容文件总字节（不含本清单）：%d" % sum(len(b) for _, b in entries),
        "",
        "运行方式：解压后双击 start.bat → 起服务 + 系统默认外部浏览器打开页面。",
        "依赖：Python 3.8+（必需）；7-Zip（可选，用于 .7z/.rar/.001 分卷/AES 加密 ZIP）。",
        "其它命令：start.bat --status / --stop / --port 8877 / --no-browser；python selftest.py。",
        "试用样例：samples/input/ 内含正常包、损坏包、加密包（密码 demo123）、4 卷 7z.001~004。",
        "",
        "显式排除：__pycache__/、*.pyc、output/（运行时解压产物）、归档自身。",
        "校验：下表 SHA256 为该「文件原始内容」的哈希，可用任意哈希工具核对。",
        "",
        "清单格式：包内相对路径 | 字节数 | SHA256（文件原始内容） | 用途",
        "",
    ]
    for rel, data in entries:
        lines.append("%s | %d | %s | %s" %
                     (rel, len(data), sha256(data), purpose_of(rel)))
    lines.append("MANIFEST.txt | (本文件) | - | %s" % PURPOSE["MANIFEST.txt"])
    if skipped_docs:
        lines += ["", "未收录的额外文档（源不存在，已跳过）："]
        lines += ["  " + s for s in skipped_docs]
    if failed_reads:
        lines += ["", "读取失败、未收录的文件："]
        lines += ["  " + s for s in failed_reads]
    lines += [
        "",
        "重新打包：在本目录运行 python make_package.py（可加 --output / --docs）。",
    ]
    return "\r\n".join(lines) + "\r\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成批量解压控制台完整分发 zip")
    ap.add_argument("--output", default="", help="归档 zip 路径；默认项目父目录 + 日期")
    ap.add_argument("--docs", nargs="*", default=[],
                    help="额外收进 docs/ 的已有文档；可写 别名=路径 自定义包内文件名")
    ap.add_argument("--no-manifest", action="store_true", help="不生成 MANIFEST.txt")
    args = ap.parse_args()

    out = Path(args.output).expanduser().absolute() if args.output else \
        HERE.parent / ("batch-extractor-%s.zip" % date.today().strftime("%Y-%m-%d"))
    try:
        files = collect_files()
    except FileNotFoundError as exc:
        print("[错误] %s" % exc)
        return 1

    entries = []          # [(包内相对路径, 原始字节)]
    failed_reads = []
    for rel, src in files:
        try:
            entries.append((rel, src.read_bytes()))
        except OSError as exc:
            failed_reads.append("%s（%s: %s）" % (rel, type(exc).__name__, exc))

    skipped_docs = []
    for item in args.docs:
        alias, raw = item.split("=", 1) if "=" in item else ("", item)
        p = Path(raw).expanduser()
        arc = "docs/" + (alias or p.name)
        if not p.is_file():
            skipped_docs.append("%s <- %s" % (arc, p))
            continue
        try:
            entries.append((arc, p.read_bytes()))
        except OSError as exc:
            failed_reads.append("%s（%s: %s）" % (arc, type(exc).__name__, exc))
    entries.sort()

    content_bytes = sum(len(b) for _, b in entries)
    manifest = None if args.no_manifest else \
        build_manifest(entries, skipped_docs, failed_reads)
    if manifest is not None:
        entries.append(("MANIFEST.txt", manifest.encode("utf-8")))

    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED,
                             compresslevel=9) as zf:
            for rel, data in entries:
                info = zipfile.ZipInfo("%s/%s" % (TOP, rel),
                                       date_time=datetime.now().timetuple()[:6])
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, data)
            zf.comment = ("批量解压控制台完整分发包 | 解压后双击 start.bat | "
                          "生成于 %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S")).encode("utf-8")
    except OSError as exc:
        print("[错误] 无法写入归档 %s：%s" % (out, exc))
        return 1

    print("[完成] 归档：%s" % out)
    print("        包大小：%d 字节 | 包 SHA256：%s" % (out.stat().st_size, sha256(out.read_bytes())))
    print("        文件数：%d（含清单） | 内容文件总字节：%d（不含清单）" %
          (len(entries), content_bytes))
    print("        排除：__pycache__/、*.pyc、output/（运行时产物）、归档自身")
    if skipped_docs:
        print("        跳过的额外文档：%s" % ", ".join(skipped_docs))
    if failed_reads:
        print("        读取失败项：%s" % ", ".join(failed_reads))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
