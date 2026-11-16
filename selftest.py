# -*- coding: utf-8 -*-
"""确定性自测：构造样例压缩包 -> 跑核心 / CLI / HTTP -> 逐项断言。

    python selftest.py
    退出码 0 = 全部通过；1 = 至少一项失败（输出会列出具体断言）。

覆盖：目录层级与相对路径、中文名、空目录、同名冲突不覆盖、损坏、加密、
      格式不支持、路径越界、后续包不中断、overwrite、无子目录模式、
      .001 分卷真实创建 / 自动归组 / 去重 / 缺卷分类、
      加密包（7z / 加密文件名 / ZipCrypto / AES ZIP / 加密分卷）真实解压、
      缺密码与错密码原因码、多候选密码 / 密码本 / 环境变量、密码脱敏、
      CLI stdout 纯 JSON、HTTP 异步任务与统一汇总 schema、前端静态 QA、
      一键启动器（bat 编码 / 解释器定位 / 端口探测 / 外部浏览器调用）。
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import shutil
import socket
from html.parser import HTMLParser
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import batch_extract as bx  # noqa: E402
import launch  # noqa: E402  一键启动器（端口探测 / 外部浏览器 / 安全停止）

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(("  PASS  " if cond else "  FAIL  ") + name + ("  | " + str(detail) if detail else ""))


def make_encrypted_zip(path: Path) -> None:
    """手工置位 general purpose bit 0，模拟未提供密码的加密 ZIP 条目。"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("secret/data.txt", "encrypted payload")
    data = bytearray(buf.getvalue())
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        pos = 0
        while True:
            i = data.find(sig, pos)
            if i < 0:
                break
            flags = int.from_bytes(data[i + off:i + off + 2], "little")
            data[i + off:i + off + 2] = (flags | 0x0001).to_bytes(2, "little")
            pos = i + 4
    path.write_bytes(bytes(data))


