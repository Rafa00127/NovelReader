"""Simple TTS GUI — 文本转语音，试听后保存。"""
import ctypes, os, sys, json, struct, socket, time, threading
import numpy as np
import sounddevice as sd
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *

_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(_FILE_DIR)

# ═══════════ 全局常量 ═══════════
CONFIG = os.path.join(_FILE_DIR, "tts_gui_config.json")
SAMPLE_RATES = {"qwen": 24000, "fish": 44100}
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 9988

QSS = """
* { font-family: "Microsoft YaHei UI", sans-serif; }
QMainWindow, QWidget { background-color: #111; color: #ddd; }
QPlainTextEdit, QTextEdit {
    background-color: #1a1a18; color: #c8b878; border: 1px solid #333;
    border-radius: 8px; padding: 16px; font-size: 16px;
    selection-background-color: #3a3520;
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
QPushButton#primaryBtn { background: #2d5a2d; color: #fff; font-size: 14px; font-weight: bold; padding: 10px 24px; border: none; }
QPushButton#primaryBtn:hover { background: #3a6a3a; }
QComboBox {
    background: #252525; color: #ddd; border: 1px solid #444;
    border-radius: 6px; padding: 6px 10px;
}
QComboBox::drop-down { border: none; }
QComboBox QAbstractItemView { background: #252525; color: #ddd; selection-background-color: #333; }
QLabel { background: transparent; }
"""

# ═══════════ 工具函数 ═══════════

def load_cfg():
    if os.path.exists(CONFIG):
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"server": {}}

def save_cfg(c):
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False, indent=2)


# ═══════════ TTS 引擎 ═══════════

class TtsEngine:
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
    def synth(cls, text, port=9988):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(120)
        sock.connect((SERVER_HOST, port))
        payload = text.encode("utf-8")
        sock.sendall(struct.pack(">i", len(payload)))
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
        return arr


# ═══════════ 模型配置弹窗（复用 reader 的 ServerDialog 逻辑） ═══════════

class ModelConfigDialog(QDialog):
    def __init__(self, parent, cfg):
        super().__init__(parent); self._c = cfg
        self.setWindowTitle("模型配置"); self.setFixedSize(620, 500)
        self.setStyleSheet(QSS)
        lay = QVBoxLayout(self); lay.setSpacing(6)
        sv = cfg.get("server", {})

        def _row(label, *widgets):
            r = QHBoxLayout(); r.addWidget(QLabel(label))
            for w in widgets: r.addWidget(w)
            lay.addLayout(r)

        self._model_type = QComboBox()
        _row("模型类型", self._model_type)
        self._model_type.addItems(["Qwen3-TTS", "Fish S2"])
        self._model_type.setCurrentText(sv.get("model_type", "Qwen3-TTS"))
        self._model_type.currentTextChanged.connect(self._on_model_type)
        lay.addWidget(self._model_type)

        # Qwen
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
        self._mode = QComboBox()
        r = QHBoxLayout(); r.addWidget(QLabel("模式")); r.addWidget(self._mode)
        self._mode.addItems(["Base", "CustomVoice"])
        self._mode.setCurrentText(sv.get("mode", "Base"))
        self._mode.currentTextChanged.connect(self._on_qwen_mode)
        ql.addLayout(r)

        self._qwen_base = QWidget()
        bl = QVBoxLayout(self._qwen_base); bl.setContentsMargins(0, 0, 0, 0)
        self._ref_audio = QLineEdit(sv.get("ref_audio", ""))
        r = QHBoxLayout(); r.addWidget(QLabel("参考音频")); r.addWidget(self._ref_audio)
        b = QPushButton("浏览"); b.clicked.connect(lambda: self._br(self._ref_audio)); r.addWidget(b); bl.addLayout(r)
        self._ref_text = QLineEdit(sv.get("ref_text", ""))
        r = QHBoxLayout(); r.addWidget(QLabel("参考文本")); r.addWidget(self._ref_text)
        bl.addLayout(r)
        ql.addWidget(self._qwen_base)

        self._qwen_cv = QWidget()
        cvl = QVBoxLayout(self._qwen_cv); cvl.setContentsMargins(0, 0, 0, 0)
        self._cv_speaker = QLineEdit(sv.get("cv_speaker", ""))
        r = QHBoxLayout(); r.addWidget(QLabel("音色名")); r.addWidget(self._cv_speaker)
        cvl.addLayout(r)
        ql.addWidget(self._qwen_cv)

        self._on_qwen_mode(sv.get("mode", "Base"))
        lay.addWidget(self._qwen_box)

        # Fish
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

        self._on_model_type(sv.get("model_type", "Qwen3-TTS"))

        btn = QPushButton("🚀 启动 Server")
        btn.setObjectName("primaryBtn"); btn.clicked.connect(self._launch)
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

    def _on_qwen_mode(self, mode):
        self._qwen_base.setVisible(mode == "Base")
        self._qwen_cv.setVisible(mode == "CustomVoice")

    def _launch(self):
        sv = {
            "model_type": self._model_type.currentText(),
            "port": self._port.text(),
            "exe": self._qwen_exe.text(), "talker": self._talker.text(),
            "codec": self._codec.text(),
            "mode": self._mode.currentText(), "cv_speaker": self._cv_speaker.text(),
            "ref_audio": self._ref_audio.text(), "ref_text": self._ref_text.text(),
            "fish_exe": self._fish_exe.text(), "fish_model": self._fish_model.text(),
            "fish_tokenizer": self._fish_tokenizer.text(),
            "fish_ref_audio": self._fish_ra.text(), "fish_ref_text": self._fish_rt.text(),
        }
        prev_type = self._c.get("server", {}).get("model_type", "")
        self._c["server"] = sv
        save_cfg(self._c)

        is_qwen = (sv["model_type"] == "Qwen3-TTS")
        if is_qwen:
            exe = (sv.get("exe") or "").strip() or "qwen3tts_server.exe"
            args = [exe, "--model", sv["talker"], "--codec", sv["codec"],
                    "--port", sv["port"], "--lang", sv.get("lang", "auto")]
            if sv.get("mode") == "CustomVoice":
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
        bat = os.path.join(_FILE_DIR, "tts_gui_launch.bat")
        with open(bat, "w", encoding="utf-8") as f:
            f.write(f"@echo off\r\nchcp 65001 >nul\r\nset QWEN3_TTS_CODEC_GPU=1\r\n{cmd_line}\r\npause\r\n")
        os.startfile(bat)

        new_type = sv["model_type"]
        if prev_type and prev_type != new_type:
            self._status.setText("Server 已启动。⚠ 模型类型已变更，请重启 GUI。")
        else:
            self._status.setText("Server 已在新窗口启动，关闭窗口即停止。")


