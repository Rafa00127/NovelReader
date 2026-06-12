这里的 causal conv1d-transpose 实现基于 qwentts.cpp 的 [causal-trans-conv.h](https://github.com/ServeurpersoCom/qwentts.cpp/blob/master/src/causal-trans-conv.h)。

Fish S2 Pro 模型支持 (fish2_server) 移植自 rodrigomatta 的 [s2.cpp](https://github.com/rodrigomatta/s2.cpp)，使用 Fish Audio Research License 授权。Copyright (c) 39 AI, INC.

## 模型下载

**Qwen3-TTS (qwen3tts_server):**
- Talker: [cstr/qwen3-tts-1.7b-base-GGUF](https://huggingface.co/cstr/qwen3-tts-1.7b-base-GGUF)
- Codec: [cstr/qwen3-tts-tokenizer-12hz-GGUF](https://huggingface.co/cstr/qwen3-tts-tokenizer-12hz-GGUF)

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
- `ggml.dll`、`ggml-hip.dll` 等
