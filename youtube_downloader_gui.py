import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk


def get_resource_base_dir():
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def get_runtime_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def maybe_run_embedded_yt_dlp():
    if len(sys.argv) > 1 and sys.argv[1] == "--yt-dlp-subprocess":
        import yt_dlp

        raise SystemExit(yt_dlp.main(sys.argv[2:]))


APP_TITLE = "YouTube Bulk Downloader"
RESOURCE_BASE_DIR = get_resource_base_dir()
RUNTIME_BASE_DIR = get_runtime_base_dir()
DEFAULT_OUTPUT_DIR = RUNTIME_BASE_DIR / "downloads"
LOCAL_FFMPEG_DIR = RESOURCE_BASE_DIR / "ffmpeg" / "bin"
BG_COLOR = "#f4efe8"
CARD_COLOR = "#fffaf5"
ACCENT_COLOR = "#c96d28"
ACCENT_HOVER = "#ad5b1f"
TEXT_COLOR = "#2b2119"
MUTED_COLOR = "#6d5b50"
NOTICE_BG = "#f6e4d6"
HEADER_BG = "#f1e2d5"
ROW_ALT_BG = "#fff7f0"
STAT_BG = "#f8efe7"
QUALITY_OPTIONS = [
    "Highest Quality",
    "144p",
    "240p",
    "360p",
    "480p",
    "720p",
    "1080p",
    "2K (1440p)",
]
THREAD_OPTIONS = [str(value) for value in range(1, 31)]
QUALITY_HEIGHTS = {
    "Highest Quality": None,
    "144p": 144,
    "240p": 240,
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
    "2K (1440p)": 1440,
}


def get_subprocess_window_args():
    if sys.platform != "win32":
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    return {
        "startupinfo": startupinfo,
        "creationflags": subprocess.CREATE_NO_WINDOW,
    }


def format_bytes(value):
    if not value:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024

    return f"{size:.1f} TB"


def format_eta(seconds):
    if seconds is None:
        return "--:--"

    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_speed(bytes_per_second):
    if bytes_per_second is None:
        return "--"
    return f"{format_bytes(bytes_per_second)}/s"


