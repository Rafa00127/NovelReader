"""小说朗读器 — PyQt6 现代深色 UI + CrispASR Qwen3-TTS DLL 后端。"""
import os, sys, re, json, threading, queue, struct, ctypes, wave, tempfile
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
SAMPLE_RATE = 24000

CHAPTER_RE = [
    re.compile(r'^第[\d一二三四五六七八九十百千万]*章'),
    re.compile(r'^第[\d一二三四五六七八九十百千万]*[节回]'),
    re.compile(r'^Chapter\s+\d+', re.IGNORECASE),
    re.compile(r'^第\s*\d+\s*章'), re.compile(r'^\d+[\.、]\s+\S'),
    re.compile(r'^[一二三四五六七八九十百千万]+、'),
]

# ═══════════ DLL 加载 ═══════════
_DLL_DIR = os.path.join(_FILE_DIR, "dll")
os.add_dll_directory(_DLL_DIR)
for p in os.environ.get("PATH", "").split(";"):
    p = p.strip()
    if p:
        try:
            os.add_dll_directory(p)
        except OSError:
            pass

def _dll_path(s):
    """Encode a path for the DLL. On Windows, the C runtime uses the
    system ANSI code page (e.g. GBK), not UTF-8."""
    if sys.platform == "win32":
        return s.encode("mbcs")  # mbcs = system default ANSI code page
    return s.encode()

class TtsParams(ctypes.Structure):
    _fields_ = [
        ("n_threads",       ctypes.c_int),
        ("verbosity",       ctypes.c_int),
        ("use_gpu",         ctypes.c_bool),
        ("temperature",     ctypes.c_float),
        ("seed",            ctypes.c_uint64),
        ("max_codec_steps", ctypes.c_int),
        ("flash_attn",      ctypes.c_bool),
    ]

_dll = ctypes.CDLL(os.path.join(_DLL_DIR, "qwen3_tts_shared.dll"))
_dll.qwen3_tts_context_default_params.restype = TtsParams
_dll.qwen3_tts_init_from_file.argtypes = [ctypes.c_char_p, TtsParams]
_dll.qwen3_tts_init_from_file.restype  = ctypes.c_void_p
_dll.qwen3_tts_set_codec_path.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_dll.qwen3_tts_set_codec_path.restype  = ctypes.c_int
_dll.qwen3_tts_set_voice_prompt_with_text.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
_dll.qwen3_tts_set_voice_prompt_with_text.restype  = ctypes.c_int
_dll.qwen3_tts_set_voice_prompt_xvec_only.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_dll.qwen3_tts_set_voice_prompt_xvec_only.restype  = ctypes.c_int
_dll.qwen3_tts_is_custom_voice.argtypes = [ctypes.c_void_p]
_dll.qwen3_tts_is_custom_voice.restype  = ctypes.c_int
_dll.qwen3_tts_n_speakers.argtypes = [ctypes.c_void_p]
_dll.qwen3_tts_n_speakers.restype  = ctypes.c_int
_dll.qwen3_tts_get_speaker_name.argtypes = [ctypes.c_void_p, ctypes.c_int]
_dll.qwen3_tts_get_speaker_name.restype  = ctypes.c_char_p
_dll.qwen3_tts_set_speaker_by_name.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_dll.qwen3_tts_set_speaker_by_name.restype  = ctypes.c_int
_dll.qwen3_tts_set_language.argtypes = [ctypes.c_void_p, ctypes.c_int]
_dll.qwen3_tts_set_language.restype  = ctypes.c_int
_dll.qwen3_tts_synthesize.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
_dll.qwen3_tts_synthesize.restype  = ctypes.POINTER(ctypes.c_float)
_dll.qwen3_tts_pcm_free.argtypes = [ctypes.POINTER(ctypes.c_float)]
_dll.qwen3_tts_pcm_free.restype  = None
_dll.qwen3_tts_free.argtypes = [ctypes.c_void_p]
_dll.qwen3_tts_free.restype  = None

