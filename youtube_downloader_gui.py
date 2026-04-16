import concurrent.futures
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


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


maybe_run_embedded_yt_dlp()

try:
    from PyQt6.QtCore import Qt, QTimer
    from PyQt6.QtGui import QCloseEvent, QFont
    from PyQt6.QtWidgets import QApplication, QAbstractItemView, QComboBox, QFileDialog, QFrame, QHBoxLayout
    from PyQt6.QtWidgets import QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox, QProgressBar
    from PyQt6.QtWidgets import QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QTextEdit
    from PyQt6.QtWidgets import QVBoxLayout, QWidget
except ImportError as exc:
    raise SystemExit("PyQt6 is not installed for this Python environment. Install it with: pip install PyQt6") from exc


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
HEADER_BG = "#f1e2d5"
ROW_ALT_BG = "#fff7f0"
STAT_BG = "#f8efe7"
UI_EVENT_BATCH_SIZE = 40
UI_EVENT_POLL_MS = 25
QUALITY_OPTIONS = ["Highest Quality", "144p", "240p", "360p", "480p", "720p", "1080p", "2K (1440p)"]
THREAD_OPTIONS = [str(value) for value in range(1, 31)]
PARALLEL_OPTIONS = [str(value) for value in range(1, 9)]
STATUS_PENDING = "PENDING"
STATUS_DOWNLOADING = "DOWNLOADING"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"
STATUS_RETRYING = "RETRYING"
STATUS_STOPPED = "STOPPED"
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
    return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}


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
    return f"{hours:d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def format_speed(bytes_per_second):
    return "--" if bytes_per_second is None else f"{format_bytes(bytes_per_second)}/s"


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