def build_fixture(root: Path) -> Path:
    src = root / "input"
    src.mkdir(parents=True)
    (src / "nested").mkdir()

    with zipfile.ZipFile(src / "normal.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("src/deep/a.txt", "A-CONTENT")
        z.writestr("src/deep/中文目录/说明.txt", "中文内容")
        z.writestr("空目录/", "")
    with zipfile.ZipFile(src / "second.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("src/deep/a.txt", "SECOND-CONTENT")
    with zipfile.ZipFile(src / "unsafe.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("../escape.txt", "ESCAPE")
        z.writestr("/absolute.txt", "ABS")
        z.writestr("safe/ok.txt", "OK")
    with zipfile.ZipFile(src / "nested" / "deeper.zip", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("lvl1/lvl2/lvl3/deep.bin", b"\x00\x01\x02")

    (src / "corrupt.zip").write_bytes(b"PK\x03\x04" + b"\x00" * 30)
    make_encrypted_zip(src / "encrypted.zip")
    (src / "bad.7z").write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 48)
    (src / "notes.txt").write_text("not an archive", encoding="utf-8")
    (src / "single.gz").write_bytes(gzip.compress(b"GZIP-SINGLE-FILE"))

    with tarfile.open(src / "tree.tar.gz", "w:gz") as tf:
        payload = b"TAR-CONTENT"
        info = tarfile.TarInfo("lib/x/y.txt")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    return src


def build_encrypted_fixture(root: Path, tool: str) -> Path:
    """用真实 7-Zip 造加密样本；分卷重命名成用户那种 7z.001~7z.NNN。

    单元共 7 个：enc.7z / enc_he.7z（加密文件名）/ zc.zip（ZipCrypto）/
    aes.zip（AES）/ enc_b.7z（另一个密码）/ plain.7z（明文）/ 7z.001~（加密分卷）。
    每个包内 2 个文件：data.bin 与 nested/中文.txt。
    """
    src = root / "pwd_src"
    (src / "nested").mkdir(parents=True)
    (src / "nested" / "中文.txt").write_text("PWD-中文-内容", encoding="utf-8")
    (src / "data.bin").write_bytes(os.urandom(90000))
    packs = root / "pwd_packs"
    packs.mkdir()

    def z7(args):
        return subprocess.run([tool] + args, cwd=str(root), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")

    z7(["a", "-y", "-pSECRET", str(packs / "enc.7z"), "pwd_src"])
    z7(["a", "-y", "-pSECRET", "-mhe=on", str(packs / "enc_he.7z"), "pwd_src"])
    z7(["a", "-y", "-pSECRET", "-mem=ZipCrypto", str(packs / "zc.zip"), "pwd_src"])
    z7(["a", "-y", "-pSECRET", "-mem=AES256", str(packs / "aes.zip"), "pwd_src"])
    z7(["a", "-y", "-pOTHER", str(packs / "enc_b.7z"), "pwd_src"])
    z7(["a", "-y", str(packs / "plain.7z"), "pwd_src"])
    z7(["a", "-y", "-pSECRET", "-v25k", "-mx0", str(packs / "split.7z"), "pwd_src"])
    for i, p in enumerate(sorted(packs.glob("split.7z.*")), 1):
        shutil.copy2(p, packs / ("7z.%03d" % i))
        p.unlink()  # 删掉原始命名，否则目录里会出现两组不同的分卷
    return packs


def reasons_of(summary, status):
    out = set()
    for g in summary["groups"][status]:
        out.add(g["reason"])
    return out


def failed_items(summary, reason):
    for g in summary["groups"]["failed"]:
        if g["reason"] == reason:
            return g["items"]
    return []


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_port(port, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = socket.create_connection(("127.0.0.1", port), 0.2)
            s.close()
            return True
        except OSError:
            time.sleep(0.1)
    return False


def contrast(hex_a, hex_b):
    """WCAG 2.1 相对亮度对比度，用于前端配色实测（非目测）。"""
    def lum(h):
        h = h.lstrip("#")
        ch = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        r, g, b = [f(c) for c in ch]
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    l1, l2 = lum(hex_a), lum(hex_b)
    return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)


class TagStack(HTMLParser):
    VOID = {"meta", "link", "br", "input", "img", "hr", "source", "area",
            "base", "col", "embed", "param", "track", "wbr"}

    def __init__(self):
        super().__init__()
        self.stack = []
        self.bad = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.bad.append((tag, list(self.stack[-3:])))


def main():
    # 宿主环境可能设了这两个变量；自测必须隔离，否则所有断言都会意外带上密码
    saved_env = (os.environ.pop("BX_PASSWORD", None), os.environ.pop("BX_PASSWORD_FILE", None))
    tmp = Path(tempfile.mkdtemp(prefix="bx_selftest_"))
    try:
        src = build_fixture(tmp)
        out = tmp / "output"
        out.mkdir()
        # 预置同名文件：默认不覆盖时应保持原内容并记为 skipped/conflict
        pre = out / "second" / "src" / "deep" / "a.txt"
        pre.parent.mkdir(parents=True)
        pre.write_text("PRE-EXISTING", encoding="utf-8")

        print("\n[1] 核心库 run_extract：目录模式 / 默认不覆盖")
        cfg = {"mode": "dir", "inputDir": str(src), "archives": [],
               "outputDir": str(out), "overwrite": False, "useArchiveSubdir": True,
               "externalTool": ""}
        s = bx.run_extract(cfg)
        check("schema 固定为 batch-extract-summary/v1", s["schema"] == bx.SCHEMA)
        check("counts 三项为整数", all(isinstance(s["counts"][k], int)
                                   for k in ("success", "failed", "skipped")))
        check("counts 与 items 逐项计数一致",
              s["counts"]["success"] == sum(1 for i in s["items"] if i["status"] == "success") and
              s["counts"]["failed"] == sum(1 for i in s["items"] if i["status"] == "failed") and
              s["counts"]["skipped"] == sum(1 for i in s["items"] if i["status"] == "skipped"),
              json.dumps(s["counts"], ensure_ascii=False))
        check("groups 按 reason 归类且 count 与 items 长度一致",
              all(g["count"] == len(g["items"]) for k in ("failed", "skipped") for g in s["groups"][k]))

        check("保留包内目录层级与相对路径",
              (out / "normal" / "src" / "deep" / "a.txt").read_text(encoding="utf-8") == "A-CONTENT")
        check("保留中文目录与文件名",
              (out / "normal" / "src" / "deep" / "中文目录" / "说明.txt").read_text(encoding="utf-8") == "中文内容")
        check("保留空目录", (out / "normal" / "空目录").is_dir())
        check("保留递归扫描到的子目录压缩包层级",
              (out / "deeper" / "lvl1" / "lvl2" / "lvl3" / "deep.bin").read_bytes() == b"\x00\x01\x02")
        check("tar.gz 正常解出", (out / "tree" / "lib" / "x" / "y.txt").read_bytes() == b"TAR-CONTENT")
        check("单文件 .gz 正常解出", (out / "single" / "single").read_bytes() == b"GZIP-SINGLE-FILE")
        check("unsafe 包内合法条目仍被处理（逐条目不中断）",
              (out / "unsafe" / "safe" / "ok.txt").read_text(encoding="utf-8") == "OK")

        check("默认不覆盖：预置文件内容未被改写", pre.read_text(encoding="utf-8") == "PRE-EXISTING")
        check("同名条目被跳过且记录冲突路径",
              any(i["status"] == "skipped" and i["reason"] == "conflict" and i["path"] == bx._s(pre)
                  for i in s["items"]))
        check("groups.skipped 仅含 conflict", reasons_of(s, "skipped") == {"conflict"},
              str(reasons_of(s, "skipped")))

        fr = reasons_of(s, "failed")
        check("损坏包归类 corrupt", "corrupt" in fr and
              any("corrupt.zip" in i["archive"] for i in failed_items(s, "corrupt")), str(fr))
        check("原因码表含加密细分原因码",
              all(k in bx.REASONS for k in ("encrypted", "password_required", "wrong_password")))
        check("未提供密码时加密包归类 password_required", "password_required" in fr and
              any("encrypted.zip" in i["archive"] for i in failed_items(s, "password_required")),
              str(fr))
        check("汇总含 auth 字段且未记录任何密码",
              s.get("auth") == {"provided": False, "candidates": 0,
                                "attempted": 1, "solved": 0},
              json.dumps(s.get("auth"), ensure_ascii=False))
        check("路径越界归类 unsafe_path", "unsafe_path" in fr and len(failed_items(s, "unsafe_path")) == 2,
              str(len(failed_items(s, "unsafe_path"))))
        tool = bx.find_external_tool()
        expected_7z = "unsupported" if not tool else "corrupt"
        check(".7z 归类 %s" % expected_7z, expected_7z in fr,
              "工具=%s，实际原因=%s" % (tool, str(fr)))
        check("异常包之后的包仍被处理（整体不中断）",
              (out / "tree" / "lib" / "x" / "y.txt").is_file() and
              (out / "normal" / "src" / "deep" / "a.txt").is_file() and
              s["archiveCounts"]["ok"] >= 4, json.dumps(s["archiveCounts"]))
        check("非压缩包文件被忽略", not any("notes.txt" in i["archive"] for i in s["items"]))
        check("messages 含开始/结束提示", len(s["messages"]) >= 2 and
              s["messages"][-1]["text"].startswith("全部完成"))

        print("\n[2] 覆盖策略 / 无子目录模式 / 输入校验")
        s2 = bx.run_extract(dict(cfg, overwrite=True, outputDir=str(tmp / "out_overwrite")))
        check("overwrite=True 时无 conflict 跳过", s2["counts"]["skipped"] == 0 and
              reasons_of(s2, "skipped") == set(), str(s2["counts"]))

        flat_out = tmp / "out_flat"
        s3 = bx.run_extract(dict(cfg, outputDir=str(flat_out), useArchiveSubdir=False))
        check("无子目录模式按原始相对路径落地", (flat_out / "src" / "deep" / "a.txt").is_file())
        check("跨包同名条目计入 skipped/conflict", s3["counts"]["skipped"] >= 1 and
              "conflict" in reasons_of(s3, "skipped"), str(s3["counts"]))

        s4 = bx.run_extract({"mode": "files", "archives": [], "outputDir": ""})
        check("非法配置返回统一汇总而非抛异常", s4["schema"] == bx.SCHEMA and
              "invalid_input" in reasons_of(s4, "failed"))
        missing = str(tmp / "no_such.zip")
        s5 = bx.run_extract({"mode": "files", "archives": [missing],
                             "outputDir": str(tmp / "out_missing")})
        check("显式路径不存在进入 failed(invalid_input)", "invalid_input" in reasons_of(s5, "failed"))

        # 外部工具可用时，验证 .7z 真实解压链路（不是只检查「工具存在」）
        if tool:
            payload = src / "seven_payload.txt"
            payload.write_text("SEVEN-ZIP-CONTENT", encoding="utf-8")
            arc7z = src / "real.7z"
            made = subprocess.run([tool, "a", "-y", "real.7z", "seven_payload.txt"],
                                  cwd=str(src), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
            check("7z 成功创建真实 .7z 样例包", made.returncode == 0 and arc7z.is_file(),
                  (made.stdout or made.stderr or "")[-160:])
            s7 = bx.run_extract({"mode": "files", "archives": [str(arc7z)],
                                 "outputDir": str(tmp / "out_7z"),
                                 "overwrite": False, "useArchiveSubdir": True,
                                 "externalTool": ""})
            target = tmp / "out_7z" / "real" / "seven_payload.txt"
            check("真实 .7z 经外部工具解出且内容正确",
                  target.is_file() and target.read_text(encoding="utf-8") == "SEVEN-ZIP-CONTENT" and
                  s7["counts"]["success"] == 1 and s7["counts"]["failed"] == 0,
                  json.dumps(s7["counts"], ensure_ascii=False))
        else:
            print("  SKIP  本机未检测到 7-Zip，跳过真实 .7z 解压验证（unsupported 分支已覆盖）")

        print("\n[2.5] 分卷压缩包：7z.001 / 7z.002 自动归组与完整性检查")
        vol_files = []
        check("原因码表含 missing_volume", "missing_volume" in bx.REASONS)
        check("NUM_VOLUME_RE 识别 7z.001：组名 7z / 编号 1",
              bx.parse_volume(Path("7z.001"))["stem"] == "7z" and
              bx.parse_volume(Path("7z.001"))["index"] == 1)
        check("同组不同编号共享同一分组 key",
              bx.parse_volume(Path("7z.001"))["key"] == bx.parse_volume(Path("7z.002"))["key"])
        check("不同组名不会被合并",
              bx.parse_volume(Path("a.001"))["key"] != bx.parse_volume(Path("b.001"))["key"])
        check("part9 / part10 不被编号宽度误拆成两组",
              bx.parse_volume(Path("pack.part9.rar"))["key"] ==
              bx.parse_volume(Path("pack.part10.rar"))["key"])
        check("组名推导输出子目录（7z / foo.7z→foo / a.tar.gz→a）",
              bx.stem_for_output("7z") == "7z" and
              bx.stem_for_output("foo.7z") == "foo" and
              bx.stem_for_output("a.tar.gz") == "a")
        check("detect_format 把 .001 分卷交给 7-Zip",
              bx.detect_format(Path("7z.001")) == "external" and
              bx.detect_format(Path("foo.7z.001")) == "external")

        if tool:
            vol_root = tmp / "vol_case"
            payload = vol_root / "vol_src"
            payload.mkdir(parents=True)
            (payload / "data.bin").write_bytes(os.urandom(120000))  # 不可压缩，确保切成多卷
            (payload / "nested").mkdir()
            (payload / "nested" / "中文.txt").write_text("VOL-中文-内容", encoding="utf-8")
            packs = vol_root / "packs"
            packs.mkdir()
            made = subprocess.run(
                [tool, "a", "-y", "-v30k", "-mx0", str(packs / "split.7z"), "vol_src"],
                cwd=str(vol_root), capture_output=True, text=True,
                encoding="utf-8", errors="replace")
            native = sorted(packs.glob("split.7z.*"))
            for i, p in enumerate(native, 1):
                p.rename(packs / ("7z.%03d" % i))  # 复现用户截图里的 7z.001 / 7z.002 命名
            vol_files = sorted(packs.glob("7z.*"))
            check("7-Zip 生成真实多卷并重命名为用户命名（≥2 卷）",
                  made.returncode == 0 and len(vol_files) >= 2, "卷数=%d" % len(vol_files))

            sv = bx.run_extract({"mode": "dir", "inputDir": str(packs), "archives": [],
                                 "outputDir": str(tmp / "out_vol_dir")})
            outvd = tmp / "out_vol_dir"
            check("目录模式：N 个分卷只算 1 个压缩包",
                  sv["archiveCounts"] == {"ok": 1, "partial": 0, "failed": 0} and
                  sv["counts"]["success"] == 2 and sv["counts"]["failed"] == 0,
                  json.dumps(sv["archiveCounts"], ensure_ascii=False) + " " +
                  json.dumps(sv["counts"], ensure_ascii=False))
            check("volumeSets 记录组名 / 首末卷 / 卷数 / 无缺卷",
                  len(sv["volumeSets"]) == 1 and
                  sv["volumeSets"][0]["name"] == "7z" and
                  sv["volumeSets"][0]["count"] == len(vol_files) and
                  sv["volumeSets"][0]["missing"] == [] and
                  sv["volumeSets"][0]["first"].endswith("7z.001"),
                  json.dumps(sv["volumeSets"], ensure_ascii=False))
            check("分卷内容完整解出：包内层级与中文名原样保留",
                  (outvd / "7z" / "vol_src" / "data.bin").read_bytes() ==
                  (payload / "data.bin").read_bytes() and
                  (outvd / "7z" / "vol_src" / "nested" / "中文.txt").read_text(encoding="utf-8")
                  == "VOL-中文-内容")
            check("输出子目录取组名 7z，而不是首卷名 7z.001",
                  (outvd / "7z").is_dir() and not (outvd / "7z.001").exists())

            sv2 = bx.run_extract({"mode": "files",
                                  "archives": [str(p) for p in vol_files],
                                  "outputDir": str(tmp / "out_vol_all")})
            check("多路径模式填全部卷自动去重，仍只解 1 组",
                  len(sv2["volumeSets"]) == 1 and
                  sv2["counts"]["success"] == sv["counts"]["success"] and
                  sv2["archiveCounts"]["ok"] == 1, json.dumps(sv2["counts"], ensure_ascii=False))
            sv3 = bx.run_extract({"mode": "files", "archives": [str(vol_files[-1])],
                                  "outputDir": str(tmp / "out_vol_one")})
            check("只填末卷也自动补全同目录整组并成功",
                  sv3["counts"]["success"] == sv["counts"]["success"] and
                  sv3["volumeSets"][0]["count"] == len(vol_files) and
                  sv3["archiveCounts"]["ok"] == 1, json.dumps(sv3["counts"], ensure_ascii=False))

            broken = vol_root / "broken"
            broken.mkdir()
            for p in vol_files:
                shutil.copy2(p, broken / p.name)
            shutil.copy2(src / "normal.zip", broken / "normal.zip")
            (broken / vol_files[1].name).unlink()  # 制造中间断号：缺 7z.002
            sb = bx.run_extract({"mode": "dir", "inputDir": str(broken),
                                 "outputDir": str(tmp / "out_vol_broken")})
            check("中间断号归 missing_volume，整组跳过且计入 archiveCounts.failed",
                  "missing_volume" in reasons_of(sb, "failed") and
                  sb["volumeSets"][0]["missing"] == [2] and
                  sb["archiveCounts"]["failed"] == 1,
                  json.dumps(sb["counts"], ensure_ascii=False))
            check("缺卷明细列出缺失文件名 7z.002",
                  any("7z.002" in i["detail"] for i in failed_items(sb, "missing_volume")))
            check("缺卷组不影响同目录其它包继续解压（normal.zip）",
                  (tmp / "out_vol_broken" / "normal" / "src" / "deep" / "a.txt").is_file())

            nofirst = vol_root / "nofirst"
            nofirst.mkdir()
            for p in vol_files[1:]:
                shutil.copy2(p, nofirst / p.name)
            sn = bx.run_extract({"mode": "dir", "inputDir": str(nofirst),
                                 "outputDir": str(tmp / "out_vol_nofirst")})
            check("缺首卷归 missing_volume 并提示 7z.001",
                  "missing_volume" in reasons_of(sn, "failed") and
                  any("7z.001" in i["detail"] for i in failed_items(sn, "missing_volume")))
        else:
            print("  SKIP  本机未检测到 7-Zip，跳过真实分卷解压（归组与原因码逻辑仍已断言）")

        print("\n[2.8] 加密压缩包：密码 / 候选 / 密码本 / 脱敏 / 半成品防护")
        check("7-Zip 输出分类：四种实测密码措辞均归 password",
              all(bx._classify_7z(2, t) == "password" for t in (
                  "ERROR: Wrong password : src\\data.bin",
                  "ERROR: Data Error in encrypted file. Wrong password? : src\\data.bin",
                  "ERROR: CRC Failed in encrypted file. Wrong password? : src\\data.bin",
                  "Cannot open encrypted archive. Wrong password?")))
        check("7-Zip 输出分类：无密码交互中断归 password",
              bx._classify_7z(255, "Break signaled") == "password")
        check("7-Zip 输出分类：普通损坏归 corrupt",
              bx._classify_7z(2, "ERROR: Data Error : broken.bin") == "corrupt")
        check("7-Zip 输出分类：分卷缺失归 missing_volume",
              bx._classify_7z(2, "ERROR: Missing volume : 7z.003") == "missing_volume")
        c1 = bx.PasswordContext({"password": "A", "passwords": ["A", "B"], "passwordFile": ""})
        check("候选密码去重保序且 auth 只给数量",
              c1.passwords == ["A", "B"] and c1.auth() == {
                  "provided": True, "candidates": 2, "attempted": 0, "solved": 0})
        c2 = bx.PasswordContext({"password": "SECRET"})
        scrubbed = c2.scrub("ERROR: Wrong password : src\\data.bin -pSECRET")
        check("scrub 清洗 -p 回显但不破坏报错里的文件名",
              "-pSECRET" not in scrubbed and "src\\data.bin" in scrubbed, scrubbed)
        pf = tmp / "passwords.txt"
        pf.write_text("NOPE\nSECRET\n\n", encoding="utf-8")
        c3 = bx.PasswordContext({"passwordFile": str(pf)})
        check("密码本按行读取且忽略空行",
              c3.passwords == ["NOPE", "SECRET"] and not c3.errors)
        c4 = bx.PasswordContext({"passwordFile": str(tmp / "no_such_pwd.txt")})
        check("密码本不存在 -> invalid_input，且不抛异常",
              bool(c4.errors) and c4.errors[0]["reason"] == "invalid_input")
        os.environ["BX_PASSWORD"] = "SECRET"
        try:
            c5 = bx.PasswordContext({})
        finally:
            os.environ.pop("BX_PASSWORD", None)
        check("环境变量 BX_PASSWORD 生效", c5.passwords == ["SECRET"])

        enc_packs = None
        if tool:
            enc_packs = build_encrypted_fixture(tmp, tool)
            check("真实加密样本齐备（6 个加密单元 + 1 个明文单元）",
                  len(list(enc_packs.iterdir())) >= 9 and
                  all((enc_packs / n).is_file() for n in
                      ("enc.7z", "enc_he.7z", "zc.zip", "aes.zip",
                       "enc_b.7z", "plain.7z", "7z.001")))
            check("AES ZIP 识别为 AES，ZipCrypto 不误判",
                  bx._zip_encryption_info(enc_packs / "aes.zip") == (True, True) and
                  bx._zip_encryption_info(enc_packs / "zc.zip") == (True, False) and
                  bx._zip_encryption_info(enc_packs / "plain.7z") == (False, False))

            def run_pwd(label, **kw):
                cfg = {"mode": "dir", "inputDir": str(enc_packs), "archives": [],
                       "outputDir": str(tmp / ("out_pwd_" + label)),
                       "overwrite": False, "useArchiveSubdir": True, "externalTool": ""}
                cfg.update(kw)
                return bx.run_extract(cfg), Path(cfg["outputDir"])

            def files_in(out):
                return sorted(p.relative_to(out).as_posix()
                              for p in out.rglob("*") if p.is_file())

            s_none, o_none = run_pwd("none")
            f_none = files_in(o_none)
            check("无密码：6 个加密单元失败且均归 password_required",
                  s_none["archiveCounts"] == {"ok": 1, "partial": 0, "failed": 6} and
                  reasons_of(s_none, "failed") == {"password_required"},
                  json.dumps(s_none["archiveCounts"], ensure_ascii=False))
            check("无密码：明文包照常成功，加密包不产出半成品",
                  len(f_none) == 2 and all(x.startswith("plain/") for x in f_none), str(f_none))
            check("无密码：失败明细给出填写密码指引",
                  any("填写" in i["detail"] or "--password" in i["detail"]
                      for i in failed_items(s_none, "password_required")))
            check("无密码：auth 记录需要认证 6 包、成功 0 包",
                  s_none["auth"]["attempted"] == 6 and s_none["auth"]["solved"] == 0 and
                  s_none["auth"]["provided"] is False,
                  json.dumps(s_none["auth"], ensure_ascii=False))

            s_wrong, o_wrong = run_pwd("wrong", password="NOPE")
            check("错密码：归 wrong_password 且无半成品落地",
                  reasons_of(s_wrong, "failed") == {"wrong_password"} and
                  len(files_in(o_wrong)) == 2 and
                  all(x.startswith("plain/") for x in files_in(o_wrong)),
                  str(reasons_of(s_wrong, "failed")))
            check("错密码：明细写明已尝试的候选数",
                  any("1 个候选" in i["detail"] for i in failed_items(s_wrong, "wrong_password")))

            s_right, o_right = run_pwd("right", password="SECRET")
            check("正确密码：5 个 SECRET 包成功、另一个密码的包仍失败",
                  s_right["counts"]["success"] == 12 and s_right["counts"]["failed"] == 1 and
                  "wrong_password" in reasons_of(s_right, "failed"),
                  json.dumps(s_right["counts"], ensure_ascii=False))
            check("正确密码：加密文件名 7z / AES ZIP / ZipCrypto ZIP 均解出",
                  all((o_right / n / "pwd_src" / "nested" / "中文.txt").read_text(encoding="utf-8")
                      == "PWD-中文-内容" for n in ("enc", "enc_he", "aes", "zc")))
            check("正确密码：加密分卷保持归组、包内层级与中文名完整",
                  len(s_right["volumeSets"]) == 1 and
                  (o_right / "7z" / "pwd_src" / "nested" / "中文.txt").is_file() and
                  (o_right / "7z" / "pwd_src" / "data.bin").is_file())
            check("正确密码：auth.solved=5、attempted=6",
                  s_right["auth"]["solved"] == 5 and s_right["auth"]["attempted"] == 6,
                  json.dumps(s_right["auth"], ensure_ascii=False))

            s_mix, o_mix = run_pwd("mixed", passwords=["SECRET", "OTHER"])
            check("多候选密码：批次内两种密码的包全部成功",
                  s_mix["counts"]["success"] == 14 and s_mix["counts"]["failed"] == 0 and
                  s_mix["archiveCounts"] == {"ok": 7, "partial": 0, "failed": 0},
                  json.dumps(s_mix["counts"], ensure_ascii=False))
            check("多候选密码：auth.candidates=2 / solved=6",
                  s_mix["auth"]["candidates"] == 2 and s_mix["auth"]["solved"] == 6,
                  json.dumps(s_mix["auth"], ensure_ascii=False))
            s_file, _ = run_pwd("file", passwordFile=str(pf))
            check("密码本文件生效（NOPE 失败后自动换 SECRET）",
                  s_file["counts"]["success"] == 12 and s_file["auth"]["candidates"] == 2,
                  json.dumps(s_file["counts"], ensure_ascii=False))
            check("错密码重试不把 7-Zip 写出的错数据落到输出目录",
                  not (o_wrong / "enc" / "pwd_src" / "data.bin").exists() and
                  not (o_wrong / "aes" / "pwd_src" / "data.bin").exists())
            blob = json.dumps(s_mix, ensure_ascii=False)
            check("汇总 / 明细 / messages 均不含密码明文",
                  not any(secret in blob for secret in ("SECRET", "OTHER", "NOPE")) and
                  not any(secret in m["text"] for m in s_mix["messages"]
                          for secret in ("SECRET", "OTHER", "NOPE")))
        else:
            print("  SKIP  本机未检测到 7-Zip，跳过真实加密包解压（纯函数与原因码断言仍已覆盖）")

        print("\n[3] CLI：stdout 为可解析的同一份汇总 JSON")
        cli_out = tmp / "out_cli"
        proc = subprocess.run(
            [sys.executable, str(HERE / "batch_extract.py"),
             "--archives", str(src / "normal.zip"), str(src / "tree.tar.gz"),
             "--output", str(cli_out)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            cli_json = json.loads(proc.stdout)
            ok_json = True
        except Exception as exc:
            cli_json, ok_json = {}, False
            print("        JSON 解析失败：%s / stdout=%r" % (exc, proc.stdout[:200]))
        check("CLI 退出码 0 且 stdout 纯 JSON", proc.returncode == 0 and ok_json)
        check("CLI 汇总 schema 与核心一致", cli_json.get("schema") == bx.SCHEMA)
        check("CLI 成功数 = 3（normal 2 文件 + tar 1 文件；空目录不计入文件数）",
              cli_json.get("counts", {}).get("success") == 3 and
              cli_json.get("counts", {}).get("failed") == 0,
              str(cli_json.get("counts")))
        check("CLI 提示信息走 stderr", "[info]" in (proc.stderr or ""))
        if tool and vol_files:
            vcli = subprocess.run(
                [sys.executable, str(HERE / "batch_extract.py"),
                 "--archives", str(vol_files[0]), "--output", str(tmp / "out_cli_vol")],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            try:
                vjson = json.loads(vcli.stdout)
            except Exception as exc:
                vjson = {}
                print("        分卷 CLI JSON 解析失败：%s" % exc)
            check("CLI 只填首卷即可解压整组，stdout JSON 含 volumeSets",
                  vjson.get("counts", {}).get("success") == 2 and
                  len(vjson.get("volumeSets", [])) == 1 and
                  vjson["volumeSets"][0]["count"] == len(vol_files),
                  json.dumps(vjson.get("counts", {}), ensure_ascii=False))
        if tool and enc_packs:
            pcli = subprocess.run(
                [sys.executable, str(HERE / "batch_extract.py"),
                 "--archives", str(enc_packs / "enc.7z"),
                 "--output", str(tmp / "out_cli_pwd"),
                 "--password", "SECRET", "--password", "NOPE"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            try:
                pjson = json.loads(pcli.stdout)
            except Exception as exc:
                pjson = {}
                print("        加密 CLI JSON 解析失败：%s" % exc)
            check("CLI --password 可重复指定多个候选并解开加密包",
                  pjson.get("counts", {}).get("success") == 2 and
                  pjson.get("auth", {}).get("solved") == 1 and
                  pjson.get("auth", {}).get("candidates") == 2,
                  json.dumps(pjson.get("counts", {}), ensure_ascii=False))
            check("CLI stdout 汇总不含密码明文",
                  "SECRET" not in pcli.stdout and "SECRET" not in (pcli.stderr or ""))

        print("\n[4] HTTP 服务：异步任务 / 轮询提示 / 最终汇总")
        port = free_port()
        srv = subprocess.Popen(
            [sys.executable, str(HERE / "batch_extract.py"), "--serve",
             "--host", "127.0.0.1", "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            check("服务端口就绪", wait_port(port), "port=%d" % port)
            base = "http://127.0.0.1:%d" % port
            with urllib.request.urlopen(base + "/api/meta", timeout=5) as r:
                meta = json.loads(r.read().decode("utf-8"))
            check("/api/meta 返回格式矩阵与 schema",
                  meta.get("schema") == bx.SCHEMA and len(meta.get("formats", [])) >= 4)
            check("/api/meta 格式矩阵含 .001 分卷与 missing_volume 原因码",
                  any(".001" in f.get("ext", "") for f in meta.get("formats", [])) and
                  "missing_volume" in meta.get("reasons", {}))
            check("/api/meta 声明密码能力与两个新原因码",
                  meta.get("auth", {}).get("supported") is True and
                  all(k in meta.get("reasons", {}) for k in
                      ("password_required", "wrong_password")) and
                  "password" in meta.get("auth", {}).get("fields", []))
            check("launch.probe 把本项目服务识别为 ours",
                  launch.probe(port) == (True, True))
            check("launch.listening_pids 解析出 LISTENING 进程 PID",
                  bool(launch.listening_pids(port)),
                  str(launch.listening_pids(port)))
            with urllib.request.urlopen(base + "/", timeout=5) as r:
                page = r.read().decode("utf-8")
            check("GET / 返回前端页面", "<!DOCTYPE html>" in page and "开始解压" in page)

            body = json.dumps({"mode": "files",
                               "archives": [str(src / "normal.zip"), str(src / "corrupt.zip")],
                               "outputDir": str(tmp / "out_http"),
                               "overwrite": False, "useArchiveSubdir": True,
                               "externalTool": ""}).encode("utf-8")
            req = urllib.request.Request(base + "/api/jobs", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=5) as r:
                job = json.loads(r.read().decode("utf-8"))
            jid = job.get("jobId", "")
            check("POST /api/jobs 返回 jobId", bool(jid), str(job))
            final = None
            for _ in range(60):
                with urllib.request.urlopen(base + "/api/jobs/" + jid, timeout=5) as r:
                    snap = json.loads(r.read().decode("utf-8"))
                if snap["status"] != "running":
                    final = snap
                    break
                time.sleep(0.15)
            check("轮询拿到 done 状态", bool(final) and final["status"] == "done",
                  str(final and final["status"]))
            check("轮询期间 messages 非空且带 level/text/at",
                  bool(final) and len(final["messages"]) >= 2 and
                  all(set(("level", "text", "at")) <= set(m) for m in final["messages"]))
            if final and final["summary"]:
                h = final["summary"]
                check("HTTP 汇总与 CLI 同构", set(("schema", "counts", "archiveCounts",
                                                 "groups", "items", "messages", "outputDir")) <= set(h))
                check("HTTP 汇总含 volumeSets 字段（无分卷时为空数组）",
                      isinstance(h.get("volumeSets"), list))
                check("HTTP 结果含正常包 2 个成功文件 + 损坏包 1 条 corrupt",
                      h["counts"]["success"] == 2 and h["counts"]["failed"] == 1 and
                      "corrupt" in reasons_of(h, "failed"),
                      json.dumps(h["counts"], ensure_ascii=False))
                check("HTTP 汇总含 auth（无密码时 provided=false）",
                      h.get("auth", {}).get("provided") is False and
                      h.get("auth", {}).get("candidates") == 0)

            if tool and enc_packs:
                pwd_body = json.dumps({
                    "mode": "files", "archives": [str(enc_packs / "enc.7z")],
                    "outputDir": str(tmp / "out_http_pwd"),
                    "password": "SECRET", "passwords": [], "passwordFile": "",
                    "overwrite": False, "useArchiveSubdir": True, "externalTool": "",
                }).encode("utf-8")
                preq = urllib.request.Request(base + "/api/jobs", data=pwd_body,
                                              headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(preq, timeout=5) as r:
                    pjob = json.loads(r.read().decode("utf-8"))
                pfinal = None
                for _ in range(60):
                    with urllib.request.urlopen(base + "/api/jobs/" + pjob["jobId"], timeout=5) as r:
                        psnap = json.loads(r.read().decode("utf-8"))
                    if psnap["status"] != "running":
                        pfinal = psnap
                        break
                    time.sleep(0.15)
                ph = (pfinal or {}).get("summary") or {}
                check("HTTP POST 带 password 解开加密包，auth.solved=1",
                      ph.get("counts", {}).get("success") == 2 and
                      ph.get("auth", {}).get("solved") == 1,
                      json.dumps(ph.get("counts", {}), ensure_ascii=False))
                check("HTTP 轮询响应 / 汇总均不回显密码明文",
                      "SECRET" not in json.dumps(psnap, ensure_ascii=False))

            bad_body = json.dumps({"mode": "files", "outputDir": str(tmp / "out_bad"),
                                   "passwords": "not-a-list"}).encode("utf-8")
            breq = urllib.request.Request(base + "/api/jobs", data=bad_body,
                                          headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(breq, timeout=5) as r:
                    bad_code = r.status
            except urllib.error.HTTPError as exc:
                bad_code = exc.code
            check("passwords 非数组时返回 400（字段类型校验）", bad_code == 400,
                  "HTTP %s" % bad_code)
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                srv.kill()

        print("\n[5] 前端静态 QA：JS 语法 / HTML 结构 / 对比度 / 状态与动效")
        html = (HERE / "web" / "index.html").read_text(encoding="utf-8")
        script = re.search(r"<script>([\s\S]*?)</script>", html)
        check("页面存在内嵌脚本且包含接口约定注释",
              bool(script) and "POST /api/jobs" in script.group(1) and
              "GET /api/jobs/<jobId>" in script.group(1))
        check("页面含分卷输入示例与 missing_volume 说明",
              "7z.001" in html and "missing_volume" in html and "整组跳过" in html)
        check("JS 汇总约定含 volumeSets 与分卷归组说明",
              "volumeSets" in script.group(1) and "分卷约定" in script.group(1) and
              "自动归组" in script.group(1))
        check("页面含密码输入框 / 显示切换 / 候选密码 / 密码本字段",
              all(k in html for k in ('id="password"', 'type="password"', 'id="togglePwd"',
                                      'id="passwords"', 'id="passwordFile"', 'class="note"')))
        check("页面明确密码不写入汇总 / 日志 / 本地存储",
              "本地存储" in html and "不写入" in html)
        check("JS 接口注释含三个密码字段与安全约定",
              all(k in script.group(1) for k in ("password:string", "passwords:string[]",
                                                 "passwordFile:string", "用完清空")))
        check("JS 汇总约定含 auth 字段且只给数量",
              "auth:{provided" in script.group(1) and "绝不回显明文" in script.group(1))
        check("buildPayload 传递三个密码字段且单密码不 trim",
              "password: $(\"password\").value," in script.group(1) and
              "passwords: $(\"passwords\").value" in script.group(1) and
              "passwordFile: $(\"passwordFile\").value.trim()" in script.group(1))
        check("汇总渲染含密码摘要与密码问题提示",
              "需要认证" in script.group(1) and
              '"password_required"' in script.group(1) and
              '"wrong_password"' in script.group(1) and
              ".auth-strip" in html)
        node = shutil.which("node")
        if node and script:
            js_path = tmp / "inline.js"
            js_path.write_text(script.group(1), encoding="utf-8")
            pr = subprocess.run([node, "--check", str(js_path)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
            check("内联 JS 通过 node --check 语法校验", pr.returncode == 0,
                  (pr.stderr or "").strip()[:220])
        else:
            check("Node 可用于 JS 语法校验", False, "未找到 node")

        st = TagStack()
        st.feed(html)
        check("HTML 标签正确闭合", not st.bad and not st.stack,
              "异常=%s 未闭合=%s" % (st.bad[:3], st.stack[:3]))
        check("语义地标与唯一 h1", all(("<%s" % t) in html for t in
                                   ("header", "main", "section", "footer")) and html.count("<h1") == 1)
        root = re.search(r":root\s*\{([\s\S]*?)\}", html)
        defined = set(re.findall(r"(--[\w-]+)\s*:", root.group(1))) if root else set()
        used = set(re.findall(r"var\((--[\w-]+)", html))
        check("CSS 变量全部有定义（无悬空 var）", bool(defined) and not (used - defined),
              "未定义=%s" % sorted(used - defined))
        check("零外部资源（无 Google Fonts / 无外链 CSS·JS，离线可用）",
              "fonts.googleapis.com" not in html and "fonts.gstatic.com" not in html and
              not re.search(r"<link[^>]+rel=[\"']stylesheet", html) and
              not re.search(r"<script[^>]+src=", html))
        check("可访问性规则齐备（focus-visible / reduced-motion / 响应式断点）",
              ":focus-visible" in html and "prefers-reduced-motion: reduce" in html and
              "max-width: 980px" in html and "max-width: 560px" in html)
        check("状态样式齐备（hover / disabled / empty / error / loading）",
              all(k in html for k in (":hover", ":disabled", ".summary-empty",
                                      ".form-error", ".progress.on", ".log .empty")))
        check("字体使用系统栈（Segoe UI / 微软雅黑，无网络字体依赖）",
              '"Segoe UI"' in html or "system-ui" in html or "Microsoft YaHei" in html)
        pairs = [
            ("正文 ink/surface", "#1f2329", "#ffffff", 4.5),
            ("次要文字 muted/surface", "#5d6470", "#ffffff", 4.5),
            ("成功色 ok/surface", "#1e7a48", "#ffffff", 4.5),
            ("失败色 err/surface", "#c4321f", "#ffffff", 4.5),
            ("跳过色 warn/surface", "#9a6b0f", "#ffffff", 4.5),
            ("主按钮白字/蓝底", "#ffffff", "#3a7fd5", 3.0),
            ("提示文字 ink/浅灰提示条", "#1f2329", "#f0f2f5", 4.5),
            ("次要文字 muted/浅灰提示条", "#5d6470", "#f0f2f5", 4.5),
        ]
        for name, fg, bg, minimum in pairs:
            v = contrast(fg, bg)
            check("对比度 %s = %.2f:1（阈值 %.1f）" % (name, v, minimum), v >= minimum)

        print("\n[5.5] 一键启动器：bat 编码 / 解释器定位 / 端口探测 / 外部浏览器")
        from unittest import mock
        bat_path = HERE / "start.bat"
        check("start.bat 与 launch.py 均已交付",
              bat_path.is_file() and (HERE / "launch.py").is_file())
        raw_bat = bat_path.read_bytes() if bat_path.is_file() else b""
        try:
            bat_text = raw_bat.decode("gbk")
            gbk_ok = True
        except UnicodeDecodeError:
            bat_text, gbk_ok = "", False
        check("start.bat 为 GBK + CRLF，并固定 PYTHONIOENCODING（中文 cmd 不乱码）",
              gbk_ok and b"\r\n" in raw_bat and "chcp 936" in bat_text and
              "PYTHONIOENCODING=gbk" in bat_text)
        check("bat 可编辑配置与解释器定位约定完整",
              all(k in bat_text for k in ('set "PORT=8765"', 'set "BX_BROWSER="',
                                          'set "KEEP_CONSOLE=0"', 'launch.py',
                                          'where python', 'WindowsApps', 'BX_PYTHON',
                                          'set "RC=')))
        check("bat 不依赖外部 find（避免 Git Bash 等 PATH 抢占）",
              "find /i" not in bat_text)
        compiled = subprocess.run([sys.executable, "-m", "py_compile",
                                   str(HERE / "launch.py")], capture_output=True)
        check("launch.py 通过 py_compile 语法校验", compiled.returncode == 0,
              (compiled.stderr or b"").decode("utf-8", "replace")[:160])
        check("launch 的 URL 构造与服务端一致",
              launch.url_of(8765) == "http://127.0.0.1:8765/" and
              launch.meta_url(8765) == "http://127.0.0.1:8765/api/meta" and
              launch.SCHEMA == bx.SCHEMA)
        unused = free_port()
        check("launch.probe 对空闲端口返回 (False, False)",
              launch.probe(unused, timeout=0.3) == (False, False))
        saved_browser = os.environ.pop("BX_BROWSER", None)
        try:
            with mock.patch.object(launch.subprocess, "Popen") as popen:
                ok = launch.open_external_browser("http://127.0.0.1:9/",
                                                  str(HERE / "selftest.py"))
                check("指定浏览器时 URL 作为单参数交给该 exe",
                      ok and popen.called and
                      "http://127.0.0.1:9/" in popen.call_args[0][0])
            with mock.patch.object(launch.os, "startfile", create=True) as startfile:
                ok = launch.open_external_browser("http://127.0.0.1:9/",
                                                  str(tmp / "no_such_browser.exe"))
                check("指定浏览器不存在时回退系统默认外部浏览器",
                      ok and startfile.called)
        finally:
            os.environ.pop("BX_BROWSER", None)
            if saved_browser is not None:
                os.environ["BX_BROWSER"] = saved_browser
        with mock.patch.object(launch, "probe", return_value=(True, False)):
            check("端口被其它程序占用时拒绝终止任何进程",
                  launch.stop_service(unused) == 2)
        if os.name == "nt":
            p = free_port()
            proc_bat = subprocess.run(["cmd.exe", "/c", "start.bat", "--status",
                                       "--port", str(p)], cwd=str(HERE),
                                      capture_output=True, timeout=60)
            bat_out = (proc_bat.stdout or b"").decode("gbk", errors="replace")
            check("bat 可执行、能定位 Python 且中文输出无乱码",
                  proc_bat.returncode == 0 and "[启动] Python" in bat_out and
                  "[未运行]" in bat_out and "鏈" not in bat_out, bat_out[-160:])

        print("\n[6] 汇总")
        failed = [n for n, ok, _ in RESULTS if not ok]
        print("共 %d 项断言，通过 %d，失败 %d" % (len(RESULTS), len(RESULTS) - len(failed), len(failed)))
        if failed:
            print("失败项：" + " / ".join(failed))
            return 1
        print("SELFTEST_ALL_PASS")
        return 0
    finally:
        os.environ.pop("BX_PASSWORD", None)
        os.environ.pop("BX_PASSWORD_FILE", None)
        if saved_env[0] is not None:
            os.environ["BX_PASSWORD"] = saved_env[0]
        if saved_env[1] is not None:
            os.environ["BX_PASSWORD_FILE"] = saved_env[1]
        shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
