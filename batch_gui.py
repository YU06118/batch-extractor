# -*- coding: utf-8 -*-
"""批量解压控制台 —— 桌面窗口版（tkinter）

双击运行直接弹出程序窗口；核心引擎复用 batch_extract.run_extract。
环境变量 BX_GUI_SMOKE=1 时启动后 2 秒自动退出（供自动化冒烟验证）。
"""
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any, Dict, List, Optional

if getattr(sys, "frozen", False):
    sys.path.insert(0, getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)))
import batch_extract as bx  # noqa: E402

APP_TITLE = "批量解压控制台"
LEVEL_TAGS = {
    "info": ("log_info",),
    "success": ("log_ok",),
    "warn": ("log_warn",),
    "error": ("log_err",),
}


def default_output_dir() -> str:
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "output")


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("860x680")
        self.minsize(760, 560)
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._running = False

        self._build_style()
        self._build_ui()
        self._refresh_tool_status()
        if os.environ.get("BX_GUI_SMOKE"):
            self.after(2000, self.destroy)

    # ---------------- 样式 ----------------
    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        self.configure(bg="#f5f6f8")
        style.configure("TFrame", background="#f5f6f8")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#f5f6f8", foreground="#1f2329")
        style.configure("Card.TLabel", background="#ffffff", foreground="#1f2329")
        style.configure("Muted.TLabel", background="#ffffff", foreground="#6b7280")
        style.configure("Title.TLabel", background="#ffffff", foreground="#1f2329",
                        font=("Microsoft YaHei UI", 13, "bold"))
        style.configure("Status.TLabel", background="#f5f6f8", foreground="#6b7280")
        style.configure("Card.TLabelframe", background="#ffffff", borderwidth=1)
        style.configure("Card.TLabelframe.Label", background="#ffffff",
                        foreground="#1f2329", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TCheckbutton", background="#ffffff", foreground="#1f2329")
        style.configure("TRadiobutton", background="#ffffff", foreground="#1f2329")
        style.configure("Primary.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TEntry", padding=2)

    # ---------------- 界面 ----------------
    def _card(self, parent: tk.Misc, title: str) -> ttk.LabelFrame:
        return ttk.LabelFrame(parent, text="  " + title + "  ", style="Card.TLabelframe")

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        # ---- 1. 输入 ----
        c1 = self._card(outer, "1 · 选择压缩包")
        c1.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        c1.columnconfigure(1, weight=1)
        self.mode_var = tk.StringVar(value="dir")

        r0 = ttk.Frame(c1, style="Card.TFrame")
        r0.grid(row=0, column=0, columnspan=3, sticky="ew", padx=10, pady=(8, 4))
        ttk.Radiobutton(r0, text="扫描整个文件夹（递归，含子目录）", value="dir",
                        variable=self.mode_var, command=self._on_mode).pack(side="left", padx=(0, 18))
        ttk.Radiobutton(r0, text="手动选择压缩包文件", value="files",
                        variable=self.mode_var, command=self._on_mode).pack(side="left")

        ttk.Label(c1, text="输入目录：", style="Card.TLabel").grid(
            row=1, column=0, sticky="e", padx=(10, 4), pady=4)
        self.input_var = tk.StringVar()
        self.input_entry = ttk.Entry(c1, textvariable=self.input_var)
        self.input_entry.grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(c1, text="浏览…", command=self._pick_input_dir, width=9).grid(
            row=1, column=2, padx=(6, 10), pady=4)

        self.filebar = ttk.Frame(c1, style="Card.TFrame")
        self.filebar.columnconfigure(0, weight=1)
        self.file_list: Optional[tk.Listbox] = None
        self.file_paths: List[str] = []
        self._build_file_picker(c1)

        self.hint = ttk.Label(c1, style="Muted.TLabel", text=(
            "支持 zip / tar.gz / tar.xz / gz / bz2 / tar 等；装好 7-Zip 后自动支持 "
            "7z / rar / 001 分卷。分卷（如 7z.001~005）自动算一组。"))
        self.hint.grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=(2, 8))

        # ---- 2. 输出与选项 ----
        c2 = self._card(outer, "2 · 输出与选项")
        c2.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        c2.columnconfigure(1, weight=1)

        ttk.Label(c2, text="输出目录：", style="Card.TLabel").grid(
            row=0, column=0, sticky="e", padx=(10, 4), pady=4)
        self.output_var = tk.StringVar(value=default_output_dir())
        ttk.Entry(c2, textvariable=self.output_var).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Button(c2, text="浏览…", command=self._pick_output_dir, width=9).grid(
            row=0, column=2, padx=(6, 10), pady=4)

        opt = ttk.Frame(c2, style="Card.TFrame")
        opt.grid(row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8))
        self.overwrite_var = tk.BooleanVar(value=False)
        self.subdir_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="覆盖已存在的文件（默认同名跳过）",
                        variable=self.overwrite_var).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(opt, text="按压缩包名建立子文件夹",
                        variable=self.subdir_var).pack(side="left")

        pw = ttk.Frame(c2, style="Card.TFrame")
        pw.grid(row=2, column=0, columnspan=3, sticky="ew", padx=10, pady=(0, 10))
        pw.columnconfigure(1, weight=1)
        ttk.Label(pw, text="解压密码：", style="Card.TLabel").grid(row=0, column=0, sticky="e", padx=(0, 4))
        self.password_var = tk.StringVar()
        self.pwd_entry = ttk.Entry(pw, textvariable=self.password_var, show="●")
        self.pwd_entry.grid(row=0, column=1, sticky="ew", pady=3)
        self.show_pwd = tk.BooleanVar(value=False)
        ttk.Checkbutton(pw, text="显示", variable=self.show_pwd,
                        command=self._toggle_pwd).grid(row=0, column=2, padx=(6, 0))
        ttk.Label(pw, text="候选密码（多个包密码不同时全部列出，逗号分隔）：",
                  style="Card.TLabel").grid(row=1, column=0, sticky="e", padx=(0, 4))
        self.passwords_var = tk.StringVar()
        ttk.Entry(pw, textvariable=self.passwords_var).grid(row=1, column=1, sticky="ew", pady=3)
        ttk.Label(pw, text="密码本文件：", style="Card.TLabel").grid(row=2, column=0, sticky="e", padx=(0, 4))
        self.pwdfile_var = tk.StringVar()
        ttk.Entry(pw, textvariable=self.pwdfile_var).grid(row=2, column=1, sticky="ew", pady=3)
        ttk.Button(pw, text="浏览…", command=self._pick_pwdfile, width=9).grid(
            row=2, column=2, padx=(6, 0))
        note = ttk.Label(pw, style="Muted.TLabel",
                         text="密码仅用于本次解压，不写入日志或汇总。")
        note.grid(row=3, column=1, sticky="w", pady=(2, 4))

        # ---- 3. 日志 ----
        c3 = self._card(outer, "运行日志")
        c3.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        c3.rowconfigure(0, weight=1)
        c3.columnconfigure(0, weight=1)
        self.log = ScrolledText(c3, height=14, state="disabled", wrap="word",
                                font=("Consolas", 9), bg="#ffffff", fg="#1f2329",
                                relief="flat", padx=8, pady=6)
        self.log.grid(row=0, column=0, sticky="nsew", padx=10, pady=(4, 8))
        for tag, color in (("log_info", "#1f2329"), ("log_ok", "#1e7a48"),
                           ("log_warn", "#9a6b0f"), ("log_err", "#c4321f")):
            self.log.tag_configure(tag, foreground=color)

        # ---- 4. 底部操作条 ----
        bar = ttk.Frame(outer)
        bar.grid(row=3, column=0, sticky="ew")
        bar.columnconfigure(2, weight=1)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=180)
        self.progress.grid(row=0, column=0, padx=(0, 10))
        self.status = ttk.Label(bar, text="就绪 · " + self._tool_text(), style="Status.TLabel")
        self.status.grid(row=0, column=1, sticky="w")
        self.open_btn = ttk.Button(bar, text="打开输出目录", command=self._open_output,
                                   state="disabled")
        self.open_btn.grid(row=0, column=3, padx=6)
        self.copy_btn = ttk.Button(bar, text="复制汇总", command=self._copy_summary,
                                   state="disabled")
        self.copy_btn.grid(row=0, column=4, padx=(0, 6))
        self.start_btn = ttk.Button(bar, text="开始解压", style="Primary.TButton",
                                    command=self._start)
        self.start_btn.grid(row=0, column=5)
        self.last_summary: Optional[Dict[str, Any]] = None

    def _build_file_picker(self, parent: ttk.LabelFrame) -> None:
        self.file_list = tk.Listbox(parent, height=4, selectmode="extended",
                                    relief="flat", bg="#ffffff", fg="#1f2329",
                                    highlightthickness=1,
                                    highlightbackground="#d0d4da")
        self.file_list.grid(row=2, column=0, columnspan=2, sticky="ew", padx=(10, 4), pady=4)
        btns = ttk.Frame(parent, style="Card.TFrame")
        btns.grid(row=2, column=2, sticky="ns", padx=(6, 10), pady=4)
        ttk.Button(btns, text="添加…", width=9, command=self._pick_files).pack(pady=1)
        ttk.Button(btns, text="移除选中", width=9, command=self._remove_files).pack(pady=1)
        ttk.Button(btns, text="清空", width=9, command=self._clear_files).pack(pady=1)

    # ---------------- 交互 ----------------
    def _on_mode(self) -> None:
        pass  # 两种控件常驻显示，简单直观；模式由单选按钮决定

    def _toggle_pwd(self) -> None:
        self.pwd_entry.config(show="" if self.show_pwd.get() else "●")

    def _pick_input_dir(self) -> None:
        d = filedialog.askdirectory(title="选择包含压缩包的目录")
        if d:
            self.input_var.set(os.path.normpath(d))

    def _pick_output_dir(self) -> None:
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.output_var.set(os.path.normpath(d))

    def _pick_pwdfile(self) -> None:
        f = filedialog.askopenfilename(title="选择密码本文件（UTF-8，一行一个）",
                                       filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if f:
            self.pwdfile_var.set(os.path.normpath(f))

    def _pick_files(self) -> None:
        fs = filedialog.askopenfilenames(title="选择压缩包（分卷只需选首卷或全选）")
        for f in fs:
            n = os.path.normpath(f)
            if n not in self.file_paths:
                self.file_paths.append(n)
        self._refresh_file_list()

    def _remove_files(self) -> None:
        for idx in reversed(self.file_list.curselection()):
            del self.file_paths[idx]
        self._refresh_file_list()

    def _clear_files(self) -> None:
        self.file_paths.clear()
        self._refresh_file_list()

    def _refresh_file_list(self) -> None:
        self.file_list.delete(0, "end")
        for p in self.file_paths:
            self.file_list.insert("end", p)

    def _tool_text(self) -> str:
        return ("外部工具：7-Zip 已就绪（支持 7z/rar/分卷）" if bx.find_external_tool()
                else "外部工具：未检测到 7-Zip（7z/rar/分卷将标记为不支持）")

    def _refresh_tool_status(self) -> None:
        self.status.config(text="就绪 · " + self._tool_text())

    def _open_output(self) -> None:
        d = self.output_var.get().strip()
        if d and os.path.isdir(d):
            os.startfile(d)  # noqa: S606

    def _copy_summary(self) -> None:
        if self.last_summary:
            self.clipboard_clear()
            self.clipboard_append(bx.json.dumps(self.last_summary, ensure_ascii=False, indent=2))
            self.status.config(text="汇总 JSON 已复制到剪贴板")

    def _append_log(self, level: str, text: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", text + "\n", LEVEL_TAGS.get(level, ("log_info",)))
        self.log.see("end")
        self.log.config(state="disabled")

    # ---------------- 运行 ----------------
    def _collect_cfg(self) -> Optional[Dict[str, Any]]:
        mode = self.mode_var.get()
        out = self.output_var.get().strip()
        if mode == "dir" and not self.input_var.get().strip():
            messagebox.showwarning(APP_TITLE, "请先选择输入目录。")
            return None
        if mode == "files" and not self.file_paths:
            messagebox.showwarning(APP_TITLE, "请先添加至少一个压缩包文件。")
            return None
        if not out:
            messagebox.showwarning(APP_TITLE, "请先设置输出目录。")
            return None
        passwords = [p.strip() for p in self.passwords_var.get().split(",") if p.strip()]
        cfg: Dict[str, Any] = {
            "mode": mode,
            "inputDir": self.input_var.get().strip(),
            "archives": list(self.file_paths),
            "outputDir": out,
            "overwrite": self.overwrite_var.get(),
            "useArchiveSubdir": self.subdir_var.get(),
            "externalTool": "",
            "password": self.password_var.get(),
            "passwords": passwords,
            "passwordFile": self.pwdfile_var.get().strip(),
        }
        return cfg

    def _start(self) -> None:
        if self._running:
            return
        cfg = self._collect_cfg()
        if cfg is None:
            return
        self._running = True
        self.last_summary = None
        self.start_btn.config(state="disabled", text="解压中…")
        self.open_btn.config(state="disabled")
        self.copy_btn.config(state="disabled")
        self.progress.start(12)
        self.status.config(text="正在解压…")
        self._append_log("info", "―――― 开始批量解压 ――――")
        threading.Thread(target=self._worker, args=(cfg,), daemon=True).start()
        self.after(120, self._poll)

    def _worker(self, cfg: Dict[str, Any]) -> None:
        try:
            summary = bx.run_extract(
                cfg, emit=lambda level, text: self._queue.put((level, text)))
            self._queue.put(("__done__", summary))
        except Exception as exc:  # 兜底，保证界面不卡死
            self._queue.put(("error", "程序异常：%s: %s" % (type(exc).__name__, exc)))
            self._queue.put(("__done__", None))

    def _poll(self) -> None:
        try:
            while True:
                level, payload = self._queue.get_nowait()
                if level == "__done__":
                    self._finish(payload)
                    return
                self._append_log(level, payload)
        except queue.Empty:
            pass
        if self._running:
            self.after(120, self._poll)

    def _finish(self, summary: Optional[Dict[str, Any]]) -> None:
        self._running = False
        self.progress.stop()
        self.start_btn.config(state="normal", text="开始解压")
        if summary is None:
            self.status.config(text="异常终止")
            return
        self.last_summary = summary
        c = summary["counts"]
        ac = summary["archiveCounts"]
        self._append_log("info", "―――― 汇总 ――――")
        self._append_log("info",
                         "文件条目：成功 %d，失败 %d，跳过 %d"
                         % (c["success"], c["failed"], c["skipped"]))
        self._append_log("info",
                         "压缩包：成功 %d，部分成功 %d，失败 %d"
                         % (ac["ok"], ac["partial"], ac["failed"]))
        for status, groups in summary["groups"].items():
            for g in groups:
                self._append_log("warn" if status == "skipped" else "error",
                                 "%s（%s）：%d 项" % (g["label"], g["reason"], g["count"]))
                for it in g["items"][:5]:
                    self._append_log("warn" if status == "skipped" else "error",
                                     "    %s %s" % (it["archive"], it["detail"]))
                if len(g["items"]) > 5:
                    self._append_log("warn" if status == "skipped" else "error",
                                     "    …等共 %d 项（详情见“复制汇总”导出的 JSON）" % g["count"])
        wp = next((g for g in summary["groups"]["failed"]
                   if g["reason"] == "wrong_password"), None)
        if wp:
            self._append_log("warn", "有 %d 项密码不对（已尝试全部候选密码），"
                             "请核对候选密码后重试。" % wp["count"])
        self._append_log("success", "输出目录：%s" % summary["outputDir"])
        self.open_btn.config(state="normal")
        self.copy_btn.config(state="normal")
        level, msg = (("success", "完成：成功 %d，失败 %d，跳过 %d"
                       % (c["success"], c["failed"], c["skipped"]))
                      if c["failed"] == 0 else ("warn", "完成：%d 项失败" % c["failed"]))
        self.status.config(text=msg)
        if c["failed"] or ac["failed"]:
            messagebox.showwarning(APP_TITLE, msg + "，详情见日志。")
        else:
            messagebox.showinfo(APP_TITLE, msg)


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