# ═══════════ 主窗口 ═══════════

class SimpleTtsGui(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("Simple TTS")
        self.resize(700, 600); self.setStyleSheet(QSS)
        self._c = load_cfg()
        self._pcm = None
        self._playing = False

        sv = self._c.get("server", {})
        self._sample_rate = SAMPLE_RATES.get(
            "fish" if sv.get("model_type", "") == "Fish S2" else "qwen", 24000)
        if sv.get("port"):
            global SERVER_PORT
            SERVER_PORT = int(sv["port"])

        self._build()

    def _build(self):
        cw = QWidget(); self.setCentralWidget(cw)
        lay = QVBoxLayout(cw); lay.setContentsMargins(12, 10, 12, 10); lay.setSpacing(8)

        # ── 工具栏 ──
        tb = QHBoxLayout()
        cfg_btn = QPushButton("🔧 模型配置")
        cfg_btn.clicked.connect(lambda: ModelConfigDialog(self, self._c).exec())
        tb.addWidget(cfg_btn)
        launch_btn = QPushButton("🚀 启动 Server")
        launch_btn.clicked.connect(self._launch_server)
        tb.addWidget(launch_btn)
        self._status_lbl = QLabel("未连接"); self._status_lbl.setStyleSheet("color:#888;")
        tb.addWidget(self._status_lbl)
        tb.addStretch()
        lay.addLayout(tb)

        # ── 文本输入 ──
        lay.addWidget(QLabel("输入文本"))
        self._text_edit = QPlainTextEdit()
        self._text_edit.setPlaceholderText("在这里输入要合成的文本...")
        self._text_edit.setMinimumHeight(200)
        lay.addWidget(self._text_edit)

        # ── 操作按钮 ──
        btn_row = QHBoxLayout()
        self._synth_btn = QPushButton("🎤 合成语音")
        self._synth_btn.setObjectName("primaryBtn")
        self._synth_btn.clicked.connect(self._synthesize)
        btn_row.addWidget(self._synth_btn)

        self._play_btn = QPushButton("▶ 试听")
        self._play_btn.setEnabled(False)
        self._play_btn.clicked.connect(self._toggle_play)
        btn_row.addWidget(self._play_btn)

        self._save_btn = QPushButton("💾 保存")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save)
        btn_row.addWidget(self._save_btn)

        btn_row.addStretch()
        self._info_lbl = QLabel(""); btn_row.addWidget(self._info_lbl)
        lay.addLayout(btn_row)

    # ── Server 启动 ──
    def _launch_server(self):
        bat = os.path.join(_FILE_DIR, "tts_gui_launch.bat")
        if not os.path.exists(bat):
            QMessageBox.warning(self, "提示", "请先在「模型配置」中设置并启动一次 Server")
            return
        os.startfile(bat)
        sv = self._c.get("server", {})
        self._sample_rate = SAMPLE_RATES.get(
            "fish" if sv.get("model_type", "") == "Fish S2" else "qwen", 24000)
        self._status_lbl.setText("Server 启动中..."); self._status_lbl.setStyleSheet("color:#fa0;")
        port = int(sv.get("port", SERVER_PORT))

        def _poll():
            for _ in range(20):
                time.sleep(0.5)
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(1)
                    s.connect((SERVER_HOST, port))
                    s.close()
                    QMetaObject.invokeMethod(
                        self._status_lbl, "setText", Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, f"{SERVER_HOST}:{port}"))
                    QMetaObject.invokeMethod(
                        self._status_lbl, "setStyleSheet", Qt.ConnectionType.QueuedConnection,
                        Q_ARG(str, "color:#0a0;"))
                    return
                except (ConnectionRefusedError, socket.timeout, OSError):
                    continue
            QMetaObject.invokeMethod(
                self._status_lbl, "setText", Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, "Server 超时"))
            QMetaObject.invokeMethod(
                self._status_lbl, "setStyleSheet", Qt.ConnectionType.QueuedConnection,
                Q_ARG(str, "color:#f00;"))

        threading.Thread(target=_poll, daemon=True).start()

    # ── 合成 ──
    def _synthesize(self):
        text = self._text_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "请先输入文本")
            return

        self._pcm = None
        self._play_btn.setEnabled(False)
        self._save_btn.setEnabled(False)
        self._synth_btn.setEnabled(False)
        self._synth_btn.setText("⏳ 合成中...")
        self._info_lbl.setText("")
        QApplication.processEvents()

        sv = self._c.get("server", {})
        port = int(sv.get("port", SERVER_PORT))

        def _run():
            try:
                pcm = TtsEngine.synth(text, port)
                self._pcm = pcm
                duration = len(pcm) / self._sample_rate
                self._synth_btn.setText("🎤 重新合成")
                self._synth_btn.setEnabled(True)
                self._play_btn.setEnabled(True)
                self._save_btn.setEnabled(True)
                self._info_lbl.setText(f"时长 {duration:.1f}s | {len(pcm)} samples")
                self._info_lbl.setStyleSheet("color:#0a0;")
            except Exception as e:
                self._synth_btn.setText("🎤 合成语音")
                self._synth_btn.setEnabled(True)
                self._info_lbl.setText(f"合成失败: {e}")
                self._info_lbl.setStyleSheet("color:#f00;")

        threading.Thread(target=_run, daemon=True).start()

    # ── 试听 ──
    def _toggle_play(self):
        if self._pcm is None:
            return
        if self._playing:
            sd.stop()
            self._playing = False
            self._play_btn.setText("▶ 试听")
        else:
            self._playing = True
            self._play_btn.setText("⏸ 停止")
            sd.play(self._pcm.astype(np.float32), self._sample_rate)

            def _wait():
                try:
                    if sd.get_stream() is not None and sd.get_stream().active:
                        QTimer.singleShot(200, _wait)
                    else:
                        self._playing = False
                        self._play_btn.setText("▶ 试听")
                except Exception:
                    self._playing = False
                    self._play_btn.setText("▶ 试听")

            QTimer.singleShot(200, _wait)

    # ── 保存 ──
    def _save(self):
        if self._pcm is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存音频", "output.wav",
                                               "WAV (*.wav);;所有 (*.*)")
        if not path:
            return
        import wave
        pcm16 = np.clip(self._pcm, -1, 1) * 32767
        with wave.open(path, "w") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            wf.writeframes(pcm16.astype(np.int16).tobytes())
        self._info_lbl.setText(f"已保存: {os.path.basename(path)}")
        self._info_lbl.setStyleSheet("color:#0a0;")

    def closeEvent(self, e):
        sd.stop(); save_cfg(self._c); e.accept()


if __name__ == "__main__":
    if sys.platform == "win32":
        ctypes.windll.user32.ShowWindow(ctypes.windll.kernel32.GetConsoleWindow(), 0)
    app = QApplication(sys.argv); app.setStyle("Fusion")
    win = SimpleTtsGui(); win.show()
    sys.exit(app.exec())
