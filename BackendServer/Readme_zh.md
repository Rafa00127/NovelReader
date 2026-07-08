Qwen3-TTS 支持 (qwen3tts_server) 基于 [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) 模型，使用 Apache 2.0 协议授权。

Fish S2 Pro 模型支持 (fish2_server) 移植自 rodrigomatta 的 [s2.cpp](https://github.com/rodrigomatta/s2.cpp)，使用 Fish Audio Research License 授权。Copyright (c) 39 AI, INC.

Higgs TTS 支持 (higgs_server) 是 [bosonai/higgs-tts-3-4b](https://huggingface.co/bosonai/higgs-tts-3-4b) 的 ggml 移植，详见 [LICENSE-HIGGS](../LICENSE-HIGGS)。

## 模型下载

**Qwen3-TTS (qwen3tts_server):**
- Talker: [cstr/qwen3-tts-1.7b-base-GGUF](https://hf-mirror.com/cstr/qwen3-tts-1.7b-base-GGUF)

- Codec: [cstr/qwen3-tts-tokenizer-12hz-GGUF](https://hf-mirror.com/cstr/qwen3-tts-tokenizer-12hz-GGUF)

**Higgs TTS (higgs_server):**
- Model: [NeemaShioSe/HiggsTTS3.gguf](https://huggingface.co/NeemaShioSe/HiggsTTS3.gguf)

**Fish S2 Pro (fish2_server):**
- Model: [rodrigomt/s2-pro-gguf](https://huggingface.co/rodrigomt/s2-pro-gguf)

模型转化方式可在对应 Hugging Face 仓库中查找。

## 编译（以 AMD GPU + Windows 为例）
>
> 所有 GPU 后端编译流程相同，只需更换 cmake 选项：`GGML_CUDA=ON`（NVIDIA）、`GGML_VULKAN=ON`（通用 GPU）、`GGML_METAL=ON`（macOS）。

### 前置条件

1. **HIP SDK** — 从 [AMD ROCm Hub](https://www.amd.com/en/developer/resources/rocm-hub/hip-sdk.html) 下载安装。包含 HIP 编译器（`clang++`）、ROCm 库和运行时。
2. **Visual Studio Build Tools** — 从 [Visual Studio 下载](https://visualstudio.microsoft.com/downloads/) 安装。选择"使用 C++ 的桌面开发"工作负载。提供 MSVC 链接器和 Windows SDK。
3. **CMake** (>= 3.21) + **Ninja** — 使用 Visual Studio 自带的 CMake，或通过 `winget install CMake Ninja` 自行安装。

### 编译

> 以下命令假设 ROCm **6.2** 安装在 `D:/Rocm/6.2/`。请根据实际版本和路径调整。

打开 Visual Studio Developer Command Prompt（x64）（可通过左下角搜索 "Developer Command Prompt" 找到），执行：

```batch
cd /d D:\your\path\NovelReader\BackendServer
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release ^
  -DGGML_HIP=ON -DHIP_PLATFORM=amd ^
  -DCMAKE_C_COMPILER=D:/Rocm/6.2/bin/clang.exe ^
  -DCMAKE_CXX_COMPILER=D:/Rocm/6.2/bin/clang++.exe ^
  -DCMAKE_CXX_SCAN_FOR_MODULES=OFF

cmake --build build --config Release
```

> **注意：** 请根据你的 ROCm 安装路径调整编译器路径。常见安装位置：
> - Windows: `D:/Rocm/<版本>/bin/clang++.exe`
> - Linux: `/opt/rocm/bin/clang++`（通常自动检测）

编译产物在 `build/bin/` 目录：
- `qwen3tts_server.exe`
- `fish2_server.exe`
- `higgs_server.exe`
- `higgs_cli.exe`
- `higgs_quantize.exe`
- `ggml.dll`、`ggml-hip.dll` 等

## TCP API Reference

两个 server 共用同一套 TCP 协议。

### 连接

| 项目 | 值 |
|------|----|
| 地址 | `127.0.0.1`（仅本地回环） |
| 默认端口 | `9988`，可通过 `--port` 覆盖 |
| 并发 | 每连接 `detach` 一条线程，合成由全局 mutex 序列化 |

### 请求（Client → Server）

```
┌─────────────────────┬──────────────────────────┐
│  4 bytes (big-endian)  │  N bytes (UTF-8)          │
│  int32: text_len       │  text payload              │
└─────────────────────┴──────────────────────────┘
```

- `text_len`：文本字节长度（不含空终止符），最大 `10000`
- `text`：UTF-8 编码的文本

### 响应（Server → Client）

成功时：

```
┌─────────────────────┬──────────────────────────┐
│  4 bytes (big-endian)  │  M × 4 bytes                │
│  int32: n_samples      │  float32 little-endian PCM  │
└─────────────────────┴──────────────────────────┘
```

- `n_samples`：采样点数，`> 0`
- PCM：单声道 float32，取值范围 `[-1, 1]`
- Qwen3-TTS 采样率为 `24000 Hz`，Fish S2 Pro 为 `44100 Hz`

出错时（模型未加载 / 合成失败 / 文本过长等）：

```
┌─────────────────────┐
│  4 bytes (big-endian)  │
│  int32: -1             │
└─────────────────────┘
```

### 示例（Python）

```python
import socket, struct

def synth(host="127.0.0.1", port=9988, text=""):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(120)
    sock.connect((host, port))

    payload = text.encode("utf-8")
    sock.sendall(struct.pack(">i", len(payload)))
    sock.sendall(payload)

    hdr = recvn(sock, 4)
    n = struct.unpack(">i", hdr)[0]
    if n <= 0:
        raise RuntimeError(f"server error: n_samples={n}")

    data = recvn(sock, n * 4)
    sock.close()
    return data  # float32 PCM bytes


def recvn(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return buf
```

### 计划

以后可能加上真流式功能，至少qwen3tts是已经有现成的实现方法可以移植过来