# language name → codec_language_id (Qwen3-TTS token IDs)
_LANG_TO_ID = {
    "auto": -1, "zh": 2055, "chinese": 2055,
    "en": 2050, "english": 2050,
    "ja": 2058, "japanese": 2058,
    "ko": 2064, "korean": 2064,
}

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
            if buf and not s: buf.append("")  # preserve paragraph break
        elif any(p.match(s.strip()) for p in CHAPTER_RE) and len(s.strip()) <= 60:
            if buf: ch.append((title, buf))
            title = s.strip(); buf = []
        else:
            buf.append(s)
    if buf: ch.append((title, buf))
    return ch

def split_text(text):
    """Split into semantically complete sentences for natural prosody.
    Sentence-ending punctuation (.!?。！？…) produces final segments.
    Segments > 45 chars are additionally split at commas / semicolons."""
    MAX_LEN = 45
    text = re.sub(r'…{2,}', '…', text)
    # Phase 1: split on sentence-final punctuation.  … is NOT a sentence
    # boundary (it's a hesitation/trailing-off, not a full stop).
    raw = [s for s in re.split(r'(?<=[.。!！?？\n])', text) if s.strip()]
    # Move leading closing quotes (ASCII + curly/smart + CJK) from a
    # segment to the end of the previous one.
    _closing_q = r'」』"''”」”＂'  # including Unicode right curly quote
    for i in range(len(raw) - 1, 0, -1):
        m = re.match(r'^([' + _closing_q + r']+)', raw[i])
        if m:
            raw[i - 1] = raw[i - 1].rstrip() + m.group(1)
            raw[i] = raw[i][len(m.group(1)):].strip()
    # Phase 2: strip leading punctuation noise, but keep left quotes
    # (opening quotes mark dialogue — stripping them sounds weird).
    _lead_noise = r'^[\s　。，、；：！？…\.\,\;\:\!\?\-'
    _lead_noise += r'」』"''”'  # right/closing quotes — safe to strip
    _lead_noise += r']+'
    final = []
    for seg in raw:
        seg = re.sub(_lead_noise, '', seg)
        if not re.search(r'[\w一-鿿]', seg):
            continue
        if len(seg) <= MAX_LEN:
            final.append(seg)
        else:
            # Split at comma-like punctuation, keeping the delimiter
            parts = re.split(r'(?<=[，,;；、])', seg)
            buf = ""
            for p in parts:
                if len(buf) + len(p) <= MAX_LEN or len(buf) == 0:
                    buf += p
                else:
                    if buf.strip(): final.append(buf.strip())
                    buf = p
            if buf.strip(): final.append(buf.strip())
    # Strip trailing commas — a comma at the end of a TTS segment
    # produces unnatural rising intonation with no continuation.
    final = [re.sub(r'[，,]+$', '', s) for s in final]
    return final

# ═══════════ TTS 引擎 (CrispASR DLL 封装) ═══════════

