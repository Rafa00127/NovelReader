"""小说朗读器 — PyQt6 现代深色 UI + CrispASR TCP TTS Server 后端。"""
import ctypes, os, sys, re, json, threading, queue, struct, socket, time, wave, tempfile, subprocess
from datetime import datetime
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

CHAPTER_RE = [
    re.compile(r'^第[\d一二三四五六七八九十百千万]*章'),
    re.compile(r'^第[\d一二三四五六七八九十百千万]*[节回]'),
    re.compile(r'^Chapter\s+\d+', re.IGNORECASE),
    re.compile(r'^第\s*\d+\s*章'), re.compile(r'^\d+[\.、]\s+\S'),
    re.compile(r'^[一二三四五六七八九十百千万]+、'),
]

# ═══════════ 深色 QSS ═══════════
QSS = """
* { font-family: "Microsoft YaHei UI", "PingFang SC", sans-serif; }
QMainWindow, QWidget { background-color: #111; color: #ddd; }
QPlainTextEdit, QTextEdit {
    background-color: #1a1a18; color: #c8b878; border: none;
    padding: 30px 50px; line-height: 1.8;
    selection-background-color: #3a3520;
}
QListWidget {
    background-color: #1a1a1a; color: #ccc; border: none;
    font-size: 13px; outline: none; padding: 4px;
}
QListWidget::item { padding: 6px 10px; border-radius: 4px; }
QListWidget::item:selected { background: #333; color: #fff; }
QListWidget::item:hover { background: #252525; }
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
QComboBox {
    background: #252525; color: #ddd; border: 1px solid #444;
    border-radius: 6px; padding: 6px 10px;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView { background: #252525; color: #ddd; selection-background-color: #333; }
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

# ═══════════ 工具函数 ═══════════

def load_cfg():
    if os.path.exists(CONFIG):
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"books": {}, "last_gguf": {}}

def save_cfg(c):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)

def _read_file(fp):
    for enc in ["utf-8-sig", "utf-8", "utf-16", "gbk", "gb18030", "latin-1"]:
        try:
            with open(fp, "r", encoding=enc) as f:
                f.read(); break
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(fp, "r", encoding=enc) as f:
        return f.readlines()

def parse_book(fp):
    lines = _read_file(fp)
    ch = []; buf = []; title = "前言"
    for line in lines:
        s = line.rstrip("\n\r")
        if not s.strip():
            if buf and not s: buf.append("")
        elif any(p.match(s.strip()) for p in CHAPTER_RE) and len(s.strip()) <= 60:
            if buf: ch.append((title, buf))
            title = s.strip(); buf = []
        else:
            buf.append(s)
    if buf: ch.append((title, buf))
    return ch

def split_text(text):
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


# ═══════════ TTS 引擎 (TCP Server 封装) ═══════════

class TtsEngine:
    """Talks to tts_server over TCP. The server owns the model (GPU-resident),
    serialises synthesis via its own mutex. We just send text, receive PCM."""

    @staticmethod
    def _recvn(sock, n):
        """Receive exactly n bytes, handling partial reads and connection resets."""
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
    def synth(cls, text, speed=1.0):
        """Send text to server, return float32 PCM array. Raises on error."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(120)
            sock.connect((SERVER_HOST, SERVER_PORT))

            # Send: 4-byte big-endian length + UTF-8 text
            payload = text.encode("utf-8")
            sock.sendall(struct.pack(">i", len(payload)))
            sock.sendall(payload)

            # Read response: 4-byte n_samples + float32 PCM
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

            # Speed change
            if speed != 1.0 and speed > 0.1:
                old_len = len(arr)
                new_len = int(old_len / speed)
                arr = np.interp(np.linspace(0, old_len - 1, new_len),
                                np.arange(old_len), arr).astype(np.float32)
            return arr

        except ConnectionRefusedError:
            raise RuntimeError("TTS server not running (start tts_server.exe first)")
        except socket.timeout:
            raise RuntimeError("TTS server timeout")


# ═══════════ 设置弹窗 ═══════════