def parse_cli_number(value):
    if value in (None, "", "NA"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def get_ffmpeg_location():
    local_binary = LOCAL_FFMPEG_DIR / "ffmpeg.exe"
    if local_binary.exists():
        return str(LOCAL_FFMPEG_DIR)

    system_binary = shutil.which("ffmpeg")
    if system_binary:
        return str(Path(system_binary).parent)

    return None


def get_selected_height(quality_label):
    return QUALITY_HEIGHTS.get(quality_label, None)


class GuiLogger:
    def __init__(self, app):
        self.app = app

    def debug(self, msg):
        text = msg.strip()
        if text and not text.startswith("[debug]"):
            self.app.enqueue("log", text)

    def warning(self, msg):
        self.app.enqueue("log", f"Warning: {msg.strip()}")

    def error(self, msg):
        self.app.enqueue("log", f"Error: {msg.strip()}")


class DownloadRow:
    def __init__(self, parent, download_id, title):
        row_color = ROW_ALT_BG if download_id % 2 else "#fffdfb"
        self.frame = ctk.CTkFrame(
            parent,
            fg_color=row_color,
            corner_radius=0,
            border_width=2,
            border_color="#ecd8c7",
        )
        self.frame.grid_columnconfigure(1, weight=1)

        self.id_label = ctk.CTkLabel(
            self.frame,
            text=f"{download_id:02d}",
            width=70,
            anchor="w",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Consolas", size=14),
        )
        self.id_label.grid(row=0, column=0, sticky="w", padx=(14, 10), pady=12)

        self.title_label = ctk.CTkLabel(
            self.frame,
            text=title,
            anchor="w",
            justify="left",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Segoe UI", size=14),
        )
        self.title_label.grid(row=0, column=1, sticky="ew", padx=(0, 12), pady=12)

        progress_cell = ctk.CTkFrame(self.frame, fg_color=row_color)
        progress_cell.grid(row=0, column=2, sticky="ew", padx=(0, 14), pady=10)
        progress_cell.grid_columnconfigure(0, weight=1)

        self.progress_bar = ctk.CTkProgressBar(
            progress_cell,
            height=14,
            corner_radius=0,
            progress_color=ACCENT_COLOR,
            fg_color="#ead6c6",
            border_width=1,
        )
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        self.progress_bar.set(0)

        self.status_label = ctk.CTkLabel(
            progress_cell,
            text="Queued",
            anchor="w",
            justify="left",
            text_color=MUTED_COLOR,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.status_label.grid(row=1, column=0, sticky="w", pady=(6, 0))

    def update(self, *, title=None, fraction=None, status=None):
        if title is not None:
            self.title_label.configure(text=title)
        if fraction is not None:
            self.progress_bar.set(max(0, min(1, fraction)))
        if status is not None:
            self.status_label.configure(text=status)


class DownloaderApp:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x780")
        self.root.minsize(980, 700)
        self.root.configure(fg_color=BG_COLOR)

        self.events = queue.Queue()
        self.download_thread = None
        self.row_widgets = {}
        self.total_downloads = 0
        self.completed_downloads = 0
        self.success_count = 0
        self.failed_count = 0
        self.active_download_id = None
        self.active_title = ""
        self.current_quality = "Highest Quality"
        self.current_threads = "5"
        self.current_process = None
        self.current_ps_process = None
        self.process_lock = threading.Lock()
        self.stop_requested = False
        self.is_paused = False
        self.psutil = None
        self.net_last_bytes = None
        self.net_last_time = None
        self.last_gpu_poll = 0
        self.cached_gpu_text = "N/A"

        self.output_dir = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.quality_var = tk.StringVar(value="Highest Quality")
        self.thread_var = tk.StringVar(value="5")
        self.status_text = tk.StringVar(value="Paste links, choose a quality, and start downloading.")
        self.notice_text = tk.StringVar()
        self.cpu_text = tk.StringVar(value="CPU\n--")
        self.ram_text = tk.StringVar(value="RAM\n--")
        self.gpu_text = tk.StringVar(value="GPU\nN/A")
        self.internet_text = tk.StringVar(value="Internet\n--")
        self.success_text = tk.StringVar(value="Success: 0")
        self.failed_text = tk.StringVar(value="Failed: 0")

        self.build_ui()
        self.setup_system_monitoring()
        self.update_notice()
        self.root.after(100, self.process_events)
        self.root.after(1000, self.refresh_system_stats)

    def build_ui(self):
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        outer = ctk.CTkFrame(self.root, fg_color=BG_COLOR, corner_radius=0)
        outer.grid(row=0, column=0, sticky="nsew")
        outer.grid_columnconfigure(0, weight=1)
        outer.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(outer, fg_color=BG_COLOR)
        header.grid(row=0, column=0, sticky="ew", padx=26, pady=(24, 12))
        header.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            header,
            text=APP_TITLE,
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=30, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="Devoloped By - @nill404\nGITHUB - N1LL404",
            text_color=MUTED_COLOR,
            font=ctk.CTkFont(family="Segoe UI", size=15),
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        top = ctk.CTkFrame(outer, fg_color=BG_COLOR)
        top.grid(row=1, column=0, sticky="ew", padx=26, pady=(0, 16))
        top.grid_columnconfigure(0, weight=3)
        top.grid_columnconfigure(1, weight=2)

        input_card = ctk.CTkFrame(top, fg_color=CARD_COLOR, corner_radius=0, border_width=1, border_color="#eadccf")
        input_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        input_card.grid_columnconfigure(0, weight=1)
        input_card.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            input_card,
            text="Video Links",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=18, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 8))

        self.url_input = ctk.CTkTextbox(
            input_card,
            corner_radius=0,
            border_width=1,
            border_color="#e5d3c4",
            fg_color="#fffdfb",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Consolas", size=14),
            scrollbar_button_color="#d4b59d",
            scrollbar_button_hover_color="#be9879",
            height=220,
        )
        self.url_input.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 18))
        self.url_input.insert("1.0", "")

        controls_card = ctk.CTkFrame(top, fg_color=CARD_COLOR, corner_radius=0, border_width=1, border_color="#eadccf")
        controls_card.grid(row=0, column=1, sticky="nsew")
        controls_card.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            controls_card,
            text="Download Settings",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=18, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 8))

        path_row = ctk.CTkFrame(controls_card, fg_color=CARD_COLOR)
        path_row.grid(row=1, column=0, sticky="ew", padx=20)
        path_row.grid_columnconfigure(0, weight=1)

        self.path_entry = ctk.CTkEntry(
            path_row,
            textvariable=self.output_dir,
            height=42,
            corner_radius=14,
            border_width=1,
            border_color="#e5d3c4",
            fg_color="#fffdfb",
            text_color=TEXT_COLOR,
        )
        self.path_entry.grid(row=0, column=0, sticky="ew")

        ctk.CTkButton(
            path_row,
            text="Browse",
            width=108,
            height=42,
            corner_radius=14,
            fg_color="#f0d0b8",
            hover_color="#e5c1a5",
            text_color=TEXT_COLOR,
            command=self.choose_folder,
        ).grid(row=0, column=1, padx=(10, 0))

        quality_row = ctk.CTkFrame(controls_card, fg_color=CARD_COLOR)
        quality_row.grid(row=2, column=0, sticky="ew", padx=20, pady=(12, 0))
        quality_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            quality_row,
            text="Quality",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))

        self.quality_menu = ctk.CTkOptionMenu(
            quality_row,
            values=QUALITY_OPTIONS,
            variable=self.quality_var,
            command=lambda _value: self.update_notice(),
            height=38,
            corner_radius=12,
            fg_color="#f0d0b8",
            button_color=ACCENT_COLOR,
            button_hover_color=ACCENT_HOVER,
            text_color=TEXT_COLOR,
            dropdown_fg_color="#fffdfb",
            dropdown_hover_color="#f5e7db",
            dropdown_text_color=TEXT_COLOR,
        )
        self.quality_menu.grid(row=0, column=1, sticky="ew")

        thread_row = ctk.CTkFrame(controls_card, fg_color=CARD_COLOR)
        thread_row.grid(row=3, column=0, sticky="ew", padx=20, pady=(12, 0))
        thread_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            thread_row,
            text="Threads",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=14, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))

        self.thread_menu = ctk.CTkOptionMenu(
            thread_row,
            values=THREAD_OPTIONS,
            variable=self.thread_var,
            command=lambda _value: self.update_notice(),
            height=38,
            corner_radius=12,
            fg_color="#f0d0b8",
            button_color=ACCENT_COLOR,
            button_hover_color=ACCENT_HOVER,
            text_color=TEXT_COLOR,
            dropdown_fg_color="#fffdfb",
            dropdown_hover_color="#f5e7db",
            dropdown_text_color=TEXT_COLOR,
        )
        self.thread_menu.grid(row=0, column=1, sticky="ew")

        stats_row = ctk.CTkFrame(controls_card, fg_color=CARD_COLOR)
        stats_row.grid(row=4, column=0, sticky="ew", padx=20, pady=(18, 10))
        stats_row.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.cpu_box = self.build_stat_box(stats_row, 0, self.cpu_text)
        self.ram_box = self.build_stat_box(stats_row, 1, self.ram_text)
        self.gpu_box = self.build_stat_box(stats_row, 2, self.gpu_text)
        self.internet_box = self.build_stat_box(stats_row, 3, self.internet_text)

        button_row = ctk.CTkFrame(controls_card, fg_color=CARD_COLOR)
        button_row.grid(row=5, column=0, sticky="ew", padx=20, pady=(6, 12))
        button_row.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self.download_button = ctk.CTkButton(
            button_row,
            text="Download Video",
            height=48,
            corner_radius=16,
            fg_color=ACCENT_COLOR,
            hover_color=ACCENT_HOVER,
            text_color="#fffaf5",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=15, weight="bold"),
            command=self.start_download,
        )
        self.download_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.pause_button = ctk.CTkButton(
            button_row,
            text="Pause",
            height=48,
            corner_radius=16,
            fg_color="#d98d43",
            hover_color="#c67b33",
            text_color="#fffaf5",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=15, weight="bold"),
            command=self.toggle_pause,
            state="disabled",
        )
        self.pause_button.grid(row=0, column=1, sticky="ew", padx=(8, 8))

        self.stop_button = ctk.CTkButton(
            button_row,
            text="Stop",
            height=48,
            corner_radius=16,
            fg_color="#b84f3a",
            hover_color="#9f412f",
            text_color="#fffaf5",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=15, weight="bold"),
            command=self.stop_download,
            state="disabled",
        )
        self.stop_button.grid(row=0, column=2, sticky="ew", padx=(8, 8))

        ctk.CTkButton(
            button_row,
            text="Clear",
            height=48,
            corner_radius=16,
            fg_color="#efe2d4",
            hover_color="#e3d0bc",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=15, weight="bold"),
            command=self.clear_links,
        ).grid(row=0, column=3, sticky="ew", padx=(8, 0))

        self.compact_status = ctk.CTkLabel(
            controls_card,
            textvariable=self.status_text,
            justify="left",
            wraplength=360,
            text_color=MUTED_COLOR,
            font=ctk.CTkFont(family="Segoe UI", size=12),
        )
        self.compact_status.grid(row=6, column=0, sticky="ew", padx=20, pady=(0, 8))

        self.compact_notice = ctk.CTkLabel(
            controls_card,
            textvariable=self.notice_text,
            justify="left",
            wraplength=360,
            text_color=MUTED_COLOR,
            font=ctk.CTkFont(family="Segoe UI", size=11),
        )
        self.compact_notice.grid(row=7, column=0, sticky="ew", padx=20, pady=(0, 18))

        sheet_card = ctk.CTkFrame(outer, fg_color=CARD_COLOR, corner_radius=0, border_width=2, border_color="#eadccf")
        sheet_card.grid(row=2, column=0, sticky="nsew", padx=26, pady=(0, 24))
        sheet_card.grid_columnconfigure(0, weight=1)
        sheet_card.grid_rowconfigure(2, weight=1)

        ctk.CTkLabel(
            sheet_card,
            text="Downloads Sheet",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=18, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=20, pady=(18, 8))

        header_row = ctk.CTkFrame(sheet_card, fg_color=HEADER_BG, corner_radius=0, border_width=2, border_color="#e3cfbe")
        header_row.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        header_row.grid_columnconfigure(1, weight=1)
        header_row.grid_columnconfigure(2, weight=2)
        header_row.grid_columnconfigure(3, weight=0)
        header_row.grid_columnconfigure(4, weight=0)

        ctk.CTkLabel(
            header_row,
            text="ID",
            text_color=TEXT_COLOR,
            anchor="w",
            width=70,
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
        ).grid(row=0, column=0, sticky="w", padx=(14, 10), pady=10)

        ctk.CTkLabel(
            header_row,
            text="TITLE",
            text_color=TEXT_COLOR,
            anchor="w",
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
        ).grid(row=0, column=1, sticky="w", padx=(0, 12), pady=10)

        ctk.CTkLabel(
            header_row,
            text="PROGRESS",
            text_color=TEXT_COLOR,
            anchor="w",
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
        ).grid(row=0, column=2, sticky="w", padx=(0, 14), pady=10)

        ctk.CTkLabel(
            header_row,
            textvariable=self.success_text,
            text_color="#2f8f3e",
            anchor="e",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=13, weight="bold"),
        ).grid(row=0, column=3, sticky="e", padx=(0, 24), pady=10)

        ctk.CTkLabel(
            header_row,
            textvariable=self.failed_text,
            text_color="#b84f3a",
            anchor="e",
            font=ctk.CTkFont(family="Segoe UI Semibold", size=13, weight="bold"),
        ).grid(row=0, column=4, sticky="e", padx=(0, 18), pady=10)

        self.sheet_body = ctk.CTkScrollableFrame(
            sheet_card,
            fg_color="#fffdfb",
            corner_radius=0,
            border_width=2,
            border_color="#e5d3c4",
            scrollbar_button_color="#d4b59d",
            scrollbar_button_hover_color="#be9879",
        )
        self.sheet_body.grid(row=2, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.sheet_body.grid_columnconfigure(0, weight=1)

    def build_stat_box(self, parent, column, variable):
        box = ctk.CTkFrame(
            parent,
            fg_color=STAT_BG,
            corner_radius=0,
            border_width=2,
            border_color="#e3cfbe",
        )
        box.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 6, 0), pady=0)
        ctk.CTkLabel(
            box,
            textvariable=variable,
            justify="center",
            text_color=TEXT_COLOR,
            font=ctk.CTkFont(family="Segoe UI Semibold", size=13, weight="bold"),
            height=68,
        ).pack(fill="both", expand=True, padx=8, pady=8)
        return box

    def setup_system_monitoring(self):
        try:
            import psutil

            self.psutil = psutil
            counters = psutil.net_io_counters()
            self.net_last_bytes = counters.bytes_recv + counters.bytes_sent
            self.net_last_time = time.time()
            psutil.cpu_percent(interval=None)
        except Exception:
            self.psutil = None

    def get_gpu_usage_text(self):
        now = time.time()
        if now - self.last_gpu_poll < 2:
            return self.cached_gpu_text

        self.last_gpu_poll = now
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
                **get_subprocess_window_args(),
            )
            if result.returncode == 0:
                values = [line.strip() for line in result.stdout.splitlines() if line.strip()]
                if values:
                    self.cached_gpu_text = f"{values[0]}%"
                    return self.cached_gpu_text
        except Exception:
            pass

        self.cached_gpu_text = "N/A"
        return self.cached_gpu_text

    def refresh_system_stats(self):
        if self.psutil:
            try:
                cpu_value = self.psutil.cpu_percent(interval=None)
                self.cpu_text.set(f"CPU\n{cpu_value:.0f}%")
            except Exception:
                self.cpu_text.set("CPU\n--")

            try:
                ram_value = self.psutil.virtual_memory().percent
                self.ram_text.set(f"RAM\n{ram_value:.0f}%")
            except Exception:
                self.ram_text.set("RAM\n--")

            try:
                counters = self.psutil.net_io_counters()
                current_bytes = counters.bytes_recv + counters.bytes_sent
                current_time = time.time()
                if self.net_last_bytes is not None and self.net_last_time is not None:
                    elapsed = max(current_time - self.net_last_time, 0.1)
                    speed = (current_bytes - self.net_last_bytes) / elapsed
                    self.internet_text.set(f"Internet\n{format_speed(speed)}")
                else:
                    self.internet_text.set("Internet\n--")
                self.net_last_bytes = current_bytes
                self.net_last_time = current_time
            except Exception:
                self.internet_text.set("Internet\n--")
        else:
            self.cpu_text.set("CPU\n--")
            self.ram_text.set("RAM\n--")
            self.internet_text.set("Internet\n--")

        self.gpu_text.set(f"GPU\n{self.get_gpu_usage_text()}")
        self.root.after(1000, self.refresh_system_stats)

    def update_notice(self):
        ffmpeg_location = get_ffmpeg_location()
        selected_quality = self.quality_var.get()
        selected_threads = self.thread_var.get()
        if ffmpeg_location:
            self.notice_text.set(
                f"Quality: {selected_quality}  |  Threads: {selected_threads}  |  ffmpeg: {ffmpeg_location}"
            )
        else:
            self.notice_text.set(
                f"Quality: {selected_quality}  |  Threads: {selected_threads}  |  ffmpeg not found. Merging may fail."
            )

    def choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self.output_dir.get() or str(DEFAULT_OUTPUT_DIR))
        if folder:
            self.output_dir.set(folder)

    def reset_counters(self):
        self.success_count = 0
        self.failed_count = 0
        self.success_text.set("Success: 0")
        self.failed_text.set("Failed: 0")

    def update_counter_labels(self):
        self.success_text.set(f"Success: {self.success_count}")
        self.failed_text.set(f"Failed: {self.failed_count}")

    def clear_links(self):
        if self.download_thread and self.download_thread.is_alive():
            messagebox.showwarning(APP_TITLE, "Stop the current download before clearing the list.")
            return
        self.url_input.delete("1.0", "end")
        self.total_downloads = 0
        self.completed_downloads = 0
        self.reset_counters()
        self.status_text.set("Input cleared. Paste new links when you're ready.")
        self.reset_sheet(0)

    def build_sheet_rows(self, row_count):
        for download_id in range(1, row_count + 1):
            row = DownloadRow(self.sheet_body, download_id, "")
            row.frame.grid(row=download_id - 1, column=0, sticky="ew", padx=10, pady=0)
            row.update(status="Idle")
            self.row_widgets[download_id] = row

    def reset_sheet(self, row_count):
        for row in self.row_widgets.values():
            row.frame.destroy()
        self.row_widgets.clear()
        self.build_sheet_rows(row_count)

    def enqueue(self, event_type, payload):
        self.events.put((event_type, payload))

    def set_busy(self, busy):
        self.download_button.configure(state="disabled" if busy else "normal")
        self.pause_button.configure(state="normal" if busy else "disabled")
        self.stop_button.configure(state="normal" if busy else "disabled")
        if not busy:
            self.is_paused = False
            self.pause_button.configure(text="Pause")

    def set_current_process(self, process):
        with self.process_lock:
            self.current_process = process
            if self.psutil and process is not None:
                try:
                    self.current_ps_process = self.psutil.Process(process.pid)
                except Exception:
                    self.current_ps_process = None
            else:
                self.current_ps_process = None

    def get_process_tree(self):
        with self.process_lock:
            root = self.current_ps_process
        if not root:
            return []
        try:
            children = root.children(recursive=True)
        except Exception:
            children = []
        return children + [root]

    def toggle_pause(self):
        processes = self.get_process_tree()
        if not processes:
            return

        try:
            if self.is_paused:
                for proc in reversed(processes):
                    proc.resume()
                self.is_paused = False
                self.pause_button.configure(text="Pause")
                self.enqueue("status", f"Resumed {self.active_title or 'download'}")
            else:
                for proc in processes:
                    proc.suspend()
                self.is_paused = True
                self.pause_button.configure(text="Resume")
                self.enqueue("status", f"Paused {self.active_title or 'download'}")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Pause/resume failed: {exc}")

    def stop_download(self):
        self.stop_requested = True
        processes = self.get_process_tree()
        for proc in processes:
            try:
                proc.resume()
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass
        self.enqueue("status", "Stopping current download...")

    def process_events(self):
        while not self.events.empty():
            event_type, payload = self.events.get()

            if event_type == "status":
                self.status_text.set(payload)
            elif event_type == "sheet_add":
                self.add_sheet_row(payload["download_id"], payload["title"])
            elif event_type == "sheet_update":
                self.update_sheet_row(payload)
            elif event_type == "counts":
                self.success_count = payload["success"]
                self.failed_count = payload["failed"]
                self.update_counter_labels()
            elif event_type == "log":
                pass
            elif event_type == "done":
                self.set_busy(False)
                self.set_current_process(None)
                self.active_download_id = None
                self.active_title = ""
                success, message = payload
                self.status_text.set(message)
                if success:
                    messagebox.showinfo(APP_TITLE, message)
                elif success is False:
                    messagebox.showerror(APP_TITLE, message)

        self.root.after(100, self.process_events)

    def add_sheet_row(self, download_id, title):
        row = self.row_widgets.get(download_id)
        if not row:
            return
        row.update(title=title, fraction=0, status="Queued")

    def update_sheet_row(self, payload):
        row = self.row_widgets.get(payload["download_id"])
        if not row:
            return
        row.update(
            title=payload.get("title"),
            fraction=payload.get("fraction"),
            status=payload.get("status"),
        )

    def get_urls(self):
        raw_text = self.url_input.get("1.0", "end").strip()
        sample_lines = {
            "https://www.youtube.com/watch?v=...",
            "https://youtu.be/...",
        }
        return [line.strip() for line in raw_text.splitlines() if line.strip() and line.strip() not in sample_lines]

    def build_format_selector(self, selected_quality):
        target_height = get_selected_height(selected_quality)
        if target_height is None:
            return (
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/"
                "bestvideo+bestaudio/"
                "best[ext=mp4]/best"
            )

        return (
            f"bestvideo[height<={target_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={target_height}]+bestaudio/"
            f"best[height<={target_height}][ext=mp4]/best[height<={target_height}]/best"
        )

    def build_cli_command(self, output_dir, selected_quality, selected_threads, url, download_index):
        ffmpeg_location = get_ffmpeg_location()
        output_template = str(
            output_dir / f"%(title)s [%(view_count|NA)s] [{download_index:02d}].%(ext)s"
        )
        yt_dlp_args = [
            "--newline",
            "--progress",
            "--progress-template",
            "download:__PROGRESS__|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|%(progress.total_bytes_estimate)s|%(progress.eta)s|%(progress.speed)s",
            "-f",
            self.build_format_selector(selected_quality),
            "-o",
            output_template,
            "--windows-filenames",
            "--retries",
            "10",
            "--fragment-retries",
            "10",
            "--concurrent-fragments",
            selected_threads,
            "--no-playlist",
            url,
        ]

        if getattr(sys, "frozen", False):
            command = [sys.executable, "--yt-dlp-subprocess", *yt_dlp_args]
        else:
            command = [sys.executable, "-m", "yt_dlp", *yt_dlp_args]

        if ffmpeg_location:
            command.extend(["--ffmpeg-location", ffmpeg_location, "--merge-output-format", "mp4"])

        return command

    def publish_counts(self):
        self.enqueue(
            "counts",
            {
                "success": self.success_count,
                "failed": self.failed_count,
            },
        )

    def update_batch_progress(self, completed, current_fraction):
        if not self.total_downloads:
            return
        overall = (completed + current_fraction) / self.total_downloads
        self.enqueue(
            "status",
            f"Batch {overall * 100:.1f}% complete  |  {completed}/{self.total_downloads} finished",
        )

    def progress_hook(self, data):
        if self.active_download_id is None:
            return

        filename = Path(data.get("filename", "")).name if data.get("filename") else self.active_title or "video"
        status = data.get("status")

        if status == "downloading":
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes", 0)
            fraction = (downloaded / total) if total else 0
            speed = format_bytes(data.get("speed"))
            eta = format_eta(data.get("eta"))
            status_text = f"{fraction * 100:.1f}%  |  {speed}/s  |  ETA {eta}"
            self.update_batch_progress(self.completed_downloads, fraction)

            self.enqueue(
                "sheet_update",
                {
                    "download_id": self.active_download_id,
                    "title": self.active_title or filename,
                    "fraction": fraction,
                    "status": status_text,
                },
            )
        elif status == "finished":
            self.enqueue(
                "sheet_update",
                {
                    "download_id": self.active_download_id,
                    "title": self.active_title or filename,
                    "fraction": 1,
                    "status": "Download complete. Finalizing file...",
                },
            )

    def update_row_progress_from_cli(self, line):
        if self.active_download_id is None:
            return

        if not line.startswith("__PROGRESS__|"):
            return

        _, downloaded_raw, total_raw, total_estimate_raw, eta_raw, speed_raw = line.split("|", 5)
        downloaded = parse_cli_number(downloaded_raw) or 0
        total = parse_cli_number(total_raw) or parse_cli_number(total_estimate_raw)
        fraction = (downloaded / total) if total else 0
        speed = parse_cli_number(speed_raw)
        eta = parse_cli_number(eta_raw)
        status_text = f"{fraction * 100:.1f}%  |  {format_speed(speed)}  |  ETA {format_eta(eta)}"

        self.update_batch_progress(self.completed_downloads, fraction)
        self.enqueue(
            "sheet_update",
            {
                "download_id": self.active_download_id,
                "title": self.active_title,
                "fraction": fraction,
                "status": status_text,
            },
        )

    def start_download(self):
        urls = self.get_urls()
        if not urls:
            messagebox.showwarning(APP_TITLE, "Please paste at least one YouTube link.")
            return

        output_dir = Path(self.output_dir.get()).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)

        self.total_downloads = len(urls)
        self.completed_downloads = 0
        self.reset_counters()
        self.publish_counts()
        self.stop_requested = False
        self.is_paused = False
        self.pause_button.configure(text="Pause")
        self.current_quality = self.quality_var.get()
        self.current_threads = self.thread_var.get()
        self.reset_sheet(self.total_downloads)
        self.set_busy(True)
        self.update_notice()
        self.status_text.set(
            f"Preparing metadata for {self.current_quality} downloads with {self.current_threads} threads..."
        )

        self.download_thread = threading.Thread(
            target=self.download_worker,
            args=(urls, output_dir, self.current_quality, self.current_threads),
            daemon=True,
        )
        self.download_thread.start()

    def download_worker(self, urls, output_dir, selected_quality, selected_threads):
        try:
            import yt_dlp
        except ImportError:
            self.enqueue(
                "done",
                (
                    False,
                    "yt-dlp is not installed for this Python environment. Install it with: pip install -r requirements.txt",
                ),
            )
            return

        completed = 0
        has_ffmpeg = get_ffmpeg_location() is not None
        titles_by_id = {}

        try:
            info_opts = {"quiet": True, "noplaylist": True, "logger": GuiLogger(self)}
            with yt_dlp.YoutubeDL(info_opts) as info_ydl:
                for index, url in enumerate(urls, start=1):
                    title = url
                    try:
                        info = info_ydl.extract_info(url, download=False)
                        title = info.get("title") or url
                    except Exception:
                        title = url

                    titles_by_id[index] = title
                    self.enqueue("sheet_add", {"download_id": index, "title": title})
                    self.enqueue(
                        "sheet_update",
                        {
                            "download_id": index,
                            "title": title,
                            "fraction": 0,
                            "status": "Queued",
                        },
                    )

            if not has_ffmpeg:
                self.enqueue(
                    "status",
                    f"ffmpeg is missing. The app will try the best progressive stream for {selected_quality}, but merging may fail.",
                )

            for index, url in enumerate(urls, start=1):
                if self.stop_requested:
                    self.enqueue(
                        "done",
                        (None, f"Download stopped. Success: {self.success_count}, Failed: {self.failed_count}"),
                    )
                    return

                title = titles_by_id.get(index, url)
                self.active_download_id = index
                self.active_title = title
                self.enqueue("status", f"Downloading {title}")
                self.enqueue(
                    "sheet_update",
                    {
                        "download_id": index,
                        "title": title,
                        "fraction": 0,
                        "status": "Starting...",
                    },
                )

                download_succeeded = False
                for attempt in range(1, 3):
                    if attempt == 2:
                        self.enqueue(
                            "sheet_update",
                            {
                                "download_id": index,
                                "title": title,
                                "status": "Retrying (2/2)...",
                            },
                        )

                    command = self.build_cli_command(output_dir, selected_quality, selected_threads, url, index)
                    process = subprocess.Popen(
                        command,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        bufsize=1,
                        **get_subprocess_window_args(),
                    )
                    self.set_current_process(process)

                    if process.stdout:
                        for raw_line in process.stdout:
                            line = raw_line.strip()
                            if not line:
                                continue

                            if self.stop_requested:
                                break

                            if line.startswith("__PROGRESS__|"):
                                self.update_row_progress_from_cli(line)
                            elif "Destination:" in line:
                                self.enqueue(
                                    "sheet_update",
                                    {
                                        "download_id": index,
                                        "title": title,
                                        "status": "Downloading...",
                                    },
                                )

                    return_code = process.wait()
                    self.set_current_process(None)

                    if self.stop_requested:
                        self.enqueue(
                            "sheet_update",
                            {
                                "download_id": index,
                                "title": title,
                                "status": "Stopped",
                            },
                        )
                        self.enqueue(
                            "done",
                            (None, f"Download stopped. Success: {self.success_count}, Failed: {self.failed_count}"),
                        )
                        return

                    if return_code == 0:
                        download_succeeded = True
                        break

                    if attempt == 1:
                        self.enqueue(
                            "sheet_update",
                            {
                                "download_id": index,
                                "title": title,
                                "fraction": 0,
                                "status": "Failed. Retrying once...",
                            },
                        )

                if download_succeeded:
                    completed += 1
                    self.completed_downloads = completed
                    self.success_count += 1
                    self.publish_counts()
                    self.update_batch_progress(completed, 0)
                    self.enqueue(
                        "sheet_update",
                        {
                            "download_id": index,
                            "title": title,
                            "fraction": 1,
                            "status": "Saved",
                        },
                    )
                    continue

                completed += 1
                self.completed_downloads = completed
                self.failed_count += 1
                self.publish_counts()
                self.update_batch_progress(completed, 0)
                self.enqueue(
                    "sheet_update",
                    {
                        "download_id": index,
                        "title": title,
                        "fraction": 0,
                        "status": "Failed after retry",
                    },
                )

            self.active_download_id = None
            self.active_title = ""
            if self.failed_count:
                self.enqueue(
                    "done",
                    (
                        False,
                        f"Finished with errors. Success: {self.success_count}, Failed: {self.failed_count}. Saved to: {output_dir}",
                    ),
                )
            else:
                self.enqueue(
                    "done",
                    (True, f"Finished downloading {self.success_count} video(s) to: {output_dir}"),
                )
        except Exception as exc:
            self.set_current_process(None)
            self.enqueue("done", (False, f"Download failed: {exc}"))


def main():
    maybe_run_embedded_yt_dlp()
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    DownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