class TtsEngine:
    CTX = None
    _last_voice = None      # cache key for voice prompt
    _is_cv = False          # True when loaded model is CustomVoice
    _cv_speakers = []       # speaker names for CustomVoice
    _cv_speaker = ""        # current CV speaker

    @classmethod
    def init(cls, talker_gguf, codec_gguf):
        cls.shutdown()
        params = _dll.qwen3_tts_context_default_params()
        params.verbosity = 0          # silent
        params.use_gpu = True
        params.seed = 42
        params.n_threads = 16
        cls.CTX = _dll.qwen3_tts_init_from_file(_dll_path(talker_gguf), params)
        if not cls.CTX:
            raise RuntimeError(f"Failed to load talker: {talker_gguf}")
        if _dll.qwen3_tts_set_codec_path(cls.CTX, _dll_path(codec_gguf)) != 0:
            raise RuntimeError(f"Failed to load codec: {codec_gguf}")
        cls._last_voice = None
        cls._cv_speaker = ""
        cls._is_cv = bool(_dll.qwen3_tts_is_custom_voice(cls.CTX))
        cls._cv_speakers = []
        if cls._is_cv:
            n = _dll.qwen3_tts_n_speakers(cls.CTX)
            cls._cv_speakers = [
                _dll.qwen3_tts_get_speaker_name(cls.CTX, i).decode()
                for i in range(n)
            ]

    @classmethod
    def shutdown(cls):
        if cls.CTX:
            _dll.qwen3_tts_free(cls.CTX)
            cls.CTX = None
            cls._last_voice = None

    _LOUDNORM_REF = -18.0  # target LUFS (matching the known-good Elden Lord ref)

    @classmethod
    def _normalize_ref(cls, src_path):
        """Loudness-normalise a WAV to -18 LUFS via numpy. Returns path to a
        temp file (caller should clean up), or the original path on failure."""
        try:
            with wave.open(src_path, 'rb') as wf:
                sr = wf.getframerate(); ch = wf.getnchannels()
                sw = wf.getsampwidth(); n = wf.getnframes()
                raw = wf.readframes(n)
            fmt = {1: 'b', 2: 'h', 4: 'i'}.get(sw, 'h')
            pcm = np.frombuffer(raw, dtype=np.dtype(f'<{fmt}')).astype(np.float32) / 32767.0
            if ch > 1:
                pcm = pcm.reshape(-1, ch).mean(axis=1)
            rms = np.sqrt(np.mean(pcm * pcm))
            if rms < 1e-8:
                return src_path  # silence, skip
            target_rms = 10.0 ** (cls._LOUDNORM_REF / 20.0)
            pcm = pcm * (target_rms / rms)
            pcm = np.clip(pcm, -1.0, 1.0)
            tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
            tmp.close()
            with wave.open(tmp.name, 'wb') as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
                wf.writeframes((pcm * 32767.0).astype(np.int16).tobytes())
            return tmp.name
        except Exception:
            return src_path

    @classmethod
    def _ensure_voice(cls, ref_audio, ref_text, xvec_only=False, cv_speaker=""):
        """Update voice reference only when it changes (cached)."""
        if cls._is_cv and cv_speaker:
            key = ("cv", cv_speaker)
            if key == cls._last_voice:
                return True
            ok = (_dll.qwen3_tts_set_speaker_by_name(
                    cls.CTX, cv_speaker.encode()) == 0)
            cls._last_voice = key
            return ok
        key = (ref_audio, ref_text, xvec_only)
        if key == cls._last_voice:
            return True
        if not ref_audio:
            return False
        norm = cls._normalize_ref(ref_audio)
        ok = False
        if xvec_only:
            ok = (_dll.qwen3_tts_set_voice_prompt_xvec_only(
                    cls.CTX, _dll_path(norm)) == 0)
        else:
            if not ref_text:
                return False
            ok = (_dll.qwen3_tts_set_voice_prompt_with_text(
                    cls.CTX, _dll_path(norm), ref_text.encode("utf-8")) == 0)
        cls._last_voice = key
        return ok

    @classmethod
    def synth(cls, text, ref_audio, ref_text, text_lang="auto", speed=1.0,
              xvec_only=False, cv_speaker=""):
        if not cls.CTX:
            raise RuntimeError("TTS engine not initialised")
        if not cls._ensure_voice(ref_audio, ref_text, xvec_only, cv_speaker):
            raise RuntimeError("Failed to set voice reference")

        # Set language
        lang_id = _LANG_TO_ID.get(text_lang, -1)
        _dll.qwen3_tts_set_language(cls.CTX, lang_id)

        n_samples = ctypes.c_int(0)
        pcm = _dll.qwen3_tts_synthesize(cls.CTX, text.encode("utf-8"), ctypes.byref(n_samples))
        if not pcm or n_samples.value <= 0:
            raise RuntimeError("Synthesis returned no audio")

        arr = np.ctypeslib.as_array(pcm, shape=(n_samples.value,)).copy()
        _dll.qwen3_tts_pcm_free(pcm)

        # Crude speed change: linear resample
        if speed != 1.0 and speed > 0.1:
            old_len = len(arr)
            new_len = int(old_len / speed)
            arr = np.interp(np.linspace(0, old_len - 1, new_len), np.arange(old_len), arr).astype(np.float32)
        return arr