class SettingsDialog(QDialog):
    def __init__(self, parent, cfg):
        super().__init__(parent); self._c = cfg
        self.setWindowTitle("设置"); self.setFixedSize(440, 200)
        self.setStyleSheet(QSS)
        lay = QVBoxLayout(self); lay.setSpacing(10)

        row = QHBoxLayout(); row.addWidget(QLabel("语速"))
        self._sp = QSlider(Qt.Orientation.Horizontal); self._sp.setRange(50, 200)
        self._sp.setValue(int(cfg.get("speed", 1.0) * 100))
        self._sp_lbl = QLabel(f"{cfg.get('speed', 1.0):.1f}x")
        self._sp.valueChanged.connect(lambda v: self._sp_lbl.setText(f"{v / 100:.1f}x"))
        row.addWidget(self._sp); row.addWidget(self._sp_lbl); lay.addLayout(row)

        row = QHBoxLayout(); row.addWidget(QLabel("音量"))
        self._vo = QSlider(Qt.Orientation.Horizontal); self._vo.setRange(0, 150)
        self._vo.setValue(int(cfg.get("volume", 1.0) * 100))
        self._vo_lbl = QLabel(f"{int(cfg.get('volume', 1.0) * 100)}%")
        self._vo.valueChanged.connect(lambda v: self._vo_lbl.setText(f"{v}%"))
        row.addWidget(self._vo); row.addWidget(self._vo_lbl); lay.addLayout(row)

        save = QPushButton("保存设置"); save.clicked.connect(self._sv); lay.addWidget(save)

    def _sv(self):
        self._c["speed"] = self._sp.value() / 100
        self._c["volume"] = self._vo.value() / 100
        save_cfg(self._c); self.accept()


