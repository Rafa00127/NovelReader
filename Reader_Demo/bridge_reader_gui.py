"""起点桥接阅读器 — 接收浏览器发送的章节文本，通过本地 TTS 合成朗读。"""
import ctypes, os, sys, re, json, struct, socket, time, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(_FILE_DIR)

import numpy as np; import sounddevice as sd
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *

# ═══════════ 全局常量 ═══════════
CONFIG = os.path.join(_FILE_DIR, "reader_config.json")
SAMPLE_RATES = {"qwen": 24000, "fish": 44100}
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9988
BRIDGE_PORT = 8899

## ═══════════ 语速调整 (disabled) ═══════════

# ═══════════ 工具函数 ═══════════

def load_cfg():
    if os.path.exists(CONFIG):
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"books": {}, "last_gguf": {}}

def save_cfg(c):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)

def split_text(text):
    """句子拆分 — 与 reader_qt_server.py 完全一致。"""
    MAX_LEN = 45
    text = re.sub(r'…{2,}', '…', text)
    raw = [s for s in re.split(r'(?<=[.。!！?？\n])', text) if s.strip()]
    _closing_q = r'」』"''”」”＂'
    for i in range(len(raw) - 1, 0, -1):
        m = re.match(r'^([' + _closing_q + r']+)', raw[i])
        if m:
            raw[i - 1] = raw[i - 1].rstrip() + m.group(1)
            raw[i] = raw[i][len(m.group(1)):].strip()
    _lead_noise = r'^[\s　。，、；：！？…\.\,\;\:\!\?\-'
    _lead_noise += r'」』"''”'
    _lead_noise += r']+'
    final = []
    for seg in raw:
        seg = re.sub(_lead_noise, '', seg)
        if not re.search(r'[\w一-鿿]', seg):
            continue
        if len(seg) <= MAX_LEN:
            final.append(seg)
        else:
            parts = re.split(r'(?<=[，,;；、])', seg)
            buf = ""
            for p in parts:
                if len(buf) + len(p) <= MAX_LEN or len(buf) == 0:
                    buf += p
                else:
                    if buf.strip(): final.append(buf.strip())
                    buf = p
            if buf.strip(): final.append(buf.strip())
    final = [re.sub(r'[，,]+$', '', s) for s in final]
    return final


# ═══════════ 自适应按钮 ═══════════

class ResponsiveButton(QPushButton):
    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

# ═══════════ TTS 引擎 (TCP Server 封装) ═══════════

class TtsEngine:
    """与 reader_qt_server.py 完全一致的 TCP 协议封装。"""

    @staticmethod
    def _recvn(sock, n):
        data = bytearray()
        while len(data) < n:
            try:
                chunk = sock.recv(n - len(data))
            except ConnectionResetError:
                chunk = b""
            if not chunk:
                break
            data.extend(chunk)
        return data

    @classmethod
    def synth(cls, text, temperature=0.0):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(120)
            sock.connect((SERVER_HOST, SERVER_PORT))

            payload = text.encode("utf-8")
            sock.sendall(struct.pack(">i", len(payload)))
            sock.sendall(struct.pack(">f", temperature))
            sock.sendall(payload)

            hdr = cls._recvn(sock, 4)
            if len(hdr) < 4:
                sock.close()
                raise RuntimeError("Server returned empty header")
            n_samples = struct.unpack(">i", hdr)[0]
            if n_samples <= 0:
                sock.close()
                raise RuntimeError(f"Server error: n_samples={n_samples}")

            data = cls._recvn(sock, n_samples * 4)
            try:
                sock.shutdown(socket.SHUT_RD)
            except OSError:
                pass
            sock.close()

            arr = np.frombuffer(data, dtype=np.float32).copy()
            if len(arr) != n_samples:
                raise RuntimeError(f"Truncated PCM: got {len(arr)}, expected {n_samples}")

            ## speed disabled
            return arr

        except ConnectionRefusedError:
            raise RuntimeError("TTS server not running")
        except socket.timeout:
            raise RuntimeError("TTS server timeout")