# ═══════════ 设置弹窗 ═══════════

class SettingsDialog(QDialog):
    def __init__(self, parent, cfg):
        super().__init__(parent); self._c = cfg
        self.setWindowTitle("设置"); self.setFixedSize(440, 420)
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

        row = QHBoxLayout(); row.addWidget(QLabel("读出语言"))
        self._tl = QComboBox(); self._tl.addItems(["auto", "zh", "en", "ja", "ko"])
        self._tl.setCurrentText(cfg.get("text_lang", "auto"))
        row.addWidget(self._tl); lay.addLayout(row)

        if TtsEngine._is_cv:
            row = QHBoxLayout(); row.addWidget(QLabel("音色"))
            self._cv_spk = QComboBox()
            speakers = TtsEngine._cv_speakers
            self._cv_spk.addItems(speakers)
            cur_spk = cfg.get("cv_speaker", speakers[0] if speakers else "")
            if cur_spk in speakers:
                self._cv_spk.setCurrentText(cur_spk)
            row.addWidget(self._cv_spk); lay.addLayout(row)
        else:
            row = QHBoxLayout(); row.addWidget(QLabel("参考音频"))
            self._ra = QLineEdit(cfg.get("ref_audio", "")); row.addWidget(self._ra)
            b = QPushButton("浏览"); b.clicked.connect(lambda: self._browse(self._ra, "音频 (*.wav *.mp3)"))
            row.addWidget(b); lay.addLayout(row)

            row = QHBoxLayout(); row.addWidget(QLabel("参考文本"))
            self._rt = QLineEdit(cfg.get("ref_text", "")); row.addWidget(self._rt); lay.addLayout(row)

            row = QHBoxLayout(); row.addWidget(QLabel(""))
            self._xo = QCheckBox("仅用音色 (xvec_only, 跨语言推荐)")
            self._xo.setChecked(cfg.get("xvec_only", False)); row.addWidget(self._xo); lay.addLayout(row)

        save = QPushButton("保存设置"); save.clicked.connect(self._sv); lay.addWidget(save)

    def _browse(self, w, filt):
        p = QFileDialog.getOpenFileName(self, "选择", w.text(), filt)
        if p[0]: w.setText(p[0])

    def _sv(self):
        d = dict(speed=self._sp.value() / 100, volume=self._vo.value() / 100,
                 text_lang=self._tl.currentText())
        if TtsEngine._is_cv:
            d["cv_speaker"] = self._cv_spk.currentText()
        else:
            d["ref_audio"] = self._ra.text()
            d["ref_text"] = self._rt.text()
            d["xvec_only"] = self._xo.isChecked()
        self._c.update(d)
        save_cfg(self._c); self.accept()


class ModelDialog(QDialog):
    def __init__(self, parent, cfg):
        super().__init__(parent); self._c = cfg
        self.setWindowTitle("模型路径"); self.setFixedSize(600, 160)
        self.setStyleSheet(QSS)
        lay = QVBoxLayout(self); lay.setSpacing(8)
        d = cfg.get("last_gguf", {})

        r = QHBoxLayout(); r.addWidget(QLabel("Talker (.gguf)"))
        self._g = QLineEdit(d.get("talker", "")); r.addWidget(self._g)
        b = QPushButton("浏览"); b.clicked.connect(lambda: self._br(self._g, "GGUF (*.gguf)"))
        r.addWidget(b); lay.addLayout(r)

        r = QHBoxLayout(); r.addWidget(QLabel("Codec (.gguf)"))
        self._s = QLineEdit(d.get("codec", "")); r.addWidget(self._s)
        b = QPushButton("浏览"); b.clicked.connect(lambda: self._br(self._s, "GGUF (*.gguf)"))
        r.addWidget(b); lay.addLayout(r)

        save = QPushButton("保存路径 & 加载模型"); save.clicked.connect(self._sv)
        lay.addWidget(save, alignment=Qt.AlignmentFlag.AlignCenter)

    def _br(self, w, filt):
        p = QFileDialog.getOpenFileName(self, "选择", w.text(), filt)
        if p[0]: w.setText(p[0])

    def _sv(self):
        self._c["last_gguf"] = {"talker": self._g.text(), "codec": self._s.text()}
        save_cfg(self._c); self.accept()