class DownloadProgressCell(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setStyleSheet(
            f"QProgressBar{{border:1px solid #d9bfa9;background:#ead6c6;color:{TEXT_COLOR};text-align:center;}}"
            f"QProgressBar::chunk{{background:{ACCENT_COLOR};}}"
        )
        layout.addWidget(self.progress_bar)

    def set_fraction(self, fraction):
        self.progress_bar.setValue(int(max(0, min(1, fraction)) * 100))


class DownloaderApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1180, 780)
        self.setMinimumSize(980, 700)
        self.events = queue.Queue()
        self.download_thread = None
        self.row_widgets = {}
        self.total_downloads = 0
        self.completed_downloads = 0
        self.success_count = 0
        self.failed_count = 0
        self.current_quality = "Highest Quality"
        self.current_threads = "5"
        self.current_parallel = "3"
        self.current_processes = {}
        self.current_ps_processes = {}
        self.process_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.stop_requested = False
        self.is_paused = False
        self.psutil = None
        self.net_last_bytes = None
        self.net_last_time = None
        self.last_gpu_poll = 0
        self.cached_gpu_text = "N/A"
        self.progress_by_id = {}
        self.build_ui()
        self.apply_styles()
        self.setup_system_monitoring()
        self.update_notice()
        self.event_timer = QTimer(self)
        self.event_timer.timeout.connect(self.process_events)
        self.event_timer.start(UI_EVENT_POLL_MS)
        self.stats_timer = QTimer(self)
        self.stats_timer.timeout.connect(self.refresh_system_stats)
        self.stats_timer.start(1000)

    def apply_styles(self):
        self.setStyleSheet(
            f"QMainWindow{{background:{BG_COLOR};color:{TEXT_COLOR};}}"
            f"QFrame#card,QFrame#sheetCard{{background:{CARD_COLOR};border:1px solid #eadccf;}}"
            "QFrame#sheetCard{border-width:2px;}"
            f"QFrame#statBox{{background:{STAT_BG};border:2px solid #e3cfbe;}}"
            f"QTextEdit,QLineEdit,QComboBox,QTableWidget{{background:#fffdfb;border:1px solid #e5d3c4;color:{TEXT_COLOR};}}"
            "QTextEdit,QLineEdit{padding:8px;}QComboBox{padding:6px 10px;min-height:24px;}"
            "QComboBox::drop-down{border:0;width:28px;}"
            "QPushButton{border:0;border-radius:12px;padding:12px 16px;font-weight:600;}"
            f"QPushButton#accentButton{{background:{ACCENT_COLOR};color:#fffaf5;}}"
            f"QPushButton#accentButton:hover{{background:{ACCENT_HOVER};}}"
            "QPushButton#pauseButton{background:#d98d43;color:#fffaf5;}QPushButton#pauseButton:hover{background:#c67b33;}"
            "QPushButton#stopButton{background:#b84f3a;color:#fffaf5;}QPushButton#stopButton:hover{background:#9f412f;}"
            f"QPushButton#lightButton{{background:#efe2d4;color:{TEXT_COLOR};}}QPushButton#lightButton:hover{{background:#e3d0bc;}}"
            f"QTableWidget{{alternate-background-color:{ROW_ALT_BG};gridline-color:#ecd8c7;}}"
            f"QHeaderView::section{{background:{HEADER_BG};color:{TEXT_COLOR};padding:10px;border:0;border-bottom:2px solid #e3cfbe;font-weight:700;}}"
        )

    def build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(26, 24, 26, 24)
        outer.setSpacing(16)
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        title = QLabel(APP_TITLE)
        title.setStyleSheet(f"color:{TEXT_COLOR};font-size:30px;font-weight:700;")
        subtitle = QLabel("Devoloped By - @nill404\nGITHUB - N1LL404")
        subtitle.setStyleSheet(f"color:{MUTED_COLOR};")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        outer.addWidget(header)
        top = QWidget()
        top_layout = QHBoxLayout(top)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(14)
        outer.addWidget(top)
        input_card = self.create_card()
        input_layout = QVBoxLayout(input_card)
        input_layout.setContentsMargins(20, 18, 20, 18)
        input_layout.setSpacing(8)
        input_title = QLabel("Video Links")
        input_title.setStyleSheet(f"color:{TEXT_COLOR};font-size:18px;font-weight:600;")
        self.url_input = QTextEdit()
        self.url_input.setFont(QFont("Consolas", 11))
        self.url_input.setPlaceholderText("Paste one YouTube URL per line")
        self.url_input.setMinimumHeight(220)
        input_layout.addWidget(input_title)
        input_layout.addWidget(self.url_input)
        top_layout.addWidget(input_card, 3)
        controls_card = self.create_card()
        controls_layout = QVBoxLayout(controls_card)
        controls_layout.setContentsMargins(20, 18, 20, 18)
        controls_layout.setSpacing(12)
        controls_title = QLabel("Download Settings")
        controls_title.setStyleSheet(f"color:{TEXT_COLOR};font-size:18px;font-weight:600;")
        controls_layout.addWidget(controls_title)
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(10)
        self.path_entry = QLineEdit(str(DEFAULT_OUTPUT_DIR))
        browse_button = self.create_button("Browse", "lightButton", self.choose_folder)
        browse_button.setFixedWidth(110)
        path_layout.addWidget(self.path_entry, 1)
        path_layout.addWidget(browse_button)
        controls_layout.addWidget(path_row)
        quality_row = QWidget()
        quality_layout = QHBoxLayout(quality_row)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        quality_layout.setSpacing(10)
        quality_layout.addWidget(self.make_label("Quality"))
        self.quality_menu = QComboBox()
        self.quality_menu.addItems(QUALITY_OPTIONS)
        self.quality_menu.setCurrentText("Highest Quality")
        self.quality_menu.currentTextChanged.connect(self.update_notice)
        quality_layout.addWidget(self.quality_menu, 1)
        controls_layout.addWidget(quality_row)
        thread_row = QWidget()
        thread_layout = QHBoxLayout(thread_row)
        thread_layout.setContentsMargins(0, 0, 0, 0)
        thread_layout.setSpacing(10)
        thread_layout.addWidget(self.make_label("Threads"))
        self.thread_menu = QComboBox()
        self.thread_menu.addItems(THREAD_OPTIONS)
        self.thread_menu.setCurrentText("5")
        self.thread_menu.currentTextChanged.connect(self.update_notice)
        thread_layout.addWidget(self.thread_menu, 1)
        controls_layout.addWidget(thread_row)
        parallel_row = QWidget()
        parallel_layout = QHBoxLayout(parallel_row)
        parallel_layout.setContentsMargins(0, 0, 0, 0)
        parallel_layout.setSpacing(10)
        parallel_layout.addWidget(self.make_label("Parallel"))
        self.parallel_menu = QComboBox()
        self.parallel_menu.addItems(PARALLEL_OPTIONS)
        self.parallel_menu.setCurrentText("3")
        self.parallel_menu.currentTextChanged.connect(self.update_notice)
        parallel_layout.addWidget(self.parallel_menu, 1)
        controls_layout.addWidget(parallel_row)
        stats_row = QWidget()
        stats_layout = QHBoxLayout(stats_row)
        stats_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.setSpacing(6)
        self.cpu_text = self.build_stat_box(stats_layout, "CPU\n--")
        self.ram_text = self.build_stat_box(stats_layout, "RAM\n--")
        self.gpu_text = self.build_stat_box(stats_layout, "GPU\nN/A")
        self.internet_text = self.build_stat_box(stats_layout, "Internet\n--")
        controls_layout.addWidget(stats_row)
        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(8)
        self.download_button = self.create_button("Download Video", "accentButton", self.start_download)
        self.pause_button = self.create_button("Pause", "pauseButton", self.toggle_pause)
        self.stop_button = self.create_button("Stop", "stopButton", self.stop_download)
        clear_button = self.create_button("Clear", "lightButton", self.clear_links)
        self.pause_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        for button in (self.download_button, self.pause_button, self.stop_button, clear_button):
            button_layout.addWidget(button, 1)
        controls_layout.addWidget(button_row)
        self.compact_status = QLabel("Paste links, choose a quality, and start downloading.")
        self.compact_status.setWordWrap(True)
        self.compact_status.setStyleSheet(f"color:{MUTED_COLOR};")
        self.compact_notice = QLabel("")
        self.compact_notice.setWordWrap(True)
        self.compact_notice.setStyleSheet(f"color:{MUTED_COLOR};")
        controls_layout.addWidget(self.compact_status)
        controls_layout.addWidget(self.compact_notice)
        controls_layout.addStretch(1)
        top_layout.addWidget(controls_card, 2)
        sheet_card = self.create_card("sheetCard")
        sheet_layout = QVBoxLayout(sheet_card)
        sheet_layout.setContentsMargins(20, 18, 20, 20)
        sheet_layout.setSpacing(10)
        sheet_header = QWidget()
        sheet_header_layout = QHBoxLayout(sheet_header)
        sheet_header_layout.setContentsMargins(0, 0, 0, 0)
        sheet_header_layout.setSpacing(14)
        sheet_title = QLabel("Downloads Sheet")
        sheet_title.setStyleSheet(f"color:{TEXT_COLOR};font-size:18px;font-weight:600;")
        self.success_text = QLabel("Success: 0")
        self.success_text.setStyleSheet("color:#2f8f3e;font-weight:600;")
        self.failed_text = QLabel("Failed: 0")
        self.failed_text.setStyleSheet("color:#b84f3a;font-weight:600;")
        sheet_header_layout.addWidget(sheet_title)
        sheet_header_layout.addStretch(1)
        sheet_header_layout.addWidget(self.success_text)
        sheet_header_layout.addWidget(self.failed_text)
        sheet_layout.addWidget(sheet_header)
        self.sheet_table = QTableWidget(0, 4)
        self.sheet_table.setHorizontalHeaderLabels(["ID", "TITLE", "PROGRESS", "STATUS"])
        self.sheet_table.setAlternatingRowColors(True)
        self.sheet_table.setShowGrid(False)
        self.sheet_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.sheet_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.sheet_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sheet_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sheet_table.verticalHeader().setVisible(False)
        self.sheet_table.verticalHeader().setDefaultSectionSize(54)
        header_view = self.sheet_table.horizontalHeader()
        header_view.setStretchLastSection(False)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.sheet_table.setColumnWidth(0, 72)
        self.sheet_table.setColumnWidth(2, 220)
        sheet_layout.addWidget(self.sheet_table, 1)
        outer.addWidget(sheet_card, 1)

    def create_card(self, object_name="card"):
        frame = QFrame()
        frame.setObjectName(object_name)
        return frame

    def create_button(self, text, object_name, callback):
        button = QPushButton(text)
        button.setObjectName(object_name)
        button.clicked.connect(callback)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return button

    def make_label(self, text):
        label = QLabel(text)
        label.setStyleSheet("font-weight:600;")
        return label

    def build_stat_box(self, parent_layout, text):
        box = QFrame()
        box.setObjectName("statBox")
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(8, 8, 8, 8)
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet(f"color:{TEXT_COLOR};font-weight:600;")
        box_layout.addWidget(label)
        parent_layout.addWidget(box, 1)
        return label

    def create_readonly_item(self, text="", alignment=None):
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        if alignment is not None:
            item.setTextAlignment(int(alignment))
        return item

    def ensure_sheet_row(self, download_id):
        row_index = download_id - 1
        while self.sheet_table.rowCount() <= row_index:
            self.sheet_table.insertRow(self.sheet_table.rowCount())
        row = self.row_widgets.get(download_id)
        if row is not None:
            return row
        id_item = self.create_readonly_item(f"{download_id:02d}", Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title_item = self.create_readonly_item("")
        status_item = self.create_readonly_item("", Qt.AlignmentFlag.AlignCenter)
        progress_cell = DownloadProgressCell()
        self.sheet_table.setItem(row_index, 0, id_item)
        self.sheet_table.setItem(row_index, 1, title_item)
        self.sheet_table.setCellWidget(row_index, 2, progress_cell)
        self.sheet_table.setItem(row_index, 3, status_item)
        self.sheet_table.setRowHeight(row_index, 56)
        row = {"title": title_item, "progress": progress_cell, "status": status_item}
        self.row_widgets[download_id] = row
        return row

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
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
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
                self.cpu_text.setText(f"CPU\n{self.psutil.cpu_percent(interval=None):.0f}%")
            except Exception:
                self.cpu_text.setText("CPU\n--")
            try:
                self.ram_text.setText(f"RAM\n{self.psutil.virtual_memory().percent:.0f}%")
            except Exception:
                self.ram_text.setText("RAM\n--")
            try:
                counters = self.psutil.net_io_counters()
                current_bytes = counters.bytes_recv + counters.bytes_sent
                current_time = time.time()
                if self.net_last_bytes is not None and self.net_last_time is not None:
                    elapsed = max(current_time - self.net_last_time, 0.1)
                    speed = (current_bytes - self.net_last_bytes) / elapsed
                    self.internet_text.setText(f"Internet\n{format_speed(speed)}")
                else:
                    self.internet_text.setText("Internet\n--")
                self.net_last_bytes = current_bytes
                self.net_last_time = current_time
            except Exception:
                self.internet_text.setText("Internet\n--")
        else:
            self.cpu_text.setText("CPU\n--")
            self.ram_text.setText("RAM\n--")
            self.internet_text.setText("Internet\n--")
        self.gpu_text.setText(f"GPU\n{self.get_gpu_usage_text()}")

    def update_notice(self):
        ffmpeg_location = get_ffmpeg_location()
        selected_quality = self.quality_menu.currentText()
        selected_threads = self.thread_menu.currentText()
        selected_parallel = self.parallel_menu.currentText()
        if ffmpeg_location:
            self.compact_notice.setText(
                f"Quality: {selected_quality}  |  Fragment Threads: {selected_threads}  |  Parallel Videos: {selected_parallel}  |  ffmpeg: {ffmpeg_location}"
            )
        else:
            self.compact_notice.setText(
                f"Quality: {selected_quality}  |  Fragment Threads: {selected_threads}  |  Parallel Videos: {selected_parallel}  |  ffmpeg not found. Merging may fail."
            )

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, APP_TITLE, self.path_entry.text() or str(DEFAULT_OUTPUT_DIR))
        if folder:
            self.path_entry.setText(folder)

    def reset_counters(self):
        self.success_count = 0
        self.failed_count = 0
        self.success_text.setText("Success: 0")
        self.failed_text.setText("Failed: 0")

    def update_counter_labels(self):
        self.success_text.setText(f"Success: {self.success_count}")
        self.failed_text.setText(f"Failed: {self.failed_count}")

    def clear_links(self):
        if self.download_thread and self.download_thread.is_alive():
            QMessageBox.warning(self, APP_TITLE, "Stop the current download before clearing the list.")
            return
        self.url_input.clear()
        self.total_downloads = 0
        self.completed_downloads = 0
        self.reset_counters()
        self.compact_status.setText("Input cleared. Paste new links when you're ready.")
        self.reset_sheet()

    def reset_sheet(self):
        self.sheet_table.setRowCount(0)
        self.row_widgets.clear()
        with self.state_lock:
            self.progress_by_id.clear()

    def enqueue(self, event_type, payload):
        self.events.put((event_type, payload))

    def set_busy(self, busy):
        self.download_button.setEnabled(not busy)
        self.pause_button.setEnabled(busy)
        self.stop_button.setEnabled(busy)
        if not busy:
            self.is_paused = False
            self.pause_button.setText("Pause")
            with self.state_lock:
                self.progress_by_id.clear()

    def set_current_process(self, download_id, process):
        with self.process_lock:
            if process is None:
                self.current_processes.pop(download_id, None)
                self.current_ps_processes.pop(download_id, None)
            else:
                self.current_processes[download_id] = process
                if self.psutil:
                    try:
                        self.current_ps_processes[download_id] = self.psutil.Process(process.pid)
                    except Exception:
                        self.current_ps_processes.pop(download_id, None)

    def get_process_tree(self):
        with self.process_lock:
            roots = list(self.current_ps_processes.values())
        process_map = {}
        for root in roots:
            try:
                process_map[root.pid] = root
            except Exception:
                continue
            try:
                for child in root.children(recursive=True):
                    process_map[child.pid] = child
            except Exception:
                pass
        return list(process_map.values())

    def toggle_pause(self):
        processes = self.get_process_tree()
        if not processes:
            return
        try:
            if self.is_paused:
                for proc in reversed(processes):
                    proc.resume()
                self.is_paused = False
                self.pause_button.setText("Pause")
                self.enqueue("status", "Resumed active downloads")
            else:
                for proc in processes:
                    proc.suspend()
                self.is_paused = True
                self.pause_button.setText("Resume")
                self.enqueue("status", "Paused active downloads")
        except Exception as exc:
            QMessageBox.critical(self, APP_TITLE, f"Pause/resume failed: {exc}")

    def stop_download(self):
        self.stop_requested = True
        for proc in self.get_process_tree():
            try:
                proc.resume()
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass
        self.enqueue("status", "Stopping active downloads...")

    def process_events(self):
        processed = 0
        while processed < UI_EVENT_BATCH_SIZE:
            try:
                event_type, payload = self.events.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if event_type == "status":
                self.compact_status.setText(payload)
            elif event_type == "sheet_add":
                self.add_sheet_row(payload["download_id"], payload["title"])
            elif event_type == "sheet_update":
                self.update_sheet_row(payload)
            elif event_type == "counts":
                self.success_count = payload["success"]
                self.failed_count = payload["failed"]
                self.update_counter_labels()
            elif event_type == "done":
                self.set_busy(False)
                with self.process_lock:
                    self.current_processes.clear()
                    self.current_ps_processes.clear()
                success, message = payload
                self.compact_status.setText(message)
                if success:
                    QMessageBox.information(self, APP_TITLE, message)
                elif success is False:
                    QMessageBox.critical(self, APP_TITLE, message)

    def add_sheet_row(self, download_id, title):
        row = self.ensure_sheet_row(download_id)
        row["title"].setText(title)
        row["progress"].set_fraction(0)
        row["status"].setText(STATUS_PENDING)

    def update_sheet_row(self, payload):
        row = self.ensure_sheet_row(payload["download_id"])
        if payload.get("title") is not None:
            row["title"].setText(payload["title"])
        if payload.get("fraction") is not None:
            row["progress"].set_fraction(payload["fraction"])
        if payload.get("status") is not None:
            row["status"].setText(payload["status"])

    def get_urls(self):
        raw_text = self.url_input.toPlainText().strip()
        sample_lines = {"https://www.youtube.com/watch?v=...", "https://youtu.be/..."}
        return [line.strip() for line in raw_text.splitlines() if line.strip() and line.strip() not in sample_lines]

    def build_format_selector(self, selected_quality):
        target_height = get_selected_height(selected_quality)
        if target_height is None:
            return "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best[ext=mp4]/best"
        return (
            f"bestvideo[height<={target_height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={target_height}]+bestaudio/"
            f"best[height<={target_height}][ext=mp4]/best[height<={target_height}]/best"
        )

    def build_cli_command(self, output_dir, selected_quality, selected_threads, url, download_index):
        ffmpeg_location = get_ffmpeg_location()
        output_template = str(output_dir / f"%(title)s [%(view_count|NA)s] [{download_index:02d}].%(ext)s")
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
        command = [sys.executable, "--yt-dlp-subprocess", *yt_dlp_args] if getattr(sys, "frozen", False) else [sys.executable, "-m", "yt_dlp", *yt_dlp_args]
        if ffmpeg_location:
            command.extend(["--ffmpeg-location", ffmpeg_location, "--merge-output-format", "mp4"])
        return command

    def publish_counts(self):
        with self.state_lock:
            success = self.success_count
            failed = self.failed_count
        self.enqueue("counts", {"success": success, "failed": failed})

    def set_download_progress(self, download_id, fraction):
        with self.state_lock:
            self.progress_by_id[download_id] = max(0, min(1, fraction))
        self.update_batch_progress()

    def clear_download_progress(self, download_id):
        with self.state_lock:
            self.progress_by_id.pop(download_id, None)
        self.update_batch_progress()

    def mark_download_finished(self, download_id, succeeded):
        with self.state_lock:
            self.progress_by_id.pop(download_id, None)
            self.completed_downloads += 1
            if succeeded:
                self.success_count += 1
            else:
                self.failed_count += 1
        self.publish_counts()
        self.update_batch_progress()

    def update_batch_progress(self):
        with self.state_lock:
            total = self.total_downloads
            completed = self.completed_downloads
            active_fraction_total = sum(self.progress_by_id.values())
        if not total:
            return
        overall = (completed + active_fraction_total) / total
        self.enqueue("status", f"Batch {overall * 100:.1f}% complete  |  {completed}/{total} finished")

    def update_row_progress_from_cli(self, download_id, title, line):
        if not line.startswith("__PROGRESS__|"):
            return
        _, downloaded_raw, total_raw, total_estimate_raw, eta_raw, speed_raw = line.split("|", 5)
        downloaded = parse_cli_number(downloaded_raw) or 0
        total = parse_cli_number(total_raw) or parse_cli_number(total_estimate_raw)
        fraction = (downloaded / total) if total else 0
        self.set_download_progress(download_id, fraction)
        self.enqueue(
            "sheet_update",
            {"download_id": download_id, "title": title, "fraction": fraction, "status": STATUS_DOWNLOADING},
        )

    def start_download(self):
        urls = self.get_urls()
        if not urls:
            QMessageBox.warning(self, APP_TITLE, "Please paste at least one YouTube link.")
            return
        output_dir = Path(self.path_entry.text()).expanduser()
        output_dir.mkdir(parents=True, exist_ok=True)
        self.total_downloads = len(urls)
        self.completed_downloads = 0
        self.reset_counters()
        self.publish_counts()
        self.stop_requested = False
        self.is_paused = False
        self.pause_button.setText("Pause")
        self.current_quality = self.quality_menu.currentText()
        self.current_threads = self.thread_menu.currentText()
        self.current_parallel = self.parallel_menu.currentText()
        self.reset_sheet()
        for index, url in enumerate(urls, start=1):
            self.enqueue("sheet_add", {"download_id": index, "title": url})
        self.set_busy(True)
        self.update_notice()
        self.compact_status.setText(
            f"Starting {self.current_parallel} parallel downloads with {self.current_threads} fragment threads..."
        )
        self.download_thread = threading.Thread(
            target=self.download_worker,
            args=(urls, output_dir, self.current_quality, self.current_threads, int(self.current_parallel)),
            daemon=True,
        )
        self.download_thread.start()

    def download_single(self, download_id, url, output_dir, selected_quality, selected_threads):
        if self.stop_requested:
            return None
        title = url
        self.enqueue("sheet_update", {"download_id": download_id, "title": title, "fraction": 0, "status": STATUS_PENDING})
        self.set_download_progress(download_id, 0)
        download_succeeded = False
        try:
            for attempt in range(1, 3):
                if self.stop_requested:
                    break
                if attempt == 2:
                    self.enqueue(
                        "sheet_update",
                        {"download_id": download_id, "title": title, "status": STATUS_RETRYING},
                    )
                command = self.build_cli_command(output_dir, selected_quality, selected_threads, url, download_id)
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
                self.set_current_process(download_id, process)
                if process.stdout:
                    for raw_line in process.stdout:
                        line = raw_line.strip()
                        if not line:
                            continue
                        if self.stop_requested:
                            break
                        if line.startswith("__PROGRESS__|"):
                            self.update_row_progress_from_cli(download_id, title, line)
                        elif "Destination:" in line:
                            self.enqueue(
                                "sheet_update",
                                {"download_id": download_id, "title": title, "status": STATUS_DOWNLOADING},
                            )
                return_code = process.wait()
                self.set_current_process(download_id, None)
                if self.stop_requested:
                    break
                if return_code == 0:
                    download_succeeded = True
                    break
                if attempt == 1:
                    self.enqueue(
                        "sheet_update",
                        {"download_id": download_id, "title": title, "fraction": 0, "status": STATUS_RETRYING},
                    )
        finally:
            self.set_current_process(download_id, None)
        if self.stop_requested:
            self.clear_download_progress(download_id)
            self.enqueue("sheet_update", {"download_id": download_id, "title": title, "status": STATUS_STOPPED})
            return None
        self.mark_download_finished(download_id, download_succeeded)
        if download_succeeded:
            self.enqueue("sheet_update", {"download_id": download_id, "title": title, "fraction": 1, "status": STATUS_DONE})
            return True
        self.enqueue("sheet_update", {"download_id": download_id, "title": title, "fraction": 0, "status": STATUS_FAILED})
        return False

    def download_worker(self, urls, output_dir, selected_quality, selected_threads, parallel_downloads):
        try:
            import yt_dlp
        except ImportError:
            self.enqueue("done", (False, "yt-dlp is not installed for this Python environment. Install it with: pip install -r requirements.txt"))
            return
        has_ffmpeg = get_ffmpeg_location() is not None
        try:
            if not has_ffmpeg:
                self.enqueue("status", f"ffmpeg is missing. The app will try the best progressive stream for {selected_quality}, but merging may fail.")
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, parallel_downloads)) as executor:
                future_map = {
                    executor.submit(self.download_single, index, url, output_dir, selected_quality, selected_threads): index
                    for index, url in enumerate(urls, start=1)
                }
                for future in concurrent.futures.as_completed(future_map):
                    if self.stop_requested:
                        continue
                    future.result()
            if self.stop_requested:
                with self.state_lock:
                    success = self.success_count
                    failed = self.failed_count
                self.enqueue("done", (None, f"Download stopped. Success: {success}, Failed: {failed}"))
                return
            with self.state_lock:
                failed_count = self.failed_count
                success_count = self.success_count
            if failed_count:
                self.enqueue("done", (False, f"Finished with errors. Success: {success_count}, Failed: {failed_count}. Saved to: {output_dir}"))
            else:
                self.enqueue("done", (True, f"Finished downloading {success_count} video(s) to: {output_dir}"))
        except Exception as exc:
            self.enqueue("done", (False, f"Download failed: {exc}"))

    def closeEvent(self, event: QCloseEvent):
        if self.download_thread and self.download_thread.is_alive():
            reply = QMessageBox.question(
                self,
                APP_TITLE,
                "A download is still running. Do you want to close the app and stop it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.stop_download()
        event.accept()


def main():
    DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    window = DownloaderApp()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