# ═══════════ 跨线程信号 ═══════════

class BridgeSignals(QObject):
    text_received = pyqtSignal(str, str, float)   # text, title, speed
    status_msg = pyqtSignal(str, str)              # message, color


# ═══════════ 深色 QSS (与 reader_qt_server.py 一致) ═══════════

QSS = """
* { font-family: "Microsoft YaHei UI", "PingFang SC", sans-serif; }
QMainWindow, QWidget { background-color: #111; color: #ddd; }
QPlainTextEdit, QTextEdit {
    background-color: #1a1a18; color: #c8b878; border: none;
    padding: 30px 50px; line-height: 1.8;
    selection-background-color: #2a2518; selection-color: #c8b878;
}
QLineEdit {
    background: #252525; color: #ddd; border: 1px solid #444;
    border-radius: 6px; padding: 6px 10px; font-size: 13px;
}
QLineEdit:focus { border-color: #666; }
QPushButton {
    background: #2a2a2a; color: #ddd; border: 1px solid #444;
    border-radius: 6px; padding: 8px 16px; font-size: 13px;
}
QPushButton:hover { background: #3a3a3a; }
QPushButton:pressed { background: #252525; }
QPushButton#playBtn { background: #2d5a2d; color: #fff; font-size: 14px; font-weight: bold; padding: 8px 24px; border: none; }
QPushButton#playBtn:hover { background: #3a6a3a; }
QSlider::groove:horizontal { background: #444; height: 6px; border-radius: 3px; }
QSlider::handle:horizontal { background: #888; width: 14px; height: 14px; margin: -4px 0; border-radius: 7px; }
QSlider::handle:horizontal:hover { background: #aaa; }
QProgressBar {
    background: #252525; border: none; border-radius: 4px; height: 6px; text-align: center;
    font-size: 10px; color: #888;
}
QProgressBar::chunk { background: #666; border-radius: 4px; }
QLabel { background: transparent; }
QScrollBar:vertical { background: #111; width: 8px; }
QScrollBar::handle:vertical { background: #444; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #555; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


# ═══════════ 主窗口 ═══════════

class BridgeWin(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("桥接阅读器 — TTS Server")
        self.resize(1000, 750); self.setStyleSheet(QSS)
        self._c = load_cfg()

        sv = self._c.get("server", {})
        if sv.get("port"):
            global SERVER_PORT
            SERVER_PORT = int(sv["port"])
        self._sample_rate = SAMPLE_RATES.get(
            "fish" if sv.get("model_type", "") == "Fish S2" else "qwen", 24000)

        self._sentences = []; self._current_idx = 0; self._total = 0
        self._playing = False; self._paused = False; self._sync_scroll = False
        self._wavs = {}; self._wavs_lock = threading.Lock()
        self._playback_id = 0; self._speed = 1.0; self._volume = self._c.get("volume", 1.0)
        self._synth_pause = threading.Event(); self._synth_pause.set()
        self._paused_id = 0; self._paused_idx = 0; self._paused_total = 0
        self._pause_keep_synth = self._c.get("pause_keep_synth", False)
        self._auto_play = self._c.get("auto_play", False)
        self._http_server = None; self._http_thread = None
        self._signals = BridgeSignals()

        self._build()
        self._connect_signals()
        self._start_http_server()

    def _tool_btn(self, icon, text, tooltip, func):
        b = ResponsiveButton(f"{icon} {text}")
        b.setToolTip(tooltip); b.setStyleSheet("padding:4px 6px;")
        b.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        b.clicked.connect(func)
        b._icon = icon; b._text = text
        self._collapse_btns.append(b)
        return b

    def _adapt_toolbar(self):
        wide = self.width() >= 800
        for b in self._collapse_btns:
            if wide: b.setText(f"{b._icon} {b._text}")
            else: b.setText(b._icon)

    def _build(self):
        cw = QWidget(); self.setCentralWidget(cw)
        lay = QVBoxLayout(cw); lay.setContentsMargins(10, 8, 10, 6); lay.setSpacing(6)

        self._collapse_btns = []

        # ── 工具栏 ──
        tb = QHBoxLayout(); tb.setSpacing(3)
        tb.addWidget(self._tool_btn("⚙", "配置", "配置说明",
                     lambda: QMessageBox.information(
                         self, "配置", "TTS Server 配置请使用 reader_qt_server.py 的「模型配置」功能。\n\n"
                         "桥接端口在此配置：可在 reader_config.json 中修改 bridge.http_port，默认为 8899。")))
        tb.addWidget(self._tool_btn("🚀", "启动", "启动 Server", self._launch_server))

        self._server_lbl = QLabel("HTTP: 启动中...")
        self._server_lbl.setStyleSheet("color:#fa0;")
        tb.addWidget(self._server_lbl)

        tb.addStretch()
        tb.addWidget(QLabel("A-"))
        self._fs = QSlider(Qt.Orientation.Horizontal); self._fs.setRange(8, 64)
        fs = self._c.get("font_size", 23); self._fs.setValue(fs); self._fs.setFixedWidth(64)
        self._fs.valueChanged.connect(self._on_font); tb.addWidget(self._fs)
        tb.addWidget(QLabel("A+"))
        self._keep_btn = QCheckBox("后台合成")
        self._keep_btn.setChecked(self._pause_keep_synth)
        self._keep_btn.toggled.connect(self._on_keep_synth)
        tb.addWidget(self._keep_btn)
        self._auto_btn = QCheckBox("收到即读")
        self._auto_btn.setChecked(self._auto_play)
        self._auto_btn.toggled.connect(self._on_auto_play)
        tb.addWidget(self._auto_btn)
        tb.addWidget(self._tool_btn("⛶", "全屏", "全屏", self._fullscreen))
        lay.addLayout(tb)

        # ── 章节标题 ──
        self._title_lbl = QLabel("等待浏览器发送章节...")
        self._title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        f = self._title_lbl.font(); f.setBold(True); f.setPixelSize(16)
        self._title_lbl.setFont(f)
        self._title_lbl.setStyleSheet("color:#aaa; padding: 8px;")
        lay.addWidget(self._title_lbl)

        # ── 正文 ──
        self._tx = QPlainTextEdit(); self._tx.setReadOnly(True)
        f = self._tx.font(); f.setPixelSize(self._c.get("font_size", 23)); self._tx.setFont(f)
        lay.addWidget(self._tx)

        # ── 播放栏 ──
        bb = QHBoxLayout(); bb.setSpacing(3)
        self._play_btn = QPushButton("▶ 播放")
        self._play_btn.setObjectName("playBtn")
        self._play_btn.clicked.connect(self._toggle_play)
        self._play_btn.setEnabled(False)
        bb.addWidget(self._play_btn)

        bb.addWidget(self._tool_btn("⏹", "停止", "停止", self._stop))

        self._resume_btn = QPushButton("⏵ 续播")
        self._resume_btn.setObjectName("playBtn")
        self._resume_btn.clicked.connect(self._resume)
        self._resume_btn.hide()
        bb.addWidget(self._resume_btn)

        self._prog = QProgressBar(); self._prog.setMaximum(1)
        bb.addWidget(self._prog)

        self._prog_lbl = QLabel("0/0"); self._prog_lbl.setStyleSheet("color:#888;")
        bb.addWidget(self._prog_lbl)

        bb.addStretch()

        bb.addWidget(QLabel("🔈"))
        self._vol_slider = QSlider(Qt.Orientation.Horizontal)
        self._vol_slider.setRange(0, 150)
        self._vol_slider.setValue(int(self._volume * 100))
        self._vol_slider.setFixedWidth(64)
        self._vol_slider.valueChanged.connect(self._on_volume)
        bb.addWidget(self._vol_slider)
        self._vol_lbl = QLabel(f"{int(self._volume * 100)}%")
        bb.addWidget(self._vol_lbl)

        self._sync_btn = QPushButton("⟳")
        self._sync_btn.setToolTip("同步滚动")
        self._sync_btn.setCheckable(True); self._sync_btn.setStyleSheet("padding:4px 6px;")
        def _sync_toggled(v):
            self._sync_scroll = v
            if v: self._sync_btn.setStyleSheet("background:#3a5a3a; color:#fff; border:1px solid #5a8a5a; border-radius:6px; padding:4px 6px; font-size:13px;")
            else: self._sync_btn.setStyleSheet("padding:4px 6px;")
        self._sync_btn.toggled.connect(_sync_toggled)
        bb.addWidget(self._sync_btn)

        ## speed slider disabled
        ## bb.addWidget(QLabel("⚡"))
        ## self._speed_slider = QSlider(Qt.Orientation.Horizontal)
        ## self._speed_slider.setRange(50, 200); self._speed_slider.setValue(100)
        ## self._speed_slider.setFixedWidth(64)
        ## self._speed_slider.valueChanged.connect(self._on_speed)
        ## bb.addWidget(self._speed_slider)
        ## self._speed_lbl = QLabel("1.0x")
        ## bb.addWidget(self._speed_lbl)
        lay.addLayout(bb)

        # ── 状态栏 ──
        self._status_lbl = QLabel("就绪 — 等待浏览器发送章节...")
        self._status_lbl.setStyleSheet("color:#888; padding: 4px;")
        lay.addWidget(self._status_lbl)

        self._adapt_toolbar()

    def _connect_signals(self):
        self._signals.text_received.connect(self._on_text_received)
        self._signals.status_msg.connect(self._on_status_msg)

    # ── HTTP Server ──

    def _start_http_server(self):
        bridge_cfg = self._c.get("bridge", {})
        port = bridge_cfg.get("http_port", BRIDGE_PORT)

        signals = self._signals

        class BridgeHandler(BaseHTTPRequestHandler):
            def do_OPTIONS(self):
                self.send_response(204)
                self._cors()
                self.end_headers()

            def do_GET(self):
                body = json.dumps({"status": "ok", "msg": "TTS bridge running"}).encode("utf-8")
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    self._err(400, "invalid json")
                    return

                text = body.get("text", "").strip()
                if not text:
                    self._err(400, "empty text")
                    return

                title = body.get("title", "") or "未命名章节"
                speed = float(body.get("speed", 1.0))

                signals.text_received.emit(text, title, speed)

                resp = json.dumps({"status": "ok", "chars": len(text)}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

            def _err(self, code, msg):
                body = json.dumps({"status": "error", "error": msg}).encode("utf-8")
                self.send_response(code)
                self._cors()
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _cors(self):
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")

            def log_message(self, *a):
                pass

        try:
            self._http_server = HTTPServer(("127.0.0.1", port), BridgeHandler)
        except OSError as e:
            self._server_lbl.setText(f"HTTP: 端口 {port} 被占用")
            self._server_lbl.setStyleSheet("color:#f00;")
            self._status_lbl.setText(f"无法启动 HTTP Server: {e}")
            return

        self._http_thread = threading.Thread(target=self._http_server.serve_forever, daemon=True)
        self._http_thread.start()
        self._server_lbl.setText(f"● HTTP 127.0.0.1:{port}")
        self._server_lbl.setStyleSheet("color:#0a0;")
        self._status_lbl.setText(f"就绪 — 等待浏览器发送章节到 :{port}...")

    # ── 信号槽 ──

    def _on_text_received(self, text, title, speed):
        """HTTP 线程发出信号 → 主线程处理。"""
        # 无论正在播放还是暂停，都彻底终止旧播放
        self._synth_pause.set()     # 解锁旧的合成线程
        self._playback_id += 1      # 旧线程看到 ID 变化后退出
        self._playing = False; self._paused = False
        self._paused_id = 0
        self._stop_stream()
        self._resume_btn.hide()

        self._current_text = text
        self._current_title = title
        ## self._speed = speed  # speed disabled
        ## self._speed_slider.setValue(int(speed * 100))
        ## self._speed_lbl.setText(f"{speed:.1f}x")

        self._sentences = split_text(text)
        self._total = len(self._sentences)
        self._current_idx = 0

        # 显示
        self._title_lbl.setText(f"═══ {title} ═══")
        self._title_lbl.setStyleSheet("color:#c8b878; padding: 8px;")
        self._tx.setPlainText(text)

        # 进度
        self._prog.setMaximum(max(self._total, 1))
        self._prog.setValue(0)
        self._prog_lbl.setText(f"0/{self._total}句")

        self._play_btn.setEnabled(True)
        self._play_btn.setText("▶ 播放")
        self._status_lbl.setText(f"已接收: {title} ({len(text)}字, {self._total}句)")
        self._status_lbl.setStyleSheet("color:#0a0; padding: 4px;")

        if self._auto_play and self._total > 0:
            self._start_playback(0)

    def _on_status_msg(self, msg, color):
        self._status_lbl.setText(msg)
        self._status_lbl.setStyleSheet(f"color:{color}; padding: 4px;")

    # ── Server 启动 ──

    def _launch_server(self):
        bat = os.path.join(_FILE_DIR, "tts_launch.bat")
        if not os.path.exists(bat):
            QMessageBox.warning(self, "提示", "请先在 reader_qt_server.py 的「模型配置」中设置并启动一次 Server")
            return
        os.startfile(bat)
        sv = self._c.get("server", {})
        self._sample_rate = SAMPLE_RATES.get(
            "fish" if sv.get("model_type", "") == "Fish S2" else "qwen", 24000)
        self._status_lbl.setText("Server 启动中...")
        self._status_lbl.setStyleSheet("color:#fa0;")
        port = int(sv.get("port", SERVER_PORT))

        def _poll():
            for _ in range(20):
                time.sleep(0.5)
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(1)
                    s.connect((SERVER_HOST, port))
                    s.close()
                    self._status_lbl.setText(f"TTS Server: {SERVER_HOST}:{port}")
                    self._status_lbl.setStyleSheet("color:#0a0;")
                    return
                except (ConnectionRefusedError, socket.timeout, OSError):
                    continue
            self._status_lbl.setText("Server 连接超时")
            self._status_lbl.setStyleSheet("color:#f00;")

        threading.Thread(target=_poll, daemon=True).start()

    # ── 播放控制 ──

    def _toggle_play(self):
        if self._playing:
            self._pause()
        else:
            self._start_with_select()

    def _start_with_select(self):
        """弹出选句对话框，选择从哪一句开始播放。"""
        if self._total == 0:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f"选择起始句 (共 {self._total} 句)")
        dlg.resize(550, 650); dlg.setStyleSheet(QSS)
        lv = QVBoxLayout(dlg); lv.setContentsMargins(8, 8, 8, 8)

        lst = QListWidget()
        for i, s in enumerate(self._sentences):
            txt = s.replace("\n", " ").strip()
            lbl = f"[{i}] {txt[:80]}{'...' if len(txt) > 80 else ''}"
            lst.addItem(lbl)
        lst.setCurrentRow(0)
        lst.scrollToItem(lst.item(0), QAbstractItemView.ScrollHint.PositionAtCenter)
        lv.addWidget(lst)

        row = QHBoxLayout()
        btn_top = QPushButton("从头开始")
        ok = QPushButton("从此句开始"); ok.setObjectName("playBtn")
        row.addWidget(btn_top); row.addStretch(); row.addWidget(ok)
        lv.addLayout(row)

        self._save_start = 0
        ok.clicked.connect(lambda: (setattr(self, '_save_start', lst.currentRow()), dlg.accept()))
        btn_top.clicked.connect(lambda: (setattr(self, '_save_start', 0), dlg.accept()))
        lst.itemDoubleClicked.connect(lambda item: (setattr(self, '_save_start', lst.row(item)), dlg.accept()))

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._start_playback(self._save_start)

    def _start_playback(self, start_idx=0):
        if self._total == 0:
            return
        self._playing = True; self._paused = False
        self._synth_pause.set()
        self._play_btn.setText("⏸ 暂停")
        self._resume_btn.hide()
        self._current_idx = start_idx
        self._wavs = {}; self._wavs_lock = threading.Lock()
        self._playback_id += 1
        pid = self._playback_id
        ## speed = self._speed  # disabled

        def _synth_worker():
            for idx in range(start_idx, self._total):
                if not self._pause_keep_synth:
                    self._synth_pause.wait()  # 暂停时阻塞，不发请求
                if self._playback_id != pid:
                    return
                try:
                    sv = self._c.get("server", {})
                    tag = sv.get("fish_tag", "")
                    txt = self._sentences[idx]
                    if tag and sv.get("model_type", "") == "Fish S2":
                        txt = tag + txt
                    pcm = TtsEngine.synth(txt)
                    if self._playback_id != pid:
                        return
                    pcm = np.clip(pcm, -1, 1)
                    with self._wavs_lock:
                        self._wavs[idx] = pcm.astype(np.float32).tobytes()
                except Exception as e:
                    with self._wavs_lock:
                        self._wavs[idx] = e

        threading.Thread(target=_synth_worker, daemon=True).start()
        self._prog.setValue(start_idx)
        self._prog_lbl.setText(f"{start_idx}/{self._total}句")
        self._status_lbl.setText(f"合成中... ({start_idx}/{self._total})")
        self._play_next()

    def _play_next(self):
        if not self._playing:
            return
        if self._current_idx >= self._total:
            self._on_finished()
            return

        with self._wavs_lock:
            result = self._wavs.get(self._current_idx)

        if result is None:
            QTimer.singleShot(200, self._play_next)
            return

        if isinstance(result, Exception):
            self._status_lbl.setText(f"合成错误: {result}")
            self._status_lbl.setStyleSheet("color:#f00;")
            with self._wavs_lock:
                del self._wavs[self._current_idx]
            self._current_idx += 1
            QTimer.singleShot(0, self._play_next)
            return

        pcm = np.frombuffer(result, dtype=np.float32)
        self._cur_pcm = pcm.astype(np.float32)
        self._cur_pos = 0

        self._current_idx += 1
        self._prog.setValue(self._current_idx)
        self._prog_lbl.setText(f"{self._current_idx}/{self._total}句")
        self._status_lbl.setText(f"播放中 ({self._current_idx}/{self._total})")
        self._status_lbl.setStyleSheet("color:#ccc;")

        # 同步滚动
        cur = self._current_idx - 1
        if self._sync_scroll and cur < len(self._sentences):
            needle = self._sentences[cur][:30]
            if needle:
                doc = self._tx.document()
                cursor = doc.find(needle)
                if not cursor.isNull():
                    self._tx.setTextCursor(cursor)
                    self._tx.ensureCursorVisible()

        # 清理已播放的
        with self._wavs_lock:
            self._wavs.pop(self._current_idx - 2, None)

        self._stop_stream()

        def _cb(outdata, frames, time_info, status):
            if status:
                print(f"[audio] {status}")
            remaining = len(self._cur_pcm) - self._cur_pos
            if remaining <= 0:
                raise sd.CallbackStop()
            n = min(frames, remaining)
            vol = self._c.get("volume", 1.0)
            outdata[:n, 0] = np.clip(self._cur_pcm[self._cur_pos:self._cur_pos + n] * vol, -1, 1)
            if n < frames:
                outdata[n:, 0] = 0
                raise sd.CallbackStop()
            self._cur_pos += n

        stream = sd.OutputStream(samplerate=self._sample_rate, channels=1,
                                  callback=_cb, dtype='float32')
        self._cur_stream = stream
        stream.start()

        def _w():
            if not self._playing:
                try: stream.stop()
                except Exception: pass
                try: stream.close()
                except Exception: pass
                return
            try:
                if stream.active:
                    QTimer.singleShot(150, _w)
                elif self._current_idx >= self._total:
                    self._on_finished()
                else:
                    self._play_next()
            except Exception:
                if self._current_idx >= self._total:
                    self._on_finished()
                else:
                    self._play_next()
        _w()

    def _wait_audio(self):
        """保留兼容，不再使用。"""
        pass

    def _pause(self):
        """暂停：停音频、阻塞合成线程、显示续播按钮。"""
        self._paused = True; self._playing = False
        self._synth_pause.clear()  # 合成线程在此阻塞，不再发请求
        self._stop_stream()
        self._play_btn.setText("▶ 播放")
        self._resume_btn.show()
        self._paused_id = self._playback_id
        self._paused_idx = max(0, self._current_idx - 1)
        self._paused_total = self._total
        self._status_lbl.setText(f"已暂停 ({self._paused_idx}/{self._total})")
        self._status_lbl.setStyleSheet("color:#fa0;")

    def _resume(self):
        """续播：从暂停处继续，不弹选句框。"""
        if self._paused_id != self._playback_id:
            return  # 已被新文本冲掉
        self._paused = False; self._playing = True
        self._synth_pause.set()
        self._play_btn.setText("⏸ 暂停")
        self._resume_btn.hide()
        self._current_idx = self._paused_idx
        self._status_lbl.setText(f"续播中 ({self._current_idx}/{self._total})")
        self._play_next()

    def _stop(self):
        self._playing = False; self._paused = False
        self._synth_pause.set()  # 先解锁合成线程
        self._playback_id += 1   # 再让线程看到 ID 变化后退出
        self._stop_stream()
        self._resume_btn.hide()
        self._prog.setValue(0)
        self._prog_lbl.setText(f"0/{self._total}句")
        self._play_btn.setText("▶ 播放")
        self._status_lbl.setText("已停止")
        self._status_lbl.setStyleSheet("color:#888;")

    def _stop_stream(self):
        if hasattr(self, '_cur_stream') and self._cur_stream is not None:
            try: self._cur_stream.stop()
            except Exception: pass
            try: self._cur_stream.close()
            except Exception: pass
            self._cur_stream = None

    def _on_finished(self):
        self._playing = False; self._paused = False
        self._synth_pause.set()
        self._stop_stream()
        self._play_btn.setText("▶ 播放")
        self._resume_btn.hide()
        self._status_lbl.setText("朗读完成 ✓")
        self._status_lbl.setStyleSheet("color:#0a0;")

    ## def _on_speed(self, v):  # speed disabled
    ##     self._speed = v / 100.0
    ##     self._speed_lbl.setText(f"{self._speed:.1f}x")
    ##     self._c["speed"] = self._speed
    ##     save_cfg(self._c)

    def _on_volume(self, v):
        self._volume = v / 100.0
        self._vol_lbl.setText(f"{v}%")
        self._c["volume"] = self._volume
        save_cfg(self._c)

    def _on_font(self, v):
        f = self._tx.font(); f.setPixelSize(v); self._tx.setFont(f)
        self._c["font_size"] = v
        save_cfg(self._c)

    def _on_keep_synth(self, v):
        self._pause_keep_synth = v
        self._c["pause_keep_synth"] = v
        save_cfg(self._c)
        # 如果当前在暂停中且刚打开此开关，解锁合成线程
        if v and self._paused:
            self._synth_pause.set()

    def _on_auto_play(self, v):
        self._auto_play = v
        self._c["auto_play"] = v
        save_cfg(self._c)

    def _fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._adapt_toolbar()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_F11:
            self._fullscreen()
        elif e.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.showNormal()
        else:
            super().keyPressEvent(e)

    def closeEvent(self, e):
        self._playing = False
        self._playback_id += 1
        self._stop_stream()
        if self._http_server:
            self._http_server.shutdown()
        save_cfg(self._c)
        e.accept()


if __name__ == "__main__":
    if sys.platform == "win32":
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    app = QApplication(sys.argv); app.setStyle("Fusion")
    win = BridgeWin(); win.show()
    sys.exit(app.exec())