class ServerDialog(QDialog):
    def __init__(self, parent, cfg):
        super().__init__(parent); self._c = cfg
        self.setWindowTitle("模型服务器"); self.setFixedSize(620, 500)
        self.setStyleSheet(QSS)
        lay = QVBoxLayout(self); lay.setSpacing(6)
        sv = cfg.get("server", {})

        def _row(label, *widgets):
            r = QHBoxLayout(); r.addWidget(QLabel(label))
            for w in widgets: r.addWidget(w)
            lay.addLayout(r)

        # Model type
        self._model_type = QComboBox()
        _row("模型类型", self._model_type)
        self._model_type.addItems(["Qwen3-TTS", "Fish S2"])
        self._model_type.setCurrentText(sv.get("model_type", "Qwen3-TTS"))
        self._model_type.currentTextChanged.connect(self._on_model_type)
        lay.addWidget(self._model_type)

        # ── Qwen 字段 ──
        self._qwen_box = QWidget()
        ql = QVBoxLayout(self._qwen_box); ql.setContentsMargins(0, 0, 0, 0)
        self._qwen_exe = QLineEdit(sv.get("exe", "qwen3tts_server.exe"))
        r = QHBoxLayout(); r.addWidget(QLabel("Server 路径")); r.addWidget(self._qwen_exe)
        b = QPushButton("浏览"); b.clicked.connect(lambda: self._br_exe(self._qwen_exe)); r.addWidget(b); ql.addLayout(r)
        self._talker = QLineEdit(sv.get("talker", ""))
        r = QHBoxLayout(); r.addWidget(QLabel("Talker 模型")); r.addWidget(self._talker)
        b = QPushButton("浏览"); b.clicked.connect(lambda: self._br(self._talker)); r.addWidget(b); ql.addLayout(r)
        self._codec = QLineEdit(sv.get("codec", ""))
        r = QHBoxLayout(); r.addWidget(QLabel("Codec 模型")); r.addWidget(self._codec)
        b = QPushButton("浏览"); b.clicked.connect(lambda: self._br(self._codec)); r.addWidget(b); ql.addLayout(r)
        lay.addWidget(self._qwen_box)

        # ── Fish 字段 ──
        self._fish_box = QWidget()
        fl = QVBoxLayout(self._fish_box); fl.setContentsMargins(0, 0, 0, 0)
        self._fish_exe = QLineEdit(sv.get("fish_exe", "fish2_server.exe"))
        r = QHBoxLayout(); r.addWidget(QLabel("Server exe")); r.addWidget(self._fish_exe)
        b = QPushButton("浏览"); b.clicked.connect(lambda: self._br_exe(self._fish_exe)); r.addWidget(b); fl.addLayout(r)
        self._fish_model = QLineEdit(sv.get("fish_model", ""))
        r = QHBoxLayout(); r.addWidget(QLabel("Model (gguf)")); r.addWidget(self._fish_model)
        b = QPushButton("浏览"); b.clicked.connect(lambda: self._br(self._fish_model)); r.addWidget(b); fl.addLayout(r)
        self._fish_tokenizer = QLineEdit(sv.get("fish_tokenizer", ""))
        r = QHBoxLayout(); r.addWidget(QLabel("Tokenizer (json)")); r.addWidget(self._fish_tokenizer)
        b = QPushButton("浏览"); b.clicked.connect(lambda: self._br_json(self._fish_tokenizer)); r.addWidget(b); fl.addLayout(r)
        self._fish_ra = QLineEdit(sv.get("fish_ref_audio", ""))
        r = QHBoxLayout(); r.addWidget(QLabel("参考音频")); r.addWidget(self._fish_ra)
        b = QPushButton("浏览"); b.clicked.connect(lambda: self._br(self._fish_ra)); r.addWidget(b); fl.addLayout(r)
        self._fish_rt = QLineEdit(sv.get("fish_ref_text", ""))
        r = QHBoxLayout(); r.addWidget(QLabel("参考文本")); r.addWidget(self._fish_rt)
        fl.addLayout(r)
        lay.addWidget(self._fish_box)

        self._port = QLineEdit(sv.get("port", "9988"))
        _row("端口", self._port)

        self._lang = QComboBox()
        _row("语言", self._lang)
        self._lang.addItems(["auto", "zh", "en", "ja", "ko"])
        self._lang.setCurrentText(sv.get("lang", "auto"))

        # ── 语音克隆 (Qwen only) ──
        self._mode = QComboBox()
        _row("模式 (Qwen only)", self._mode)
        self._mode.addItems(["Base", "CustomVoice"])
        self._mode.setCurrentText(sv.get("mode", "Base"))
        self._mode.currentTextChanged.connect(self._on_mode)

        self._base_box = QWidget()
        bl = QVBoxLayout(self._base_box); bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(QLabel("── Base 语音克隆 ──"))
        self._ra = QLineEdit(sv.get("ref_audio", ""))
        ra_row = QHBoxLayout()
        ra_row.addWidget(QLabel("参考音频"))
        ra_row.addWidget(self._ra)
        ra_btn = QPushButton("浏览")
        ra_btn.clicked.connect(lambda: self._br(self._ra))
        ra_row.addWidget(ra_btn)
        bl.addLayout(ra_row)
        self._rt = QLineEdit(sv.get("ref_text", ""))
        rt_row = QHBoxLayout()
        rt_row.addWidget(QLabel("参考文本"))
        rt_row.addWidget(self._rt)
        bl.addLayout(rt_row)
        lay.addWidget(self._base_box)

        self._cv_box = QWidget()
        cvl = QVBoxLayout(self._cv_box); cvl.setContentsMargins(0, 0, 0, 0)
        cvl.addWidget(QLabel("── CustomVoice 音色 ──"))
        self._cv_spk = QLineEdit(sv.get("cv_speaker", ""))
        cv_spk_row = QHBoxLayout()
        cv_spk_row.addWidget(QLabel("音色名"))
        cv_spk_row.addWidget(self._cv_spk)
        cvl.addLayout(cv_spk_row)
        lay.addWidget(self._cv_box)

        self._on_model_type(sv.get("model_type", "Qwen3-TTS"))
        self._on_mode(sv.get("mode", "Base"))

        btn = QPushButton("🚀 启动 Server")
        btn.setObjectName("playBtn"); btn.clicked.connect(self._launch)
        lay.addWidget(btn)
        self._status = QLabel(""); lay.addWidget(self._status)

    def _br(self, w):
        p = QFileDialog.getOpenFileName(self, "选择", w.text(), "GGUF (*.gguf);;音频 (*.wav *.mp3);;所有 (*.*)")
        if p[0]: w.setText(p[0])
    def _br_exe(self, w):
        p = QFileDialog.getOpenFileName(self, "选择", w.text(), "exe (*.exe);;所有 (*.*)")
        if p[0]: w.setText(p[0])
    def _br_json(self, w):
        p = QFileDialog.getOpenFileName(self, "选择", w.text(), "JSON (*.json);;所有 (*.*)")
        if p[0]: w.setText(p[0])

    def _on_model_type(self, mt):
        is_qwen = (mt == "Qwen3-TTS")
        self._qwen_box.setVisible(is_qwen)
        self._fish_box.setVisible(not is_qwen)
        self._mode.setVisible(is_qwen)
        self._base_box.setVisible(is_qwen and self._mode.currentText() == "Base")
        self._cv_box.setVisible(is_qwen and self._mode.currentText() == "CustomVoice")
        self._lang.setVisible(is_qwen)

    def _on_mode(self, mode):
        is_qwen = (self._model_type.currentText() == "Qwen3-TTS")
        self._base_box.setVisible(is_qwen and mode == "Base")
        self._cv_box.setVisible(is_qwen and mode == "CustomVoice")

    def _launch(self):
        sv = {
            "model_type": self._model_type.currentText(),
            "port": self._port.text(),
            "exe": self._qwen_exe.text(),
            "talker": self._talker.text(),
            "codec": self._codec.text(),
            "fish_exe": self._fish_exe.text(),
            "fish_model": self._fish_model.text(),
            "fish_tokenizer": self._fish_tokenizer.text(),
            "fish_ref_audio": self._fish_ra.text(),
            "fish_ref_text": self._fish_rt.text(),
            "mode": self._mode.currentText(),
            "ref_audio": self._ra.text(),
            "ref_text": self._rt.text(),
            "cv_speaker": self._cv_spk.text(),
            "lang": self._lang.currentText(),
        }
        prev_type = self._c.get("server", {}).get("model_type", "")
        self._c["server"] = sv
        save_cfg(self._c)

        is_qwen = (sv["model_type"] == "Qwen3-TTS")
        if is_qwen:
            exe = (sv.get("exe") or "").strip() or "qwen3tts_server.exe"
            args = [exe, "--model", sv["talker"], "--codec", sv["codec"],
                    "--port", sv["port"], "--lang", sv["lang"]]
            if sv["mode"] == "CustomVoice":
                if sv.get("cv_speaker"): args += ["--cv-speaker", sv["cv_speaker"]]
            else:
                if sv.get("ref_audio"): args += ["--ref-audio", sv["ref_audio"]]
                if sv.get("ref_text"): args += ["--ref-text", sv["ref_text"]]
        else:
            exe = (sv.get("fish_exe") or "").strip() or "fish2_server.exe"
            args = [exe, "--model", sv["fish_model"], "--tokenizer", sv["fish_tokenizer"],
                    "--port", sv["port"]]
            if sv.get("fish_ref_audio"): args += ["--ref-audio", sv["fish_ref_audio"]]
            if sv.get("fish_ref_text"): args += ["--ref-text", sv["fish_ref_text"]]

        cmd_line = " ".join(f'"{a}"' if " " in a else a for a in args)
        bat = os.path.join(_FILE_DIR, "tts_launch.bat")
        with open(bat, "w", encoding="utf-8") as f:
            f.write(f"@echo off\r\nchcp 65001 >nul\r\nset QWEN3_TTS_CODEC_GPU=1\r\n{cmd_line}\r\npause\r\n")
        os.startfile(bat)
        new_type = sv["model_type"]
        if prev_type and prev_type != new_type:
            self._status.setText("Server 已启动。⚠ 模型类型已变更，请重启阅读器。")
        else:
            self._status.setText("Server 已在新窗口启动，关闭窗口即停止。")
        self._status.setStyleSheet("color:#0a0;")


