# -*- coding: utf-8 -*-
r"""
批量解压程序（核心库 + 命令行 + 本地 HTTP 服务），零第三方依赖，Python 3.8+。

================================================================================
一、输入 / 输出约定
================================================================================
输入（两种互斥方式，mode 决定）：
  1) mode="dir"   : inputDir = 一个目录，递归扫描其中所有「可识别的压缩包」；
                    非压缩包文件忽略；outputDir 若位于 inputDir 内会被自动排除，
                    避免把上一次的输出当成输入反复处理。
  2) mode="files" : archives = 压缩包路径列表（前端按行输入，一行一个）；
                    若某项是目录，则展开该目录下的压缩包。
  outputDir       : 输出根目录，不存在则自动创建。
  overwrite       : 默认 False。目标文件已存在 -> 跳过并记 skipped/reason=conflict；
                    True -> 覆盖并计入 success。
  useArchiveSubdir: 默认 True。每个包解压到 outputDir/<压缩包名（不含扩展名）>/，
                    包内相对路径与目录层级原样保留；False -> 直接铺到 outputDir/。
  externalTool    : 可选的 7z / 7zz 可执行文件绝对路径；留空则自动探测
                    （PATH → Windows 卸载注册表 InstallLocation → 常见安装位置）。

  ★ 加密压缩包（password / passwords / passwordFile，三者可叠加，去重保序）：
    - password      : 单个密码（页面「解压密码」输入框 / CLI --password）。
    - passwords     : 候选密码数组（页面「候选密码」一行一个 / CLI 多次 --password）。
                      批次里不同包用不同密码时，把密码都列上即可：程序对每个包
                      先试「无密码」，再按候选顺序逐个试，第一个成功即停止。
    - passwordFile  : 密码本文件路径，UTF-8，一行一个密码，空行忽略
                      （CLI --password-file；也支持环境变量 BX_PASSWORD_FILE）。
    - 环境变量 BX_PASSWORD 亦可提供单个密码，避免密码出现在命令行进程列表里。
    - 密码只存在于本次任务内存：不写入 messages / items / groups / 汇总 / 日志 /
      HTTP 响应，不写本地存储；汇总 auth 字段只给数量不给出明文。
    - 支持：加密 .7z/.rar、加密文件名（-mhe=on）、加密 .001 分卷、
            ZipCrypto 加密 ZIP（内置 zipfile）、AES 加密 ZIP（自动转 7-Zip）。
    - 缺失密码 -> failed(password_required)；所有候选都错 -> failed(wrong_password)；
      两者都是包级失败，明确给出提示，且不影响同批次其它包继续处理。
    - 注意：极少数压缩工具写入的非 ASCII 密码存在编码差异；程序对内置 ZIP
      额外尝试 GBK 编码，7-Zip 路径使用系统命令行编码。

  ★ 分卷压缩包（如 7z.001 / 7z.002、xxx.7z.001、xxx.part1.rar）：
    - 自动识别：文件名以 3~4 位数字编号结尾（.001 / .0001），或 .partN.rar。
    - 自动归组：同目录 + 同组名 + 同编号宽度 = 一组，视为「一个压缩包」。
      目录模式和多路径模式都生效；只填首卷、只填中间卷、把 5 个卷全填上，
      结果都相同——程序会扫描同目录补全该组全部分卷并去重，绝不重复解压。
    - 只把首卷（.001 / .part1.rar）交给 7-Zip，7-Zip 自动串读后续卷；
      因此同一组的分卷必须放在同一目录下，且不要改动编号。
    - 完整性预检：编号必须从 1 连续到最大编号；缺首卷或中间断号 ->
      failed(reason=missing_volume)，列出缺失编号并跳过该组，继续处理其余包。
    - 输出子目录取「组名」（7z.001 -> 7z；xxx.7z.001 -> xxx；
      xxx.part1.rar -> xxx），不是首卷文件名。

输出（文件系统）：
  outputDir/<包名>/<包内原始相对路径>      —— 完整保留层级，不做扁平化。
  仅 .gz/.bz2/.xz/.lzma 这类「单文件流压缩」没有内部层级，
  输出为 outputDir/<包名>/<原文件名去掉压缩后缀>。
  空目录也会保留。包内绝对路径、.. 逃逸、盘符、符号链接一律拒绝（reason=unsafe_path）。

================================================================================
二、页面 <-> 程序 的参数传递与结果返回约定（HTTP，全部 JSON / UTF-8）
================================================================================
  GET  /                      -> 前端页面 web/index.html
  GET  /api/meta              -> {"formats":[...],"externalTool":路径|null,"defaults":{...}}
  POST /api/jobs              -> 请求体 = 上面的输入字段（JSON）；
                                 响应 202 {"jobId":"<12位十六进制>"}；
                                 仅 JSON 解析失败 / 字段类型错误时返回 400 {"error": "..."}。
                                 密码字段：password(str) / passwords(str[]) /
                                 passwordFile(str)，均由前端表单或 CLI 传入；
                                 后台线程用完即清空，接口从不回显密码。
                                 业务层的路径不存在、格式不支持、密码错误等问题
                                 不返回 400，一律进入汇总的 failed 分组，
                                 保证「不中断整体任务」。  GET  /api/jobs/<jobId>      -> {"status":"running|done|error",
                                  "messages":[{"level":"info|success|warn|error","text":str,"at":ISO8601}],
                                  "summary": <汇总对象>|null,
                                  "error": str|null}
                                 任务后台线程执行，前端轮询本接口即可实时展示提示信息；
                                 status=done 时 summary 必为最终汇总对象。
  CLI：stdout 恒为「同一份汇总 JSON」（可直接管道解析），提示信息走 stderr；
       --pretty 缩进输出；退出码恒为 0（业务失败体现在 counts/groups，不抛异常）。

================================================================================
三、汇总字段格式（schema = "batch-extract-summary/v1"，CLI 与页面完全同构）
================================================================================
  {
    "schema": "batch-extract-summary/v1",
    "counts": {"success": <int>, "failed": <int>, "skipped": <int>},
        // 条目级口径：success=成功落盘的文件数；skipped=因同名被跳过的文件数；
        // failed=失败的文件/条目数 + 包级失败（每个整体失败的包记 1 条）。
    "archiveCounts": {"ok": <int>, "partial": <int>, "failed": <int>},
        // 包级口径：ok=无失败；partial=有成功也有失败；failed=零成功且有失败。
    "groups": {
        "failed":  [{"reason": <原因码>, "label": <中文说明>, "count": <int>,
                     "items": [{"archive": <压缩包路径>, "path": <冲突/出错路径>,
                                "detail": <具体错误文本>}]}],
        "skipped": [ ...同结构... ]
    },
        // 按 reason 归类；groups.failed + groups.skipped 覆盖全部非成功项。
    "items": [ {"status":"success|failed|skipped", "archive":str, "path":str,
                "reason":str, "detail":str} ],
        // 扁平明细，便于程序二次处理；success 项 reason 为 ""。
    "messages": [ ...同 GET /api/jobs 的 messages... ],
    "volumeSets": [ {"name": <组名>, "first": <首卷绝对路径>,
                     "last": <末卷绝对路径>, "count": <卷数>,
                     "missing": [<缺失编号 int>]} ],
        // 新增可选字段：本次识别到的全部分卷组（含因缺卷而失败的组）。
        // 一组只算 1 个压缩包，计入 archiveCounts；其余字段结构完全不变。
    "auth": {"provided": <bool>, "candidates": <int>, "attempted": <int>,
             "solved": <int>},
        // 新增可选字段（密码摘要，绝不含明文）：provided=是否提供过密码，
        // candidates=去重后的候选密码个数，attempted=触发密码尝试的包数，
        // solved=靠某个候选密码成功解压的包数。
    "outputDir": str, "startedAt": ISO8601, "finishedAt": ISO8601
  }
  原因码 reason 取值（REASONS，见下）：
    corrupt        包/条目数据损坏、CRC 校验失败、截断
    encrypted      压缩包或条目已加密（旧通用码，保留兼容）
    password_required 包已加密但未提供密码，整包跳过并提示填写密码（新增）
    wrong_password 已提供的候选密码全部不正确，整包跳过并提示核对（新增）
    missing_volume 分卷编号不连续：缺首卷 .001 或中间断号（分卷专用）
    unsupported    格式不支持，或 .7z/.rar/.001 缺少 7-Zip 外部工具
    unsafe_path    包内路径越界（绝对路径 / .. / 盘符 / 符号链接 / 非法字符）
    conflict       目标文件已存在且 overwrite=false（唯一进入 skipped 的原因）
    io_error       读写失败（权限、磁盘、路径过长、目标同名目录）
    invalid_input  输入配置或输入路径有问题
    external_failed 外部工具执行失败但未匹配到更具体的原因
    empty          压缩包内没有任何可用文件条目（提示级，不计入 failed）

================================================================================
四、异常处理分支（每一层都独立捕获，绝不中断其余压缩包 / 其余文件）
================================================================================
  A. 配置/收集层：路径不存在、outputDir 是文件、目录不可读、密码本不可读
     -> failed(invalid_input)；密码字段类型错误在 HTTP 层返回 400。
  B. 打开包层：BadZipFile / tarfile.ReadError / EOFError / OSError -> failed(corrupt)
  C. 逐条目解到暂存区：
       - zip 加密条目无密码 -> failed(password_required)；密码全错 /
         Bad CRC-32（密码碰巧通过校验字节）-> failed(wrong_password)
       - AES 加密 ZIP（zipfile 不支持）-> 整包转 7-Zip；无 7-Zip -> unsupported
       - BadZipFile("Bad CRC-32") / zlib.error / EOFError -> failed(corrupt)
       - _safe_relpath 抛 UnsafePath                     -> failed(unsafe_path)
       - 其它 OSError                                    -> failed(io_error)
       上述均为「条目级」失败，同包其余条目继续解压。
  D. 外部工具层（7z/7zz）：每个包先试「无密码」，再按候选密码顺序重试；
     每次尝试前清空 staging（错密码也会写出半成品，绝不能落地）。
     按退出码 + 输出文本分类：password 类（Wrong password / Data Error in
     encrypted file / Cannot open encrypted archive / Break signaled）继续下一个
     候选；全失败 -> password_required（未提供）或 wrong_password（已提供）；
     missing_volume / corrupt / external_failed / unsupported 立即结束该包。
     成功时仅记录「用了第几个候选密码」，绝不记录明文。
  E. 搬运层（staging -> outputDir）：目标已存在 -> skipped(conflict)；
     写入异常 -> failed(io_error)，继续下一个文件。
  F. 顶层兜底：任何未预期异常都转成 failed(external_failed) 并继续下一个包。
  G. 分卷收集层：扫描同目录补全卷 -> 编号 1..max 连续性检查 ->
     missing_volume 直接记 1 条包级失败并跳过该组；其余组继续处理。
     7-Zip 对分卷报「缺卷 / unexpected end」时归 corrupt / missing_volume。

================================================================================
五、运行方式
================================================================================
  python batch_extract.py --input-dir  <目录>   --output <目录> [--pretty]
  python batch_extract.py --archives a.zip b.7z --output <目录> --password SECRET
  python batch_extract.py --input-dir D:\packs --output D:\out   # 目录里有 7z.001~7z.005，自动归为一组
  python batch_extract.py --archives D:\packs\7z.001 --output D:\out  # 只填首卷即可
  python batch_extract.py --input-dir D:\packs --output D:\out \
      --password PWD_A --password PWD_B          # 批次内不同包用不同密码
  python batch_extract.py --input-dir D:\packs --output D:\out --password-file D:\pwd.txt
  python batch_extract.py --serve [--port 8765]      # 启动页面：http://127.0.0.1:8765
  python batch_extract.py --help
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

SCHEMA = "batch-extract-summary/v1"

# 原因码 -> 中文说明。前端直接展示 label，程序侧只比较 reason。
REASONS: Dict[str, str] = {
    "corrupt": "压缩包或条目数据损坏 / CRC 校验失败 / 文件截断",
    "encrypted": "压缩包或条目已加密（通用兜底原因码，保留兼容）",
    "password_required": "压缩包/条目已加密但未提供密码，已跳过；请填写解压密码后重试",
    "wrong_password": "解压密码不正确（已尝试全部候选密码），本包已跳过；请核对密码后重试",
    "missing_volume": "分卷编号不连续：缺少首卷 .001 或中间分卷，已跳过整组",
    "unsupported": "格式不支持，或缺少 7-Zip 外部工具（.7z / .rar / .001 分卷）",
    "unsafe_path": "包内路径越界（绝对路径 / .. / 盘符 / 符号链接 / 非法字符）",
    "conflict": "目标文件已存在，默认不覆盖，已跳过",
    "io_error": "读写失败（权限 / 磁盘 / 路径过长 / 目标同名目录）",
    "invalid_input": "输入配置或输入路径有问题",
    "external_failed": "外部解压工具执行失败",
    "empty": "压缩包内没有可用文件条目",
}

# 扩展名 -> 处理器族。多段后缀（.tar.gz）优先匹配。
EXT_MAP: Dict[str, str] = {
    ".zip": "zip", ".jar": "zip", ".whl": "zip", ".apk": "zip", ".zipx": "zip",
    ".tar": "tar", ".tgz": "tar", ".tbz2": "tar", ".txz": "tar",
    ".gz": "single", ".bz2": "single", ".xz": "single", ".lzma": "single",
    ".7z": "external", ".rar": "external",
}
TAR_EXT = (".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".txz", ".tar")
SINGLE_EXT = (".gz", ".bz2", ".xz", ".lzma")
EXTERNAL_EXT = (".7z", ".rar")
# 分卷命名：7z.001 / xxx.7z.001 / xxx.0001（数字卷），xxx.part1.rar（RAR 卷）。
# 组名用「去编号后的前缀」，编号用于连续性检查；同目录 + 同组名 + 同命名风格 = 一组。
NUM_VOLUME_RE = re.compile(r"^(?P<stem>.+)\.(?P<num>\d{3,4})$", re.IGNORECASE)
PART_VOLUME_RE = re.compile(r"^(?P<stem>.+)\.part(?P<num>\d+)\.rar$", re.IGNORECASE)
WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4",
                "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3",
                "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
MAX_MESSAGES = 5000


class UnsafePath(Exception):
    """包内路径不安全，拒绝落地（对应 reason=unsafe_path）。"""


class ArchiveError(Exception):
    """带原因码的包级/条目级异常。"""

    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _s(path: Any) -> str:
    """统一路径字符串：绝对路径 + 原生分隔符（Windows 为反斜杠）。"""
    try:
        return str(Path(path).absolute())
    except Exception:
        return str(path)


# 候选密码上限：防止误传超大列表导致每个包重复调用外部工具。
MAX_PASSWORDS = 200


# ---------------------------------------------------------------------------
# 密码上下文：收集 / 去重 / 脱敏 / 计数
# 安全约定：明文只存在于本对象的内存里，绝不进入 messages / items / groups /
#           summary / HTTP 响应 / 命令行回显；任务结束由 HTTP 层清空。
# ---------------------------------------------------------------------------
class PasswordContext:
    """本次任务的候选密码集合与统计。

    收集顺序（去重保序）：cfg.password -> cfg.passwords -> cfg.passwordFile ->
    环境变量 BX_PASSWORD -> BX_PASSWORD_FILE。密码本为 UTF-8、一行一个密码。
    """

    def __init__(self, cfg: Dict[str, Any]):
        raw: List[str] = []
        self.errors: List[Dict[str, str]] = []

        def push(value: Any) -> None:
            if value is not None and str(value) != "":
                raw.append(str(value))

        push(cfg.get("password", ""))
        for item in cfg.get("passwords", []) or []:
            push(item)
        self._read_file(str(cfg.get("passwordFile", "") or ""), raw)
        push(os.environ.get("BX_PASSWORD", ""))
        self._read_file(os.environ.get("BX_PASSWORD_FILE", ""), raw)

        seen = set()
        self.passwords: List[str] = []
        for p in raw:
            if p not in seen:
                seen.add(p)
                self.passwords.append(p)
        if len(self.passwords) > MAX_PASSWORDS:
            self.passwords = self.passwords[:MAX_PASSWORDS]
        self.attempted = 0  # 触发过密码尝试的包数
        self.solved = 0     # 靠某个候选密码成功解压的包数

    def _read_file(self, path: str, bucket: List[str]) -> None:
        if not path:
            return
        p = Path(path).expanduser()
        try:
            for line in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                value = line.strip()
                if value:
                    bucket.append(value)
        except OSError as exc:
            # 密码本读不到也要继续处理其余压缩包，只是明确记一条输入错误
            self.errors.append({"reason": "invalid_input", "path": _s(p),
                                "detail": "密码本文件无法读取：%s: %s" % (type(exc).__name__, exc)})

    @property
    def provided(self) -> bool:
        return bool(self.passwords)

    def scrub(self, text: str) -> str:
        """防御性脱敏：清洗外部工具输出里可能存在的密码回显。

        本程序从不把命令行或密码写进任何输出，7-Zip 也不回显 -p 参数；这里只
        针对 "-p<密码>" / "password=<密码>" / "密码:<密码>" 这类明确形态兜底，
        刻意不做全局字符串替换——否则密码恰好是 data / src 等常见词时，
        会把 7-Zip 报错里的文件名也打碎，反而让失败明细不可读。
        """
        out = text or ""
        for p in self.passwords:
            if not p:
                continue
            pattern = r"(?i)(-p|password|密码)(\s*[:=]?\s*)" + re.escape(p) + r"\b"
            out = re.sub(pattern, lambda m: m.group(1) + m.group(2) + "***", out)
        return out

    def auth(self) -> Dict[str, Any]:
        """汇总里的 auth 字段：只给数量，没有任何明文。"""
        return {"provided": self.provided, "candidates": len(self.passwords),
                "attempted": self.attempted, "solved": self.solved}


def _pwd_bytes(password: str) -> List[bytes]:
    """内置 zipfile 的密码字节变体：UTF-8 优先，再试 GBK（部分 Windows 工具）。"""
    out: List[bytes] = []
    for enc in ("utf-8", "gbk"):
        try:
            b = password.encode(enc)
        except UnicodeEncodeError:
            continue
        if b not in out:
            out.append(b)
    return out


def _clear_staging(staging: Path) -> None:
    """清空暂存目录。7-Zip 密码错误时也会写出 0 字节/半截文件，重试前必须丢弃。

    仅用于 tempfile.TemporaryDirectory 创建的隔离目录，不会碰到用户输出目录。
    """
    for child in staging.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(str(child), ignore_errors=True)
            else:
                child.unlink()
        except OSError:
            pass


def _classify_7z(returncode: int, out: str) -> Optional[str]:
    """把 7-Zip 的一次失败归类；密码类返回 "password" 表示可换下一个候选重试。"""
    low = (out or "").lower()
    # 密码类必须优先于 "data error" / "crc"：加密包的错误信息常同时含两者。
    # 实测 7-Zip 26.02 的四种措辞：
    #   Wrong password / Data Error in encrypted file. Wrong password?
    #   CRC Failed in encrypted file. Wrong password?
    #   Cannot open encrypted archive. Wrong password?
    if any(k in low for k in ("wrong password", "password is incorrect",
                              "密码错误", "错误的密码")):
        return "password"
    if "encrypted" in low and any(k in low for k in ("data error", "crc", "cannot open",
                                                     "can not open", "wrong password")):
        return "password"
    if any(k in low for k in ("cannot open encrypted", "can not open encrypted",
                              "enter password", "需要密码", "加密档案")):
        return "password"
    # 未提供密码时 7-Zip 想交互式询问密码；stdin 已关闭 -> "Break signaled" / rc=255
    if "break signaled" in low or (returncode in (255, -1) and "password" in low):
        return "password"
    if any(k in low for k in ("next volume", "cannot find volume", "can not find volume",
                              "missing volume", "找不到卷", "下一卷")):
        return "missing_volume"
    if any(k in low for k in ("unexpected end", "data error", "crc", "not archive",
                              "as archive", "headers error", "is not archive")) or returncode == 2:
        return "corrupt"
    return "external_failed"


def _zip_encryption_info(path: Path) -> Tuple[bool, bool]:
    """返回 (是否有加密文件条目, 是否为 AES 加密)。

    flag_bits & 0x1 表示加密；compress_type=99 或 extra 中的 0x9901 头表示
    WinZip AES —— Python 标准库不支持 AES，必须整包交给 7-Zip。
    """
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
    except Exception:
        return False, False
    enc = any((i.flag_bits & 0x1) and not i.is_dir() for i in infos)
    aes = any(i.compress_type == 99 or b"\x01\x99" in (i.extra or b"") for i in infos)
    return enc, aes


def _zip_pick_password(zf: zipfile.ZipFile, passwords: List[str]) -> Optional[bytes]:
    """用第一个加密文件条目验证候选密码，返回可用的密码字节；全错返回 None。

    ZipCrypto 密码错误时 zipfile 抛 RuntimeError("Bad password for file ...")。
    校验字节只有 1 字节，极小概率误判由后续 CRC 校验兜住（归 wrong_password）。
    """
    target = next((i for i in zf.infolist() if (i.flag_bits & 0x1) and not i.is_dir()), None)
    if target is None:
        return None
    for pwd in passwords:
        for b in _pwd_bytes(pwd):
            try:
                with zf.open(target, pwd=b) as fh:
                    fh.read(1)
                return b
            except Exception:
                continue
    return None


# ---------------------------------------------------------------------------
# 格式识别
# ---------------------------------------------------------------------------
def detect_format(path: Path) -> Optional[str]:
    """自动识别常见压缩格式：先按扩展名，再按文件头魔数兜底（支持无扩展名文件）。

    返回处理器族：zip / tar / single / external；None = 不是可识别的压缩包。
    分卷（.001 / .partN.rar）一律归 external：由 7-Zip 从首卷自动串读后续卷，
    Python 内置库无法拼接分卷，也不能把 .002 当成独立压缩包重复处理。
    """
    lower = path.name.lower()
    if NUM_VOLUME_RE.match(path.name) or PART_VOLUME_RE.match(path.name):
        return "external"
    for ext in TAR_EXT:
        if lower.endswith(ext):
            return "tar"
    for ext in EXTERNAL_EXT:
        if lower.endswith(ext):
            return "external"
    for ext, family in EXT_MAP.items():
        if lower.endswith(ext):
            return family
    try:
        with open(path, "rb") as fh:
            head = fh.read(512)
    except OSError:
        return None
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06"):
        return "zip"
    if head.startswith(b"\x1f\x8b"):
        return "tar" if b"ustar" in head[257:265] else "single"
    if head.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "external"
    if head.startswith(b"Rar!\x1a\x07"):
        return "external"
    if head.startswith(b"BZh"):
        return "single"
    if head.startswith(b"\xfd7zXZ\x00") or head.startswith(b"\x5d\x00\x00"):
        return "single"
    if head[257:262] == b"ustar":
        return "tar"
    return None


def _registry_7z_candidates() -> List[str]:
    """Windows：从卸载注册表读 7-Zip InstallLocation，覆盖自定义安装位置（如 D:\\7-Zip）。"""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    out: List[str] = []
    sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    views = [(winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_64KEY),
             (winreg.HKEY_LOCAL_MACHINE, winreg.KEY_WOW64_32KEY),
             (winreg.HKEY_CURRENT_USER, 0)]
    for hive, flag in views:
        try:
            base = winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | flag)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(base, i)
                except OSError:
                    break
                i += 1
                try:
                    item = winreg.OpenKey(base, name)
                    try:
                        display, _ = winreg.QueryValueEx(item, "DisplayName")
                        if "7-zip" in str(display).lower():
                            loc, _ = winreg.QueryValueEx(item, "InstallLocation")
                            if loc:
                                out.append(str(Path(str(loc)) / "7z.exe"))
                    finally:
                        winreg.CloseKey(item)
                except OSError:
                    continue
        finally:
            winreg.CloseKey(base)
    return out


def find_external_tool(explicit: str = "") -> Optional[str]:
    """探测 7z / 7zz：显式路径 > PATH > 卸载注册表 > 常见安装位置。"""
    candidates = [explicit] if explicit else []
    candidates += [shutil.which("7z"), shutil.which("7zz")]
    candidates += _registry_7z_candidates()
    candidates += [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
        r"D:\7-Zip\7z.exe", r"E:\7-Zip\7z.exe",
        "/usr/bin/7z", "/usr/local/bin/7z", "/usr/bin/7zz",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return str(Path(c).absolute())
    return None


# ---------------------------------------------------------------------------
# 路径安全与文件名编码修复
# ---------------------------------------------------------------------------
def safe_relpath(name: str) -> str:
    """把包内条目名规范化为安全的 POSIX 风格相对路径；不安全则抛 UnsafePath。"""
    n = (name or "").replace("\\", "/").strip()
    if not n or n.startswith("/") or n.startswith("//") or re.match(r"^[A-Za-z]:", n):
        raise UnsafePath("绝对路径 / 盘符 / 空路径：%r" % name)
    parts: List[str] = []
    for part in n.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise UnsafePath(".. 目录逃逸：%r" % name)
        if any(ch in part for ch in '<>:"|?*') or any(ord(ch) < 32 for ch in part):
            raise UnsafePath("非法字符：%r" % part)
        stem = part.split(".")[0].upper()
        if stem in WIN_RESERVED or part.rstrip(" .") == "":
            raise UnsafePath("Windows 保留名 / 非法结尾：%r" % part)
        parts.append(part)
    if not parts:
        raise UnsafePath("规范化后为空路径：%r" % name)
    return "/".join(parts)


def zip_entry_name(info: zipfile.ZipInfo) -> str:
    """修复 Windows 下由 GBK 编码写入、未设置 UTF-8 标志位的 zip 中文名。"""
    name = info.filename
    if not (info.flag_bits & 0x800):
        try:
            raw = name.encode("cp437")
            for enc in ("gbk", "utf-8"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
        except UnicodeEncodeError:
            pass
    return name


def tar_entry_name(name: str) -> str:
    """修复非 UTF-8 编码的 tar 中文名（tarfile 以 surrogateescape 保留原始字节）。"""
    if any(0xDC80 <= ord(ch) <= 0xDCFF for ch in name):
        try:
            return name.encode("utf-8", "surrogateescape").decode("gbk")
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    return name


def single_output_name(path: Path) -> str:
    """单文件流压缩：去掉压缩后缀得到输出文件名；.tgz 等 tar 族不走这里。"""
    lower = path.name.lower()
    for ext in (".gz", ".bz2", ".xz", ".lzma"):
        if lower.endswith(ext):
            base = path.name[:-len(ext)]
            return base or "extracted.bin"
    return path.name + ".out"


# ---------------------------------------------------------------------------
# 阶段一：把压缩包解到隔离的 staging 目录（逐条目容错）
# ---------------------------------------------------------------------------
def _discard_partial(target: Optional[Path]) -> None:
    """丢弃条目级失败时已写入 staging 的半截文件，避免错误数据被搬运到输出目录。"""
    if target is None:
        return
    try:
        if target.is_file():
            target.unlink()
    except OSError:
        pass


def _stage_zip(path: Path, staging: Path, emit: Callable[[str, str], None],
               pw: PasswordContext) -> List[Dict[str, str]]:
    """返回条目级失败清单 [{"reason","path","detail"}]；不因单条目失败而中断。

    加密策略：无密码 -> 每个加密条目记 password_required；有候选密码 -> 先验证
    出可用密码，再逐条目解密，密码全错记 wrong_password。明文条目始终照常解出。
    """
    failures: List[Dict[str, str]] = []
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        encrypted = any((i.flag_bits & 0x1) and not i.is_dir() for i in infos)
        chosen: Optional[bytes] = None
        pwd_state = "plain"       # plain / none / chosen / failed
        solved_marked = False
        if encrypted:
            pw.attempted += 1  # 无论是否提供密码，该包都触发了一次「需要认证」判定
            if not pw.provided:
                pwd_state = "none"
            else:
                chosen = _zip_pick_password(zf, pw.passwords)
                pwd_state = "chosen" if chosen is not None else "failed"

        for info in infos:
            raw_name = zip_entry_name(info)
            is_enc = bool(info.flag_bits & 0x1) and not info.is_dir()
            target: Optional[Path] = None
            try:
                if is_enc and pwd_state == "none":
                    raise ArchiveError(
                        "password_required",
                        "条目已加密但未提供密码：%s；请在页面填写「解压密码 / 候选密码」，"
                        "或 CLI 使用 --password / --password-file / BX_PASSWORD" % raw_name)
                if is_enc and pwd_state == "failed":
                    raise ArchiveError(
                        "wrong_password",
                        "条目已加密，已尝试 %d 个候选密码均不正确：%s；请核对密码后重试"
                        % (len(pw.passwords), raw_name))
                rel = safe_relpath(raw_name)
                target = staging.joinpath(*rel.split("/"))
                if info.is_dir() or raw_name.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                # AES 加密包由 stage_archive 前置转给 7-Zip，这里只会遇到 ZipCrypto
                with zf.open(info, pwd=chosen if is_enc else None) as src, \
                        open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 256)
                if is_enc and not solved_marked:
                    solved_marked = True
                    pw.solved += 1
                    emit("info", "已用候选密码解开加密 ZIP：%s（密码不记录）" % path.name)
            except UnsafePath as exc:
                _discard_partial(target)
                failures.append({"reason": "unsafe_path", "path": raw_name, "detail": str(exc)})
            except ArchiveError as exc:
                _discard_partial(target)
                failures.append({"reason": exc.reason, "path": raw_name, "detail": exc.detail})
            except NotImplementedError as exc:
                _discard_partial(target)
                failures.append({"reason": "unsupported", "path": raw_name,
                                 "detail": "标准库不支持该加密算法（多为 AES 加密 ZIP），"
                                           "请安装 7-Zip 后重试：%s" % pw.scrub(str(exc))})
            except RuntimeError as exc:
                _discard_partial(target)
                msg = pw.scrub(str(exc))
                reason = "wrong_password" if "password" in msg.lower() else "corrupt"
                failures.append({"reason": reason, "path": raw_name, "detail": msg})
            except (zipfile.BadZipFile, EOFError, OSError, ValueError) as exc:
                # 密码碰巧通过 1 字节校验时，CRC 在读完时失败 -> 也属于密码不对
                _discard_partial(target)
                msg = pw.scrub("%s: %s" % (type(exc).__name__, exc))
                if is_enc and "crc" in msg.lower():
                    reason = "wrong_password"
                elif isinstance(exc, OSError) and not isinstance(exc, EOFError):
                    reason = "io_error"
                else:
                    reason = "corrupt"
                failures.append({"reason": reason, "path": raw_name, "detail": msg})
            except Exception as exc:  # 未预期异常也只影响当前条目
                _discard_partial(target)
                failures.append({"reason": "corrupt", "path": raw_name,
                                 "detail": pw.scrub("%s: %s" % (type(exc).__name__, exc))})
    return failures


def _stage_tar(path: Path, staging: Path, emit: Callable[[str, str], None]) -> List[Dict[str, str]]:
    failures: List[Dict[str, str]] = []
    with tarfile.open(path, mode="r:*", encoding="utf-8", errors="surrogateescape") as tf:
        for member in tf:
            raw_name = tar_entry_name(member.name)
            try:
                # 拒绝链接 / 设备文件，避免写入包外的位置
                if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    raise UnsafePath("链接 / 设备 / FIFO 条目不允许落地：%s" % raw_name)
                rel = safe_relpath(raw_name)
                target = staging.joinpath(*rel.split("/"))
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise UnsafePath("非常规文件条目：%s" % raw_name)
                target.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(member)
                if src is None:
                    raise ArchiveError("corrupt", "条目无法读取：%s" % raw_name)
                with src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1024 * 256)
            except UnsafePath as exc:
                failures.append({"reason": "unsafe_path", "path": raw_name, "detail": str(exc)})
            except ArchiveError as exc:
                failures.append({"reason": exc.reason, "path": raw_name, "detail": exc.detail})
            except (tarfile.TarError, EOFError, OSError, ValueError) as exc:
                reason = "io_error" if isinstance(exc, OSError) else "corrupt"
                failures.append({"reason": reason, "path": raw_name,
                                 "detail": "%s: %s" % (type(exc).__name__, exc)})
            except Exception as exc:
                failures.append({"reason": "corrupt", "path": raw_name,
                                 "detail": "%s: %s" % (type(exc).__name__, exc)})
    return failures


def _stage_single(path: Path, staging: Path) -> List[Dict[str, str]]:
    """单文件流压缩（.gz/.bz2/.xz/.lzma）：整包即一个条目。"""
    import bz2
    import gzip
    import lzma

    lower = path.name.lower()
    if lower.endswith(".gz"):
        opener = gzip.open
    elif lower.endswith(".bz2"):
        opener = bz2.open
    else:
        opener = lzma.open
    target = staging / safe_relpath(single_output_name(path))
    target.parent.mkdir(parents=True, exist_ok=True)
    with opener(path, "rb") as src, open(target, "wb") as dst:
        shutil.copyfileobj(src, dst, length=1024 * 256)
    return []


def _stage_external(path: Path, staging: Path, tool: Optional[str],
                    pw: PasswordContext, emit: Callable[[str, str], None]) -> List[Dict[str, str]]:
    """调用 7z/7zz 解到 staging；整包级失败抛 ArchiveError。

    密码尝试顺序：先「无密码」（明文包零额外开销），再按候选密码逐个重试。
    实测 7-Zip 在错密码时也会写出 0 字节/半截文件，故每次尝试前清空 staging；
    只有 returncode==0 的那一次结果才会被 relocate 搬到输出目录。
    """
    if not tool:
        raise ArchiveError(
            "unsupported",
            "%s 需要 7-Zip（7z.exe / 7zz）。单文件 .7z/.rar、.001 分卷、AES 加密 ZIP "
            "都依赖它，请安装后重试，或在页面/CLI 中指定外部工具路径。" % path.name,
        )
    last_out = ""
    attempt_marked = False
    cmd_base = [tool, "x", "-y", "-bso0", "-bsp0"]
    kwargs: Dict[str, Any] = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT,
                              # 分卷缺卷 / 加密包无密码时 7-Zip 会交互式询问，
                              # 关闭 stdin 可避免任务挂住（表现为 "Break signaled"）。
                              "stdin": subprocess.DEVNULL,
                              "text": True, "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    for index, password in enumerate([None] + pw.passwords):
        _clear_staging(staging)
        cmd = list(cmd_base)
        if password is not None:
            # -p<password> 作为单个 argv 元素传递，不拼接进 shell；
            # 密码绝不写入日志 / 汇总 / 异常文本（下面还有 scrub 兜底）。
            cmd.append("-p" + password)
        cmd += ["-o%s" % str(staging), str(path)]
        try:
            proc = subprocess.run(cmd, **kwargs)
        except OSError as exc:
            raise ArchiveError("external_failed", "外部工具无法启动：%s" % exc)
        out = pw.scrub((proc.stdout or "").strip())
        if proc.returncode == 0:
            if password is not None:
                pw.solved += 1
                emit("info", "已通过第 %d 个候选密码解压：%s（密码不记录）"
                     % (index, path.name))
            return []
        kind = _classify_7z(proc.returncode, out)
        if kind == "password":
            if not attempt_marked:
                pw.attempted += 1
                attempt_marked = True
            last_out = out
            continue  # 换下一个候选密码重试，不把半成品落地
        if kind == "missing_volume":
            raise ArchiveError("missing_volume", "外部工具报告分卷缺失：%s"
                               % (out[-400:] or "无输出"))
        if kind == "corrupt":
            raise ArchiveError("corrupt", "外部工具报告数据损坏 / 非有效归档：%s"
                               % (out[-400:] or "无输出"))
        raise ArchiveError("external_failed", "退出码 %s：%s"
                           % (proc.returncode, out[-400:] or "无输出"))

    tail = (last_out[-300:] or "无输出")
    if not pw.passwords:
        raise ArchiveError(
            "password_required",
            "该压缩包已加密，但未提供任何密码（7-Zip：%s）。请在页面填写「解压密码」，"
            "或 CLI 使用 --password / --password-file / BX_PASSWORD 后重试" % tail)
    raise ArchiveError(
        "wrong_password",
        "已尝试 %d 个候选密码，均未能解开该压缩包（7-Zip：%s）。请核对密码后重试"
        % (len(pw.passwords), tail))


def stage_archive(path: Path, family: str, staging: Path, tool: Optional[str],
                  pw: PasswordContext, emit: Callable[[str, str], None]) -> List[Dict[str, str]]:
    """打开包 -> 逐条目写入 staging。包级异常抛 ArchiveError，条目级失败以列表返回。"""
    try:
        if family == "zip":
            encrypted, aes = _zip_encryption_info(path)
            if encrypted and aes:
                # Python zipfile 对 AES 加密 ZIP 会抛 NotImplementedError，整包转 7-Zip
                if tool:
                    emit("info", "检测到 AES 加密 ZIP，转由 7-Zip 解密：%s" % path.name)
                    return _stage_external(path, staging, tool, pw, emit)
                raise ArchiveError("unsupported",
                                   "AES 加密 ZIP 需要 7-Zip（7z.exe / 7zz），"
                                   "请安装后重试或在页面指定 7z.exe 路径")
            return _stage_zip(path, staging, emit, pw)
        if family == "tar":
            return _stage_tar(path, staging, emit)
        if family == "single":
            return _stage_single(path, staging)
        if family == "external":
            # 分卷只需传首卷：7-Zip 会按同目录同名编号自动串读 .002/.003…
            return _stage_external(path, staging, tool, pw, emit)
        raise ArchiveError("unsupported", "未识别的格式族：%s" % family)
    except ArchiveError:
        raise
    except zipfile.BadZipFile as exc:
        raise ArchiveError("corrupt", "ZIP 结构无效：%s" % exc)
    except tarfile.ReadError as exc:
        raise ArchiveError("corrupt", "TAR 结构无效：%s" % exc)
    except (EOFError, OSError, ValueError) as exc:
        # gzip/BZ2/XZ 的 CRC 与截断错误多表现为 EOFError / OSError
        raise ArchiveError("corrupt", "压缩流被截断或校验失败：%s: %s" % (type(exc).__name__, exc))
    except Exception as exc:
        raise ArchiveError("corrupt", "打开压缩包时未预期异常：%s: %s" % (type(exc).__name__, exc))


# ---------------------------------------------------------------------------
# 阶段二：staging -> outputDir，统一的冲突检测 / 覆盖策略 / 统计
# ---------------------------------------------------------------------------
def relocate(staging: Path, dest_root: Path, archive: str, overwrite: bool,
             add: Callable[[str, str, str, str, str], None],
             emit: Callable[[str, str], None]) -> Tuple[int, int, int]:
    """返回 (成功数, 失败数, 跳过数)。空目录也保留；逐文件容错。"""
    ok = failed = skipped = 0
    try:
        dest_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        add("failed", archive, _s(dest_root), "io_error", "输出目录创建失败：%s" % exc)
        emit("error", "输出目录创建失败：%s" % exc)
        return 0, 1, 0
    for root, dirs, files in os.walk(str(staging), followlinks=False):
        rel_root = Path(root).relative_to(staging)
        for d in dirs:
            p = staging / rel_root / d
            if p.is_symlink():
                add("failed", archive, _s(dest_root / rel_root / d), "unsafe_path", "符号链接目录")
                failed += 1
                continue
            (dest_root / rel_root / d).mkdir(parents=True, exist_ok=True)
        for f in files:
            src = Path(root) / f
            rel = rel_root / f
            dest = dest_root / rel
            if src.is_symlink():
                add("failed", archive, _s(dest), "unsafe_path", "符号链接文件")
                failed += 1
                emit("warn", "跳过符号链接条目：%s" % rel.as_posix())
                continue
            if os.path.lexists(str(dest)):
                if not overwrite:
                    add("skipped", archive, _s(dest), "conflict",
                        "输出目录已存在同名条目，默认不覆盖")
                    skipped += 1
                    emit("warn", "跳过（已存在）：%s" % _s(dest))
                    continue
                if dest.is_dir():
                    add("failed", archive, _s(dest), "io_error", "目标是目录，无法以文件覆盖")
                    failed += 1
                    continue
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dest))
                add("success", archive, _s(dest), "", "")
                ok += 1
            except OSError as exc:
                add("failed", archive, _s(dest), "io_error", "%s: %s" % (type(exc).__name__, exc))
                failed += 1
                emit("error", "写入失败 %s：%s" % (_s(dest), exc))
    return ok, failed, skipped


# ---------------------------------------------------------------------------
# 分卷压缩包：识别 -> 同目录补全 -> 编号连续性检查 -> 合并为「一个解压单元」
# ---------------------------------------------------------------------------
def parse_volume(path: Path) -> Optional[Dict[str, Any]]:
    """解析分卷文件名，非分卷返回 None。

    key = (父目录, 组名, 命名风格, 编号宽度) 唯一确定一组分卷。
    用 os.path.normcase 比较：Windows 大小写不敏感，Linux 保持大小写敏感。
    partN.rar 的编号宽度固定记 0，避免 part9 / part10 被误拆成两组。
    """
    m = NUM_VOLUME_RE.match(path.name)
    if m:
        stem, num, kind = m.group("stem"), m.group("num"), "num"
    else:
        m = PART_VOLUME_RE.match(path.name)
        if not m:
            return None
        stem, num, kind = m.group("stem"), m.group("num"), "part"
    try:
        parent = os.path.normcase(str(path.parent.absolute()))
    except Exception:
        parent = os.path.normcase(str(path.parent))
    width = len(num) if kind == "num" else 0
    return {"stem": stem, "index": int(num), "width": width, "kind": kind,
            "key": (parent, os.path.normcase(stem), kind, width)}


def scan_volume_group(spec: Dict[str, Any]) -> Dict[str, Any]:
    """扫描首卷所在目录，补全同组全部分卷。

    只显式传一个卷（哪怕是 .003）也能找齐整组；分卷不在同目录时不会被误并。
    返回 {"files": 按编号升序的 Path 列表, "missing": 缺失编号列表, ...}
    """
    parent = Path(spec["key"][0])
    found: Dict[int, Path] = {}
    try:
        entries = list(parent.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
            v = parse_volume(entry)
        except OSError:
            continue
        if v and v["key"] == spec["key"]:
            found[v["index"]] = entry
    if not found:  # 防御性：正常情况下至少触发扫描的那个文件存在
        return {"files": [], "missing": [1], "top": 0, "stem": spec["stem"],
                "kind": spec["kind"], "width": spec["width"]}
    top = max(found)
    missing = [i for i in range(1, top + 1) if i not in found]
    return {"files": [found[i] for i in sorted(found)], "missing": missing, "top": top,
            "stem": spec["stem"], "kind": spec["kind"], "width": spec["width"]}


def stem_for_output(name: str) -> str:
    """由（组）文件名推出输出子目录名：多段 tar 后缀优先，否则去最后一段后缀。"""
    lower = name.lower()
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if lower.endswith(ext):
            return name[:-len(ext)]
    return Path(name).stem


def volume_file_name(group: Dict[str, Any], index: int) -> str:
    """按组的命名风格还原某个编号对应的文件名，用于缺卷提示。"""
    if group["kind"] == "num":
        return "%s.%0*d" % (group["stem"], group["width"], index)
    return "%s.part%d.rar" % (group["stem"], index)


# ---------------------------------------------------------------------------
# 输入收集
# ---------------------------------------------------------------------------
def collect_archives(cfg: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]], List[str]]:
    """返回 (解压单元列表, 输入阶段失败项, 提示信息)。不因单个坏路径中断。

    解压单元 unit 的字段（页面/CLI 不直接暴露，仅内部使用）：
      path/Archive      首卷或唯一包的 Path / 绝对路径字符串
      stem              输出子目录名（分卷取组名，不是首卷文件名）
      family            处理器族；分卷恒为 external
      volumes           全部分卷绝对路径（普通包为 []）
      display           日志用的短名，例如 "7z.001 … 7z.005（5 卷）"
      volumeSet         分卷组摘要，进入汇总 volumeSets；普通包为 None
      error             缺卷等收集期失败（{"reason","detail"}）；正常为 None
    """
    problems: List[Dict[str, str]] = []
    notes: List[str] = []
    raw: List[Path] = []
    mode = cfg.get("mode", "dir")
    if mode == "dir":
        input_dir = Path(str(cfg.get("inputDir", ""))).expanduser()
        if not input_dir.exists() or not input_dir.is_dir():
            return [], [{"reason": "invalid_input", "path": _s(input_dir),
                         "detail": "输入目录不存在或不是目录"}], notes
        try:
            for p in sorted(input_dir.rglob("*")):
                if p.is_file():
                    raw.append(p)
        except OSError as exc:
            problems.append({"reason": "invalid_input", "path": _s(input_dir),
                             "detail": "目录遍历失败：%s" % exc})

    # 显式路径列表始终生效：mode="files" 时是唯一来源；mode="dir" 时作为追加输入。
    # 前端按「一行一个路径」传入，这里逐项清理引号与首尾空白。
    for item in cfg.get("archives", []) or []:
        text = str(item).strip().strip('"').strip()
        if not text:
            continue
        p = Path(text).expanduser()
        if p.is_dir():
            try:
                raw.extend(sorted(q for q in p.rglob("*") if q.is_file()))
            except OSError as exc:
                problems.append({"reason": "invalid_input", "path": _s(p),
                                 "detail": "目录遍历失败：%s" % exc})
        else:
            raw.append(p)

    output_dir = Path(str(cfg.get("outputDir", ""))).expanduser()
    try:
        out_abs = output_dir.absolute()
    except Exception:
        out_abs = None
    # 统一前置过滤：绝对路径去重 / 排除输出目录 / 存在性检查
    candidates: List[Path] = []
    seen = set()
    for p in raw:
        try:
            abs_p = p.absolute()
        except Exception:
            continue
        if out_abs is not None and (abs_p == out_abs or out_abs in abs_p.parents):
            continue  # 排除输出目录自身，避免把结果当输入
        if str(abs_p).lower() in seen:
            continue
        seen.add(str(abs_p).lower())
        if not p.exists():
            problems.append({"reason": "invalid_input", "path": _s(p),
                             "detail": "显式指定的路径不存在"})
            continue
        if not p.is_file():
            continue
        candidates.append(p)

    units: List[Dict[str, Any]] = []
    volume_specs: Dict[Tuple[str, str, str, int], Dict[str, Any]] = {}
    for p in candidates:
        v = parse_volume(p)
        if v:
            # 同一组只记一个触发文件；其余卷稍后从磁盘扫描补全，绝不重复解压
            volume_specs.setdefault(v["key"], v)
            continue
        family = detect_format(p)
        if family is None:
            continue  # 目录模式下的普通文件静默忽略
        units.append({"path": p, "archive": _s(p), "stem": stem_for_output(p.name),
                      "family": family, "volumes": [], "display": p.name,
                      "volumeSet": None, "error": None})

    for spec in volume_specs.values():
        group = scan_volume_group(spec)
        files = group["files"]
        info = {
            "name": group["stem"],
            "first": _s(files[0]) if files else spec["key"][0],
            "last": _s(files[-1]) if files else spec["key"][0],
            "count": len(files),
            "missing": group["missing"],
        }
        if not files or group["missing"]:
            # 缺首卷 / 中间断号：明确归因为 missing_volume，整组跳过且不产出半成品
            names = [volume_file_name(group, i) for i in group["missing"][:8]]
            if len(group["missing"]) > 8:
                names.append("…")
            detail = ("分卷组 %s 发现 %d 卷、最大编号 %d，编号不连续，缺失：%s；"
                      "请把 .001 起的全部卷放在同一目录后重试（本组已跳过）"
                      % (group["stem"], len(files), group["top"],
                         ", ".join(names) or "首卷"))
            units.append({"path": files[0] if files else Path(spec["key"][0]),
                          "archive": info["first"],
                          "stem": stem_for_output(group["stem"]),
                          "family": "external", "volumes": [_s(f) for f in files],
                          "display": group["stem"], "volumeSet": info,
                          "error": {"reason": "missing_volume", "detail": detail}})
            continue
        first = files[0]
        units.append({
            "path": first, "archive": _s(first),
            "stem": stem_for_output(group["stem"]),  # 7z.001 -> 7z；xxx.7z.001 -> xxx
            "family": "external",
            "volumes": [_s(f) for f in files],
            "display": ("%s … %s（%d 卷）" % (first.name, files[-1].name, len(files))
                        if len(files) > 1 else first.name),
            "volumeSet": info, "error": None,
        })

    units.sort(key=lambda u: u["archive"].lower())
    if mode == "dir" and not units:
        notes.append("输入目录中未发现可识别的压缩包文件")
    return units, problems, notes


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def run_extract(cfg: Dict[str, Any], emit: Optional[Callable[[str, str], None]] = None) -> Dict[str, Any]:
    """执行批量解压，恒定返回统一汇总对象；不向外抛业务异常。"""
    messages: List[Dict[str, str]] = []

    def log(level: str, text: str) -> None:
        if len(messages) < MAX_MESSAGES:
            messages.append({"level": level, "text": text, "at": _now()})
        elif len(messages) == MAX_MESSAGES:
            messages.append({"level": "warn", "text": "提示信息已达上限 %d 条，后续不再记录" % MAX_MESSAGES,
                             "at": _now()})
        if emit:
            try:
                emit(level, text)
            except Exception:
                pass

    started = _now()
    items: List[Dict[str, str]] = []

    def add(status: str, archive: str, path: str, reason: str, detail: str) -> None:
        items.append({"status": status, "archive": archive, "path": path,
                      "reason": reason, "detail": detail})

    overwrite = bool(cfg.get("overwrite", False))
    use_subdir = bool(cfg.get("useArchiveSubdir", True))
    external_tool = find_external_tool(str(cfg.get("externalTool", "") or ""))
    output_dir = Path(str(cfg.get("outputDir", "") or "")).expanduser()
    pw = PasswordContext(cfg)  # 候选密码（明文仅存于内存，结束后由 HTTP 层清空）

    # 配置校验也走统一汇总，不抛异常、不返回 4xx
    config_problems: List[Dict[str, str]] = list(pw.errors)
    if not str(cfg.get("outputDir", "") or "").strip():
        config_problems.append({"reason": "invalid_input", "path": "", "detail": "输出目录不能为空"})
    elif output_dir.exists() and not output_dir.is_dir():
        config_problems.append({"reason": "invalid_input", "path": _s(output_dir),
                                "detail": "输出路径已存在且是文件"})
    if cfg.get("mode", "dir") not in ("dir", "files"):
        config_problems.append({"reason": "invalid_input", "path": "",
                                "detail": "mode 只能是 dir 或 files"})
    if cfg.get("mode", "dir") == "files" and not (cfg.get("archives") or []):
        config_problems.append({"reason": "invalid_input", "path": "",
                                "detail": "直接指定模式（mode=files）下 archives 不能为空"})

    archives: List[Dict[str, Any]] = []
    if not config_problems:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            config_problems.append({"reason": "invalid_input", "path": _s(output_dir),
                                    "detail": "输出目录创建失败：%s" % exc})
    if not config_problems:
        archives, input_problems, notes = collect_archives(cfg)
        for n in notes:
            log("info", n)
    else:
        input_problems = []

    for prob in config_problems + input_problems:
        detail = prob["detail"]
        add("failed", prob.get("archive", ""), prob.get("path", ""), prob["reason"], detail)
        log("error", "[%s] %s %s" % (prob["reason"], prob.get("path", ""), detail))

    volume_sets = [u["volumeSet"] for u in archives if u.get("volumeSet")]
    log("info", "开始处理：共 %d 个压缩包（分卷组 %d 组），输出目录 %s，覆盖策略 %s" %
        (len(archives), len(volume_sets),
         _s(output_dir) if str(cfg.get("outputDir", "")) else "(未设置)",
         "覆盖" if overwrite else "不覆盖（同名跳过）"))
    if pw.provided:
        log("info", "已提供 %d 个候选密码（明文不记录）；明文包直接解压，加密包按顺序尝试"
            % len(pw.passwords))
    for vs in volume_sets:
        if vs["count"] > 1:
            log("info", "分卷组 %s：%s → %s（%d 卷），仅解首卷，7-Zip 自动串读后续卷" %
                (vs["name"], Path(vs["first"]).name, Path(vs["last"]).name, vs["count"]))
        if vs["missing"]:
            log("warn", "分卷组 %s 缺卷编号：%s" %
                (vs["name"], ", ".join(str(i) for i in vs["missing"])))

    archive_counts = {"ok": 0, "partial": 0, "failed": 0}
    for idx, unit in enumerate(archives, 1):
        arc = unit["path"]
        arc_s = unit["archive"]
        display = unit["display"]
        log("info", "[%d/%d] %s" % (idx, len(archives), display if unit["volumes"] else arc_s))

        # 收集期已判定的整组失败（如缺卷）：记 1 条包级失败，不进入解压，继续下一组
        err = unit.get("error")
        if err:
            add("failed", arc_s, arc_s, err["reason"], err["detail"])
            archive_counts["failed"] += 1
            log("error", "整组跳过 [%s] %s：%s" % (err["reason"], display, err["detail"]))
            continue

        family = unit["family"]
        if family is None:
            add("failed", arc_s, arc_s, "unsupported", "无法识别的压缩格式")
            archive_counts["failed"] += 1
            log("error", "格式不支持：%s" % arc_s)
            continue

        stem = unit["stem"]
        dest_root = output_dir / safe_relpath_for_stem(stem) if use_subdir else output_dir

        ok = failed = skipped = 0
        try:
            with tempfile.TemporaryDirectory(prefix="bx_stage_") as tmp:
                staging = Path(tmp)
                entry_failures = stage_archive(arc, family, staging, external_tool, pw, log)
                for ef in entry_failures:
                    add("failed", arc_s, ef["path"], ef["reason"], ef["detail"])
                    failed += 1
                    log("error", "条目失败 [%s] %s：%s" % (ef["reason"], ef["path"], ef["detail"]))
                # 外部工具整体失败时抛 ArchiveError，staging 中的半成品不落地
                ok2, failed2, skipped2 = relocate(staging, dest_root, arc_s, overwrite, add, log)
                ok += ok2
                failed += failed2
                skipped += skipped2
            if ok == 0 and failed == 0 and skipped == 0:
                log("warn", "空包（无文件条目）：%s" % arc_s)
            log("success" if failed == 0 else "warn",
                "完成：%s | 成功 %d，失败 %d，跳过 %d" % (display, ok, failed, skipped))
        except ArchiveError as exc:
            add("failed", arc_s, arc_s, exc.reason, exc.detail)
            failed += 1
            log("error", "整包失败 [%s] %s：%s" % (exc.reason, display, exc.detail))
        except UnsafePath as exc:
            add("failed", arc_s, arc_s, "unsafe_path", str(exc))
            failed += 1
            log("error", "整包失败 [unsafe_path] %s：%s" % (display, exc))
        except Exception as exc:  # 顶层兜底，保证后续压缩包继续处理
            add("failed", arc_s, arc_s, "external_failed", "%s: %s" % (type(exc).__name__, exc))
            failed += 1
            log("error", "整包未预期异常 %s：%s" % (display, exc))

        if failed == 0:
            archive_counts["ok"] += 1
        elif ok > 0:
            archive_counts["partial"] += 1
        else:
            archive_counts["failed"] += 1

    counts = {
        "success": sum(1 for i in items if i["status"] == "success"),
        "failed": sum(1 for i in items if i["status"] == "failed"),
        "skipped": sum(1 for i in items if i["status"] == "skipped"),
    }
    groups: Dict[str, List[Dict[str, Any]]] = {"failed": [], "skipped": []}
    for status in ("failed", "skipped"):
        bucket: Dict[str, List[Dict[str, str]]] = {}
        for i in items:
            if i["status"] != status:
                continue
            bucket.setdefault(i["reason"], []).append(
                {"archive": i["archive"], "path": i["path"], "detail": i["detail"]})
        for reason in sorted(bucket, key=lambda r: (-len(bucket[r]), r)):
            groups[status].append({
                "reason": reason,
                "label": REASONS.get(reason, reason),
                "count": len(bucket[reason]),
                "items": bucket[reason],
            })

    log("info", "全部完成：成功 %d，失败 %d，跳过 %d" %
        (counts["success"], counts["failed"], counts["skipped"]))
    return {
        "schema": SCHEMA,
        "counts": counts,
        "archiveCounts": archive_counts,
        "groups": groups,
        "items": items,
        "messages": messages,
        "volumeSets": volume_sets,
        "auth": pw.auth(),
        "outputDir": _s(output_dir) if str(cfg.get("outputDir", "")) else "",
        "startedAt": started,
        "finishedAt": _now(),
    }


def safe_relpath_for_stem(stem: str) -> str:
    """压缩包名做目录名时的安全处理（不改变包内层级，只处理外层目录名）。"""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip(" .")
    cleaned = cleaned or "archive"
    if cleaned.split(".")[0].upper() in WIN_RESERVED:
        cleaned = "_" + cleaned
    return cleaned


# ---------------------------------------------------------------------------
# 本地 HTTP 服务：异步任务 + 轮询
# ---------------------------------------------------------------------------
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
# PyInstaller --onefile 冻结运行时，资源解包到 sys._MEIPASS；常规运行用脚本所在目录
if getattr(sys, "frozen", False):
    WEB_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "web"
    # 冻结模式下 __file__ 指向临时解包目录，默认输出应落在 exe 所在目录
    _APP_DIR = Path(sys.executable).resolve().parent
else:
    WEB_DIR = Path(__file__).resolve().parent / "web"
    _APP_DIR = Path(__file__).resolve().parent


def _worker(job_id: str, cfg: Dict[str, Any]) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]

    def emit(level: str, text: str) -> None:
        # run_extract 的 log 在写 summary.messages 的同时回调本函数，
        # 让 GET /api/jobs/<id> 在处理过程中就能拿到实时提示信息。
        job["messages"].append({"level": level, "text": text, "at": _now()})

    try:
        summary = run_extract(cfg, emit=emit)
        job["messages"] = summary["messages"]
        job["summary"] = summary
        job["status"] = "done"
    except Exception as exc:  # 防御性兜底：即便如此也返回结构化错误
        job["error"] = "%s: %s" % (type(exc).__name__, exc)
        job["status"] = "error"
    finally:
        # 密码用完即清：明文只在任务执行期间存在于本进程内存，
        # 不进入 JOBS 快照、GET 响应、任何日志或汇总字段。
        cfg["password"] = ""
        cfg["passwords"] = []
        cfg["passwordFile"] = ""


class Handler(BaseHTTPRequestHandler):
    server_version = "BatchExtractor/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("[http] %s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            page = WEB_DIR / "index.html"
            if not page.is_file():
                self._json(404, {"error": "缺少 web/index.html"})
                return
            body = page.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/api/meta":
            self._json(200, {
                "schema": SCHEMA,
                "formats": [
                    {"ext": ".zip / .jar / .whl / .apk", "via": "zipfile（内置）"},
                    {"ext": ".tar / .tar.gz / .tgz / .tar.bz2 / .tar.xz", "via": "tarfile（内置）"},
                    {"ext": ".gz / .bz2 / .xz / .lzma", "via": "内置，按单文件解出"},
                    {"ext": ".7z / .rar", "via": "7-Zip（外部工具）"},
                    {"ext": ".001 / .002 … 数字分卷（7z.001、xxx.7z.001）",
                     "via": "7-Zip 按首卷自动合并，整组算 1 个包"},
                    {"ext": ".part1.rar … RAR 分卷", "via": "同上，按 part1 归组"},
                    {"ext": "加密包（.7z / .rar / 分卷 / AES ZIP）",
                     "via": "候选密码逐个重试；缺密码=password_required，密码错=wrong_password"},
                    {"ext": "无扩展名", "via": "按文件头魔数自动识别"},
                ],
                "reasons": REASONS,
                "externalTool": find_external_tool(),
                "outputDirDefault": _s(_APP_DIR / "output"),
                "auth": {
                    "supported": True,
                    "fields": ["password", "passwords", "passwordFile"],
                    "env": ["BX_PASSWORD", "BX_PASSWORD_FILE"],
                    "maxCandidates": MAX_PASSWORDS,
                    "note": "密码仅本次任务内存使用，不写入 messages/items/groups/汇总/日志；"
                            "汇总 auth 只给数量，不给明文",
                },
            })
            return
        if path.startswith("/api/jobs/"):
            job_id = path[len("/api/jobs/"):].strip("/")
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    self._json(404, {"error": "任务不存在或已过期：%s" % job_id})
                    return
                snapshot = {"status": job["status"],
                            "messages": list(job["messages"]),
                            "summary": job["summary"], "error": job["error"]}
            self._json(200, snapshot)
            return
        self._json(404, {"error": "未知接口：%s" % path})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path != "/api/jobs":
            self._json(404, {"error": "未知接口：%s" % path})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            cfg = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as exc:
            self._json(400, {"error": "请求体不是合法 JSON：%s" % exc})
            return
        if not isinstance(cfg, dict) or not isinstance(cfg.get("outputDir", ""), str):
            self._json(400, {"error": "请求体必须是对象，且 outputDir 为字符串"})
            return
        if not isinstance(cfg.get("archives", []), list):
            self._json(400, {"error": "archives 必须是字符串数组"})
            return
        if not isinstance(cfg.get("passwords", []), list):
            self._json(400, {"error": "passwords 必须是字符串数组"})
            return
        if not isinstance(cfg.get("password", ""), str) or \
                not isinstance(cfg.get("passwordFile", ""), str):
            self._json(400, {"error": "password / passwordFile 必须是字符串"})
            return
        # 参数传递约定见文件头「二」：mode / inputDir / archives / outputDir /
        # overwrite / useArchiveSubdir / externalTool / password / passwords /
        # passwordFile。密码字段只进后台线程，用完即清空，接口从不回显。
        norm = {
            "mode": cfg.get("mode", "dir"),
            "inputDir": cfg.get("inputDir", ""),
            "archives": [str(a) for a in (cfg.get("archives") or [])],
            "outputDir": cfg.get("outputDir", ""),
            "overwrite": bool(cfg.get("overwrite", False)),
            "useArchiveSubdir": bool(cfg.get("useArchiveSubdir", True)),
            "externalTool": cfg.get("externalTool", "") or "",
            "password": cfg.get("password", "") or "",
            "passwords": [str(p) for p in (cfg.get("passwords") or [])],
            "passwordFile": cfg.get("passwordFile", "") or "",
        }
        job_id = uuid.uuid4().hex[:12]
        with JOBS_LOCK:
            # 简单内存回收，避免长时间运行堆积
            if len(JOBS) > 50:
                for k in list(JOBS)[:-20]:
                    JOBS.pop(k, None)
            JOBS[job_id] = {"status": "running", "messages": [], "summary": None, "error": None}
        threading.Thread(target=_worker, args=(job_id, norm), daemon=True).start()
        self._json(202, {"jobId": job_id})


def serve(host: str, port: int) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    print("批量解压控制台： http://%s:%d  (Ctrl+C 退出)" % (host, port), flush=True)
    print("外部工具：%s" % (find_external_tool() or
                          "未检测到 7-Zip，.7z/.rar/.001 分卷将记为 unsupported"), flush=True)
    print("加密包：页面填「解压密码 / 候选密码」；CLI 用 --password / --password-file "
          "或环境变量 BX_PASSWORD（明文不写入汇总与日志）", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


def main(argv: Optional[List[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    parser = argparse.ArgumentParser(
        description="批量解压（内置格式 + 可选 7-Zip；.001/.002 分卷自动归组；"
                    "加密包支持候选密码），输出统一 JSON 汇总")
    parser.add_argument("--input-dir", default="", help="输入目录（递归扫描；7z.001~7z.005 自动算一组）")
    parser.add_argument("--archives", nargs="+", default=[],
                        help="直接指定一个或多个压缩包路径；分卷只需填首卷，或把全部卷都填上（自动去重）")
    parser.add_argument("--output", default="", help="输出目录（必填，serve 模式除外）")
    parser.add_argument("--password", action="append", default=[], metavar="PWD",
                        help="解压密码，可重复指定多个候选（批次内不同包用不同密码时全部列上）；"
                             "为避免密码出现在命令行，可用环境变量 BX_PASSWORD 或 --password-file")
    parser.add_argument("--password-file", dest="password_file", default="",
                        help="密码本文件：UTF-8，一行一个密码，空行忽略；也可用 BX_PASSWORD_FILE")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在文件（默认跳过）")
    parser.add_argument("--no-archive-subdir", action="store_true",
                        help="不按包名建子目录，直接铺到输出目录")
    parser.add_argument("--7z", dest="tool", default="", help="7z / 7zz 可执行文件绝对路径")
    parser.add_argument("--pretty", action="store_true", help="缩进输出汇总 JSON")
    parser.add_argument("--serve", action="store_true", help="启动前端页面与 HTTP 接口")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)

    if args.serve:
        serve(args.host, args.port)
        return 0

    mode = "dir" if args.input_dir else "files"
    cfg = {
        "mode": mode,
        "inputDir": args.input_dir,
        "archives": args.archives,
        "outputDir": args.output,
        "overwrite": args.overwrite,
        "useArchiveSubdir": not args.no_archive_subdir,
        "externalTool": args.tool,
        "password": "",
        "passwords": args.password,
        "passwordFile": args.password_file,
    }
    summary = run_extract(cfg, emit=lambda level, text: sys.stderr.write("[%s] %s\n" % (level, text)))
    # stdout 恒为可解析的同一份汇总 JSON；提示信息全部走 stderr
    print(json.dumps(summary, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
