"""tkinter 图形界面，调用 decoder 模块。全部界面文案为中文（FR-1.8）。"""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .decoder import DecodeError, decode_file
from .protocol import Protocol, ProtocolError, load_protocol


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("decode2csv 二进制数据解码工具")

        self.protocol: Protocol | None = None
        self.protocol_path: str | None = None
        self.data_paths: list[str] = []
        self.output_dir: str | None = None

        self.decoding = False
        self.cancel_flag = threading.Event()
        self.msg_queue: queue.Queue = queue.Queue()

        self._build_widgets()
        self._size_window()
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _size_window(self):
        """按实际内容自适应初始窗口大小，避免不同字体/DPI 下控件被裁掉。"""
        root = self.root
        root.update_idletasks()
        req_w = root.winfo_reqwidth()
        req_h = root.winfo_reqheight()
        screen_w = root.winfo_screenwidth()
        screen_h = root.winfo_screenheight()
        width = min(max(req_w, 820), screen_w - 80)
        height = min(max(req_h, 640), screen_h - 80)
        root.geometry(f"{width}x{height}")
        root.minsize(min(width, 700), min(height, 480))

    # ---------------------------------------------------------------- UI

    def _build_widgets(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=8, pady=4)
        tk.Button(top, text="选择协议文件...", command=self._choose_protocol).pack(side="left")
        self.protocol_label = tk.Label(top, text="未选择协议文件")
        self.protocol_label.pack(side="left", padx=8)

        data_frame = tk.Frame(self.root)
        data_frame.pack(fill="x", padx=8, pady=4)
        tk.Button(data_frame, text="选择数据文件...", command=self._choose_data).pack(side="left")
        self.data_label = tk.Label(data_frame, text="未选择数据文件")
        self.data_label.pack(side="left", padx=8)

        out_frame = tk.Frame(self.root)
        out_frame.pack(fill="x", padx=8, pady=4)
        tk.Button(out_frame, text="选择输出目录...", command=self._choose_output_dir).pack(side="left")
        self.output_label = tk.Label(out_frame, text="默认：与数据文件同目录")
        self.output_label.pack(side="left", padx=8)

        summary_frame = tk.LabelFrame(self.root, text="协议摘要")
        summary_frame.pack(fill="both", expand=False, padx=8, pady=4)
        self.summary_text = self._make_scrolled_text(summary_frame, height=8)

        action_frame = tk.Frame(self.root)
        action_frame.pack(fill="x", padx=8, pady=4)
        self.start_button = tk.Button(action_frame, text="开始解码", command=self._start_decode, state="disabled")
        self.start_button.pack(side="left")
        self.progress = ttk.Progressbar(action_frame, orient="horizontal", mode="determinate", length=320)
        self.progress.pack(side="left", padx=8)

        log_frame = tk.LabelFrame(self.root, text="日志")
        log_frame.pack(fill="both", expand=True, padx=8, pady=4)
        self.log_text = self._make_scrolled_text(log_frame, height=8)

    @staticmethod
    def _make_scrolled_text(parent, height: int) -> tk.Text:
        container = tk.Frame(parent)
        container.pack(fill="both", expand=True)
        scrollbar = tk.Scrollbar(container, orient="vertical")
        text = tk.Text(container, height=height, state="disabled", yscrollcommand=scrollbar.set, wrap="word")
        scrollbar.config(command=text.yview)
        text.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return text

    def _log(self, msg: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _set_summary(self, text: str):
        self.summary_text.configure(state="normal")
        self.summary_text.delete("1.0", "end")
        self.summary_text.insert("1.0", text)
        self.summary_text.configure(state="disabled")

    # ------------------------------------------------------------ 事件

    def _choose_protocol(self):
        path = filedialog.askopenfilename(
            title="选择协议配置文件",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            protocol = load_protocol(path)
        except ProtocolError as e:
            self.protocol = None
            self.protocol_path = None
            self.protocol_label.config(text="协议文件非法")
            self._set_summary(str(e))
            self._update_start_state()
            messagebox.showerror("协议错误", str(e))
            return
        self.protocol = protocol
        self.protocol_path = path
        self.protocol_label.config(text=os.path.basename(path))
        self._set_summary(self._format_summary(protocol))
        self._update_start_state()

    def _format_summary(self, protocol: Protocol) -> str:
        lines = [
            f"协议名：{protocol.name}",
            f"说明：{protocol.description or '（无）'}",
            f"字节序：{protocol.endian}",
            f"帧长：{protocol.frame_len} 字节",
            "字段列表（名称/类型/数组长度/单位）：",
        ]
        for f in protocol.fields:
            lines.append(f"  - {f.name} / {f.type} / {f.count} / {f.unit or ''}")
        return "\n".join(lines)

    def _choose_data(self):
        initialdir = None
        if self.data_paths:
            initialdir = os.path.dirname(self.data_paths[0])
        elif self.protocol_path:
            initialdir = os.path.dirname(self.protocol_path)
        paths = filedialog.askopenfilenames(
            title="选择原始数据文件（可多选，按住 Ctrl/Shift 可多选）",
            filetypes=[("常见数据文件", "*.dat *.DAT *.bin *.BIN *.raw"), ("所有文件", "*.*")],
            initialdir=initialdir,
        )
        if not paths:
            return
        self.data_paths = list(paths)
        self.data_label.config(text=f"已选择 {len(self.data_paths)} 个文件")
        self._update_start_state()

    def _choose_output_dir(self):
        initialdir = os.path.dirname(self.data_paths[0]) if self.data_paths else None
        path = filedialog.askdirectory(title="选择输出目录", initialdir=initialdir)
        if not path:
            return
        self.output_dir = path
        self.output_label.config(text=path)

    def _update_start_state(self):
        can_start = self.protocol is not None and len(self.data_paths) > 0 and not self.decoding
        self.start_button.config(state="normal" if can_start else "disabled")

    def _start_decode(self):
        if self.decoding:
            return
        self.decoding = True
        self.cancel_flag.clear()
        self.start_button.config(state="disabled")
        self.progress["value"] = 0
        threading.Thread(target=self._decode_worker, daemon=True).start()
        self.root.after(100, self._poll_queue)

    # ------------------------------------------------------- 后台线程

    def _decode_worker(self):
        protocol = self.protocol
        for data_path in self.data_paths:
            if self.cancel_flag.is_set():
                break
            if self.output_dir:
                out_name = os.path.splitext(os.path.basename(data_path))[0] + ".csv"
                out_path = os.path.join(self.output_dir, out_name)
            else:
                out_path = os.path.splitext(data_path)[0] + ".csv"

            def progress_cb(processed, total, dp=data_path):
                self.msg_queue.put(("progress", dp, processed, total))

            def cancel_cb():
                return self.cancel_flag.is_set()

            try:
                stats = decode_file(protocol, data_path, out_path, progress_cb, cancel_cb)
            except DecodeError as e:
                self.msg_queue.put(("error", data_path, str(e)))
                continue

            if self.cancel_flag.is_set():
                self.msg_queue.put(("cancelled", data_path))
                break

            self.msg_queue.put(("file_done", data_path, out_path, stats))
        self.msg_queue.put(("all_done",))

    # ------------------------------------------------------- 主线程轮询

    def _poll_queue(self):
        try:
            while True:
                item = self.msg_queue.get_nowait()
                kind = item[0]
                if kind == "progress":
                    _, _dp, processed, total = item
                    self.progress["value"] = (processed / total * 100) if total else 100
                elif kind == "error":
                    _, dp, msg = item
                    self._log(f"[错误] {dp}：{msg}")
                elif kind == "file_done":
                    _, dp, out_path, stats = item
                    self._log(f"{dp} -> {out_path}")
                    self._log("  " + stats.summary_text())
                    if stats.ok_frames == 0 and stats.checksum_fail_kept == 0:
                        self._log("  警告：未解出任何帧")
                elif kind == "cancelled":
                    _, dp = item
                    self._log(f"已取消：{dp}")
                elif kind == "all_done":
                    self._finish()
                    return
        except queue.Empty:
            pass
        if self.decoding:
            self.root.after(100, self._poll_queue)

    def _finish(self):
        self.decoding = False
        self._update_start_state()
        if not self.cancel_flag.is_set():
            messagebox.showinfo("解码完成", "全部数据文件已处理完毕，详见日志。")

    # ---------------------------------------------------------- 关闭

    def _on_close(self):
        if self.decoding:
            self.cancel_flag.set()
            self.root.after(100, self._wait_close)
        else:
            self.root.destroy()

    def _wait_close(self):
        if self.decoding:
            self.root.after(100, self._wait_close)
        else:
            self.root.destroy()


def run_gui():
    root = tk.Tk()
    App(root)
    root.mainloop()