# ═══════════ 主窗口 ═══════════

class ReaderWin(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("小说朗读器 — TTS Server")
        self.resize(1280, 900); self.setStyleSheet(QSS)
        self._c = load_cfg(); self._chs = []; self._bp = ""; self._ci = 0; self._pi = 0
        self._playing = False
        self._nxt = 0; self._total = 0; self._save_sent = 0; self._gid = 0
        self._synth_done = threading.Event(); self._synth_done.set()
        self._synth_pause = threading.Event(); self._synth_pause.set()
        self._sync_scroll = False

        # Read server port and sample rate from saved config
        sv = self._c.get("server", {})
        if sv.get("port"):
            global SERVER_PORT
            SERVER_PORT = int(sv["port"])
        self._sample_rate = SAMPLE_RATES.get(
            "fish" if sv.get("model_type", "") == "Fish S2" else "qwen", 24000)

        self._build(); self._restore()

    def _build(self):
        cw = QWidget(); self.setCentralWidget(cw)
        lay = QVBoxLayout(cw); lay.setContentsMargins(10, 8, 10, 6); lay.setSpacing(6)

        # ── 工具栏 ──
        tb = QHBoxLayout()
        for t, f in [("📂 打开", self._ob),
                     ("🔧 模型配置", lambda: ServerDialog(self, self._c).exec()),
                     ("🚀 启动", self._launch_server),
                     ("⚙ 设置", lambda: SettingsDialog(self, self._c).exec())]:
            b = QPushButton(t); b.clicked.connect(f); tb.addWidget(b)

        self._hist = QComboBox(); self._hist.setMinimumWidth(140)
        self._hist.currentIndexChanged.connect(self._on_hist)
        tb.addWidget(self._hist)
        b = QPushButton("📜 历史"); b.clicked.connect(self._hist_dlg); tb.addWidget(b)
        b = QPushButton("⛶ 全屏"); b.clicked.connect(self._fullscreen); tb.addWidget(b)
        b = QPushButton("💾 保存退出"); b.clicked.connect(self._save_quit); tb.addWidget(b)

        tb.addWidget(QLabel(" A-"))
        self._fs = QSlider(Qt.Orientation.Horizontal); self._fs.setRange(8, 64)
        self._fs.setValue(23); self._fs.setFixedWidth(80)
        self._fs.valueChanged.connect(self._on_font); tb.addWidget(self._fs)
        tb.addWidget(QLabel("A+"))

        tb.addStretch()
        self._chap_btn = QPushButton("📑 目录"); self._chap_btn.clicked.connect(self._toggle_chap)
        tb.addWidget(self._chap_btn)
        self._ml = QLabel(f"Server: 127.0.0.1:{SERVER_PORT}"); self._ml.setStyleSheet("color:#0a0;"); tb.addWidget(self._ml)
        lay.addLayout(tb)

        # ── 正文 ──
        self._tx = QPlainTextEdit(); self._tx.setReadOnly(True)
        f = self._tx.font(); f.setPixelSize(self._c.get("font_size", 23)); self._tx.setFont(f)
        self._tx.wheelEvent = lambda e: self._tx.verticalScrollBar().setValue(
            self._tx.verticalScrollBar().value() + (-1 if e.angleDelta().y() > 0 else 1))
        self._tx.verticalScrollBar().valueChanged.connect(self._on_scroll)
        lay.addWidget(self._tx)

        # ── 章节目录弹窗 ──
        self._chap_popup = QDialog(self); self._chap_popup.setWindowTitle("目录")
        self._chap_popup.setFixedSize(280, 500); self._chap_popup.setStyleSheet(QSS)
        cl_ = QVBoxLayout(self._chap_popup); cl_.setContentsMargins(8, 8, 8, 8)
        tf = QLineEdit(); tf.setPlaceholderText("搜索..."); tf.textChanged.connect(self._search_chap)
        cl_.addWidget(tf)
        self._cl = QListWidget(); self._cl.currentRowChanged.connect(self._on_chap)
        cl_.addWidget(self._cl)

        # ── 播放栏 ──
        bb = QHBoxLayout()
        self._play_btn = QPushButton("▶ 播放"); self._play_btn.setObjectName("playBtn")
        self._play_btn.clicked.connect(self._tp); bb.addWidget(self._play_btn)
        self._resume_btn = QPushButton("⏵ 续播"); self._resume_btn.setObjectName("playBtn")
        self._resume_btn.clicked.connect(self._resume); self._resume_btn.hide(); bb.addWidget(self._resume_btn)
        for t, f in [("⏮", self._pr), ("⏭", self._nx)]:
            b = QPushButton(t); b.clicked.connect(f); b.setFixedWidth(36); bb.addWidget(b)
        self._prog = QProgressBar(); self._prog.setMaximum(1); bb.addWidget(self._prog)
        self._info = QLabel("就绪"); self._info.setStyleSheet("color:#888;"); bb.addWidget(self._info)
        self._st = QLabel(""); self._st.setStyleSheet("color:#fa0;"); bb.addWidget(self._st)
        bb.addStretch()
        self._sync_btn = QPushButton("⟳ 同步滚动"); self._sync_btn.setCheckable(True)
        self._sync_btn.setFixedWidth(100)
        self._sync_btn.toggled.connect(lambda v: setattr(self, '_sync_scroll', v)); bb.addWidget(self._sync_btn)
        self._loc = QLabel(""); self._loc.setStyleSheet("color:#888;")
        bb.addWidget(self._loc); lay.addLayout(bb)

    # ── 书本 + 历史 ──
    def _load_book(self, path):
        self._bp = path; self._chs = parse_book(path)
        self.setWindowTitle(f"小说朗读器 — {os.path.basename(path)}")
        fs = self._c.get("font_size", 23); f = self._tx.font(); f.setPixelSize(fs)
        self._tx.setFont(f); self._fs.setValue(fs)
        self._tx.clear(); self._cl.clear()
        self._cl.addItems([f"{t}  ({len(ps)}段)" for t, ps in self._chs])
        k = os.path.abspath(path); s = self._c.get("books", {}).get(k, {})
        c = min(s.get("chapter", 0), len(self._chs) - 1) if self._chs else 0
        pi = s.get("paragraph", 0); self._ci = c; self._pi = pi
        self._cl.setCurrentRow(c); self._show(c, pi); self._ri()
        self._refresh_hist()

    def _ob(self):
        p, _ = QFileDialog.getOpenFileName(self, "打开", "", "文本 (*.txt);;所有 (*.*)")
        if not p: return
        self._sv(); self._save_hist(p); self._load_book(p)

    def _save_hist(self, path):
        hs = self._c.setdefault("history", []); k = os.path.abspath(path)
        if k in hs: hs.remove(k)
        hs.insert(0, k); del hs[10:]; save_cfg(self._c)

    def _refresh_hist(self):
        self._hist.blockSignals(True); self._hist.clear()
        for k in self._c.get("history", []):
            self._hist.addItem(os.path.basename(k), k)
        if self._bp:
            k = os.path.abspath(self._bp)
            idx = self._hist.findData(k)
            if idx >= 0: self._hist.setCurrentIndex(idx)
        self._hist.blockSignals(False)

    def _on_hist(self, idx):
        if idx < 0: return
        path = self._hist.itemData(idx)
        if path and path != os.path.abspath(self._bp):
            self._sv(); self._load_book(path)

    def _hist_dlg(self):
        d = QDialog(self); d.setWindowTitle("阅读历史"); d.setFixedSize(400, 350); d.setStyleSheet(QSS)
        l = QVBoxLayout(d); l.setContentsMargins(10, 10, 10, 10)
        lst = QListWidget()
        books = self._c.get("books", {})
        items = sorted(books.items(), key=lambda x: x[1].get("updated", ""), reverse=True)
        for k, v in items:
            nm = os.path.basename(k); ch = v.get("chapter", 0) + 1
            tot = len(parse_book(k)) if os.path.exists(k) else "?"
            lst.addItem(f"{nm}  (章{ch}/{tot})")
            lst.item(lst.count() - 1).setData(Qt.ItemDataRole.UserRole, k)
        l.addWidget(lst)
        row = QHBoxLayout()
        ld = QPushButton("载入选中"); ld.clicked.connect(lambda: self._hist_load(lst))
        row.addWidget(ld)
        rm = QPushButton("删除选中"); rm.clicked.connect(lambda: self._hist_rm(lst))
        row.addWidget(rm); l.addLayout(row)
        d.exec()

    def _hist_load(self, lst):
        it = lst.currentItem()
        if it:
            path = it.data(Qt.ItemDataRole.UserRole)
            if path and os.path.exists(path):
                self._sv(); self._load_book(path); self._save_hist(path)
                self.sender().parent().accept()

    def _hist_rm(self, lst):
        it = lst.currentItem()
        if it:
            path = it.data(Qt.ItemDataRole.UserRole)
            self._c.get("books", {}).pop(path, None)
            self._c.get("history", []).remove(path) if path in self._c.get("history", []) else None
            save_cfg(self._c); lst.takeItem(lst.currentRow())

    def _show(self, ci, hp=0):
        if not self._chs: return
        t, ps = self._chs[ci]
        lines = [f"═══ {t} ═══", ""]
        for p in ps: lines.append(p); lines.append("")
        self._tx.setPlainText("\n".join(lines))
        k = os.path.abspath(self._bp) if self._bp else ""
        saved = self._c.get("books", {}).get(k, {})
        line = saved.get("top_line", 0)
        if line > 0:
            sb = self._tx.verticalScrollBar()
            sb.setValue(min(line, sb.maximum()))

    def _on_scroll(self):
        if not self._chs: return
        _, ps = self._chs[self._ci]
        if not ps: return
        sb = self._tx.verticalScrollBar()
        pct = sb.value() / max(sb.maximum(), 1)
        self._pi = min(int(pct * len(ps)), len(ps) - 1)

    def _on_font(self, v):
        f = self._tx.font(); f.setPixelSize(v); self._tx.setFont(f)
        self._c["font_size"] = v; save_cfg(self._c)

    def _on_chap(self, idx):
        if idx >= 0: self._ci = idx; self._pi = 0; self._show(idx, 0)
        self._tx.verticalScrollBar().setValue(0); self._ri()

    def _search_chap(self, txt):
        for i in range(self._cl.count()):
            self._cl.setRowHidden(i, txt.lower() not in self._cl.item(i).text().lower())

    def _toggle_chap(self):
        if self._chap_popup.isVisible(): self._chap_popup.hide()
        else: self._chap_popup.show()

    def _ri(self):
        if self._chs:
            self._info.setText(f"{self._chs[self._ci][0]} · 段{self._pi + 1}")
            self._loc.setText(f"章{self._ci + 1}/{len(self._chs)}")

    # ── Server 启动 ──
    def _launch_server(self):
        sv = self._c.get("server", {})
        self._sample_rate = SAMPLE_RATES.get(
            "fish" if sv.get("model_type", "") == "Fish S2" else "qwen", 24000)
        bat = os.path.join(_FILE_DIR, "tts_launch.bat")
        if os.path.exists(bat):
            os.startfile(bat)
            self._ml.setText("Server 启动中..."); self._ml.setStyleSheet("color:#fa0;")
        else:
            QMessageBox.warning(self, "提示", "请先在「模型配置」中设置并启动一次 Server")

    # ── 播放 ──
    def _tp(self):
        if self._playing: self._ps()
        else: self._pl()

    def _pl(self):
        if not self._chs: return
        _, ps = self._chs[self._ci]
        cur = max(0, self._pi - 5)
        cur = min(cur, len(ps) - 1) if ps else 0

        dlg = QDialog(self); dlg.setWindowTitle(f"选择起始段 (共 {len(ps)} 段)")
        dlg.resize(550, 650); dlg.setStyleSheet(QSS)
        lv = QVBoxLayout(dlg); lv.setContentsMargins(8, 8, 8, 8)
        lst = QListWidget()
        for i, p in enumerate(ps):
            txt = p.replace("\n", " ").strip()
            lbl = f"[{i}] {txt[:80]}{'...' if len(txt) > 80 else ''}"
            lst.addItem(lbl)
        lst.setCurrentRow(cur)
        lst.scrollToItem(lst.item(cur), QAbstractItemView.ScrollHint.PositionAtCenter)
        lv.addWidget(lst)
        row = QHBoxLayout()
        btn_top = QPushButton("从本章开头")
        ok = QPushButton("从此段开始"); ok.setObjectName("playBtn")
        row.addWidget(btn_top); row.addStretch(); row.addWidget(ok)
        lv.addLayout(row)
        ok.clicked.connect(lambda: (setattr(self, '_save_sent', lst.currentRow()), dlg.accept()))
        btn_top.clicked.connect(lambda: (setattr(self, '_save_sent', 0), setattr(self, '_pi', 0), dlg.accept()))
        lst.itemDoubleClicked.connect(lambda item: (setattr(self, '_save_sent', lst.row(item)), dlg.accept()))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._playing = True; self._play_btn.setText("⏸ 暂停")
        self._resume_btn.hide()
        if (hasattr(self, '_paused_gid') and self._paused_gid != 0
                and self._save_sent == getattr(self, '_paused_para', -1)):
            self._synth_pause.set()
            self._gid = self._paused_gid
            self._nxt = self._paused_nxt
            self._prog.setMaximum(self._paused_total)
            self._prog.setValue(self._paused_nxt)
            self._st.setText(f"续播中 ({self._nxt}/{self._paused_total})")
            self._paused_gid = 0
            self._pn()
        else:
            if hasattr(self, '_paused_gid') and self._paused_gid != 0:
                self._synth_pause.set()
                self._gid = 0; self._paused_gid = 0
                if not self._synth_done.wait(10):
                    self._st.setText("等待上一个合成完成..."); QApplication.processEvents()
                    self._synth_done.wait()
            self._sap()

    def _resume(self):
        if not hasattr(self, '_paused_gid') or self._paused_gid == 0:
            return
        self._synth_pause.set()
        self._gid = self._paused_gid
        self._nxt = self._paused_nxt
        self._save_sent = self._paused_para
        self._prog.setMaximum(self._paused_total)
        self._prog.setValue(self._paused_nxt)
        self._st.setText(f"续播中 ({self._nxt}/{self._paused_total})")
        self._paused_gid = 0
        self._resume_btn.hide(); self._play_btn.setText("⏸ 暂停")
        self._playing = True
        self._pn()

    def _ps(self):
        self._playing = False; self._play_btn.setText("▶ 播放")
        self._synth_pause.clear()
        self._resume_btn.show()
        sd.stop(); self._sv()
        self._paused_gid = self._gid
        self._paused_nxt = max(0, self._nxt - 1)
        self._paused_para = self._para_start
        self._paused_total = self._total
        self._save_sent = self._para_start + self._sent_in_para(self._nxt)
        self._info.setText("已暂停"); self._st.setText("")

    def _sent_in_para(self, nxt):
        if not hasattr(self, '_sens_para_map') or nxt <= 0:
            return 0
        for sp in self._sens_para_map:
            if sp[0] >= nxt:
                return max(0, sp[1] - 1)
        return self._sens_para_map[-1][1] if self._sens_para_map else 0

    def _sap(self):
        if not self._playing: return
        self._synth_pause.set()
        ci = self._ci
        if ci >= len(self._chs): self._dn(); return
        _, ps = self._chs[ci]
        para_start = self._save_sent; self._para_start = para_start
        self._save_sent = 0; self._pi = para_start
        self._ri()
        text = "".join(ps[para_start:]); cfg = self._c
        sens = split_text(text)
        self._sens_para_map = []
        acc, pi = 0, para_start
        for i, s in enumerate(sens):
            while pi < len(ps) and acc >= len(ps[pi]):
                acc -= len(ps[pi]); pi += 1
            self._sens_para_map.append((i, pi))
            acc += len(s)

        self._nxt = 0; self._total = len(sens)
        self._prog.setMaximum(len(sens)); self._prog.setValue(0)
        self._st.setText(f"合成 {len(sens)} 句...")
        QApplication.processEvents()

        gid = id(self); self._gid = gid
        self._wavs = {}; self._wavs_lock = threading.Lock()
        self._sens = sens; self._sens_cfg = cfg
        self._synth_done.clear()

        def _seq():
            try:
                for idx, txt in enumerate(sens):
                    if self._gid != gid: return
                    self._synth_pause.wait()
                    if self._gid != gid: return
                    try:
                        pcm = TtsEngine.synth(txt, cfg.get("speed", 1.0))
                        if self._gid != gid: return
                        pcm = np.clip(pcm, -1, 1) * 32767
                        with self._wavs_lock:
                            self._wavs[idx] = pcm.astype(np.int16).tobytes()
                    except Exception as e:
                        with self._wavs_lock:
                            self._wavs[idx] = e
            finally:
                self._synth_done.set()
        threading.Thread(target=_seq, daemon=True).start()
        self._pn()

    def _av(self):
        if not self._playing: return
        self._ci += 1; self._pi = 0; self._save_sent = 0; self._prog.setValue(0)
        if self._ci >= len(self._chs): self._dn(); return
        self._cl.setCurrentRow(self._ci); self._show(self._ci, 0)
        self._tx.verticalScrollBar().setValue(0)
        self._sv(); self._sap()

    def _pn(self):
        if not self._playing: return
        with self._wavs_lock:
            r = self._wavs.get(self._nxt)
        if r is None:
            QTimer.singleShot(100, self._pn); return
        if isinstance(r, Exception):
            with self._wavs_lock: del self._wavs[self._nxt]
            self._st.setText(f"错误: {r}"); self._st.setStyleSheet("color:#f00;"); return
        if not r:
            with self._wavs_lock: del self._wavs[self._nxt]
            self._nxt += 1; QTimer.singleShot(0, self._pn); return
        self._nxt += 1; self._prog.setValue(self._nxt)
        self._st.setText(f"播放中 ({self._nxt}/{self._total})")
        idx = self._nxt - 1
        if idx > 0:
            with self._wavs_lock: self._wavs.pop(idx - 1, None)
        if self._sync_scroll and self._total > 0 and idx < len(self._sens):
            doc = self._tx.document()
            needle = self._sens[idx][:30]
            if needle:
                cursor = doc.find(needle)
                if not cursor.isNull():
                    self._tx.setTextCursor(cursor)
                    self._tx.ensureCursorVisible()
        print(f"[TTS {idx}/{self._total}] {self._sens[idx][:60]}")
        sd.stop()
        pcm = np.frombuffer(r, dtype=np.int16).astype(np.float32) / 32767.0
        vol = self._sens_cfg.get("volume", 1.0)
        if vol != 1.0: pcm = np.clip(pcm * vol, -1, 1)
        sd.play(pcm.astype(np.float32), self._sample_rate)
        def _w():
            if not self._playing: sd.stop(); return
            try:
                if sd.get_stream() is not None and sd.get_stream().active:
                    QTimer.singleShot(200, _w)
                elif self._nxt >= self._total:
                    self._av()
                else:
                    self._pn()
            except Exception:
                if self._nxt >= self._total: self._av()
                else: self._pn()
        _w()

    def _dn(self):
        self._playing = False; self._play_btn.setText("▶ 播放")
        self._st.setText("朗读完成"); self._st.setStyleSheet("color:#0a0;"); self._sv()

    def _pr(self):
        if self._ci > 0: self._ci -= 1; self._pi = 0
        self._cl.setCurrentRow(self._ci); self._show(self._ci, 0)
        self._tx.verticalScrollBar().setValue(0); self._ri()

    def _nx(self):
        self._playing = False; sd.stop()
        if self._ci + 1 < len(self._chs):
            self._ci += 1; self._pi = 0; self._cl.setCurrentRow(self._ci); self._show(self._ci, 0)
        self._tx.verticalScrollBar().setValue(0); self._ri(); self._st.setText("")

    def _sv(self):
        if not self._bp: return
        c = self._tx.cursorForPosition(QPoint(10, 10))
        self._c.setdefault("books", {})[os.path.abspath(self._bp)] = {
            "chapter": self._ci, "paragraph": self._pi,
            "top_line": c.blockNumber(),
            "updated": datetime.now().isoformat()
        }
        save_cfg(self._c)

    def _restore(self):
        books = self._c.get("books", {}); bs = sorted(books.items(),
                                                       key=lambda x: x[1].get("updated", ""), reverse=True)
        if not bs: self._refresh_hist(); return
        k, v = bs[0]
        if not os.path.exists(k): self._refresh_hist(); return
        self._load_book(k)

    def _save_quit(self):
        self._sv(); sd.stop(); save_cfg(self._c); QApplication.quit()

    def _fullscreen(self):
        if self.isFullScreen(): self.showNormal()
        else: self.showFullScreen()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_F11: self._fullscreen()
        elif e.key() == Qt.Key.Key_Escape and self.isFullScreen(): self.showNormal()
        else: super().keyPressEvent(e)

    def closeEvent(self, e):
        self._sv(); sd.stop(); save_cfg(self._c)
        e.accept()


if __name__ == "__main__":
    ###隐藏控制台窗口
    if sys.platform == "win32":
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    app = QApplication(sys.argv); app.setStyle("Fusion")
    win = ReaderWin(); win.show()
    sys.exit(app.exec())