# ═══════════ 主窗口 ═══════════

class ReaderWin(QMainWindow):
    def __init__(self):
        super().__init__(); self.setWindowTitle("小说朗读器 — Qwen3-TTS")
        self.resize(1280, 900); self.setStyleSheet(QSS)
        self._c = load_cfg(); self._chs = []; self._bp = ""; self._ci = 0; self._pi = 0
        self._playing = False; self._loaded = False
        self._nxt = 0; self._total = 0; self._save_sent = 0; self._gid = 0; self._ask_sent = False
        self._synth_done = threading.Event(); self._synth_done.set()  # TTS idle at start
        self._synth_pause = threading.Event(); self._synth_pause.set()  # synthesis allowed
        self._sync_scroll = False  # auto-scroll during playback

        self._build(); self._restore()

    def _build(self):
        cw = QWidget(); self.setCentralWidget(cw)
        lay = QVBoxLayout(cw); lay.setContentsMargins(10, 8, 10, 6); lay.setSpacing(6)

        # ── 工具栏 ──
        tb = QHBoxLayout()
        for t, f in [("📂 打开", self._ob),
                     ("🔧 模型", lambda: self._model_dlg()),
                     ("▶ 加载", self._lm),
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
        self._ml = QLabel("模型: 未加载"); self._ml.setStyleSheet("color:#888;"); tb.addWidget(self._ml)
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

    # ── 模型对话框 ──
    def _model_dlg(self):
        dlg = ModelDialog(self, self._c)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            # Auto-load if both paths are set
            m = self._c.get("last_gguf", {})
            if m.get("talker") and m.get("codec"):
                self._lm()

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

    # ── 展示 ──
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
        """Update _pi to track which paragraph is at the top of the view."""
        if not self._chs: return
        _, ps = self._chs[self._ci]
        if not ps: return
        sb = self._tx.verticalScrollBar()
        pct = sb.value() / max(sb.maximum(), 1)
        # Proportional estimate: scroll position → paragraph index.
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

    # ── 模型加载 ──
    def _lm(self):
        m = self._c.get("last_gguf", {})
        if not m.get("talker") or not m.get("codec"):
            QMessageBox.warning(self, "提示", "请先设置 talker.gguf 和 codec.gguf 路径")
            return
        self._ml.setText("加载中..."); self._ml.setStyleSheet("color:#fa0;"); QApplication.processEvents()
        try:
            TtsEngine.init(m["talker"], m["codec"]); self._loaded = True
            tag = " (CV)" if TtsEngine._is_cv else ""
            self._ml.setText(f"模型: {os.path.basename(m['talker'])}{tag}"); self._ml.setStyleSheet("color:#0a0;")
        except Exception as e:
            self._ml.setText(f"失败: {e}"); self._ml.setStyleSheet("color:#f00;")

    # ── 播放 ──
    def _tp(self):
        if self._playing: self._ps()
        else: self._pl()

    def _pl(self):
        if not self._loaded:
            QMessageBox.warning(self, "提示", "请先加载模型"); return
        if not TtsEngine._is_cv and not self._c.get("ref_audio"):
            QMessageBox.warning(self, "提示", "请先设置参考音频"); return
        if TtsEngine._is_cv and not self._c.get("cv_speaker"):
            QMessageBox.warning(self, "提示", "请先在设置中选择音色"); return
        if not self._chs: return
        # Always show paragraph picker — paragraphs map 1:1 to what's on screen.
        _, ps = self._chs[self._ci]

        # Default: the tracked visible paragraph (synced with scroll position).
        cur = max(0, self._pi - 5)  # bias up: user has typically read past the visible line
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
            return  # user closed the dialog → don't play
        self._playing = True; self._play_btn.setText("⏸ 暂停")
        self._resume_btn.hide()
        # If resuming from same paragraph, reuse existing synthesis queue.
        if (hasattr(self, '_paused_gid') and self._paused_gid != 0
                and self._save_sent == getattr(self, '_paused_para', -1)):
            self._synth_pause.set()  # unblock synthesis thread
            self._gid = self._paused_gid
            self._nxt = self._paused_nxt
            self._prog.setMaximum(self._paused_total)
            self._prog.setValue(self._paused_nxt)
            self._st.setText(f"续播中 ({self._nxt}/{self._paused_total})")
            self._paused_gid = 0
            self._pn()
        else:
            # Different paragraph or first play — start fresh.
            if hasattr(self, '_paused_gid') and self._paused_gid != 0:
                self._synth_pause.set()  # unblock paused thread
                self._gid = 0; self._paused_gid = 0
                if not self._synth_done.wait(10):
                    self._st.setText("等待上一个合成完成..."); QApplication.processEvents()
                    self._synth_done.wait()
            self._sap()

    def _resume(self):
        """Resume from paused position without showing the paragraph picker."""
        if not hasattr(self, '_paused_gid') or self._paused_gid == 0:
            return  # nothing to resume
        self._synth_pause.set()  # unblock synthesis thread
        self._gid = self._paused_gid
        self._nxt = self._paused_nxt
        self._save_sent = self._paused_para  # keep consistent
        self._prog.setMaximum(self._paused_total)
        self._prog.setValue(self._paused_nxt)
        self._st.setText(f"续播中 ({self._nxt}/{self._paused_total})")
        self._paused_gid = 0
        self._resume_btn.hide(); self._play_btn.setText("⏸ 暂停")
        self._playing = True
        self._pn()

    def _ps(self):
        self._playing = False; self._play_btn.setText("▶ 播放")
        self._synth_pause.clear()  # block synthesis thread
        self._resume_btn.show()
        sd.stop(); self._sv()
        # Save synthesis state for possible resume.
        self._paused_gid = self._gid
        self._paused_nxt = max(0, self._nxt - 1)  # replay the interrupted sentence
        self._paused_para = self._para_start
        self._paused_total = self._total
        # Estimate which paragraph the current sentence falls into.
        self._save_sent = self._para_start + self._sent_in_para(self._nxt)
        self._info.setText("已暂停"); self._st.setText("")

    def _sent_in_para(self, nxt):
        """Map sentence index (in current sens batch) back to paragraph offset."""
        if not hasattr(self, '_sens_para_map') or nxt <= 0:
            return 0
        for sp in self._sens_para_map:
            if sp[0] >= nxt:
                return max(0, sp[1] - 1)
        return self._sens_para_map[-1][1] if self._sens_para_map else 0

    def _sap(self):
        if not self._playing: return
        self._synth_pause.set()  # synthesis starts unpaused
        ci = self._ci
        if ci >= len(self._chs): self._dn(); return
        _, ps = self._chs[ci]
        para_start = self._save_sent; self._para_start = para_start
        self._save_sent = 0; self._pi = para_start
        self._ri()
        text = "".join(ps[para_start:]); cfg = self._c
        sens = split_text(text)
        # Build sentence→paragraph map for pause estimation
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
                    self._synth_pause.wait()  # block while paused
                    if self._gid != gid: return
                    try:
                        pcm = TtsEngine.synth(txt,
                                              cfg.get("ref_audio", ""),
                                              cfg.get("ref_text", ""),
                                              cfg.get("text_lang", "auto"),
                                              cfg.get("speed", 1.0),
                                              cfg.get("xvec_only", False),
                                              cfg.get("cv_speaker", ""))
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
        """Get next sentence from wavs queue and play it. Uses get() so
        interrupted sentences can be replayed on resume."""
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
        sd.play(pcm.astype(np.float32), SAMPLE_RATE)
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

    # ── config ──
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
        TtsEngine.shutdown()
        e.accept()


if __name__ == "__main__":
    if sys.platform == "win32":
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    app = QApplication(sys.argv); app.setStyle("Fusion")
    win = ReaderWin(); win.show()
    sys.exit(app.exec())
