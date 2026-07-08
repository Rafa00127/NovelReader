Qwen3-TTS support (qwen3tts_server) uses the [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) model, licensed under Apache 2.0.

Fish S2 Pro model support (fish2_server) is ported from [s2.cpp](https://github.com/rodrigomatta/s2.cpp) by rodrigomatta, licensed under the Fish Audio Research License. Copyright (c) 39 AI, INC.

Higgs TTS support (higgs_server) is a ggml port of [bosonai/higgs-tts-3-4b](https://huggingface.co/bosonai/higgs-tts-3-4b). See [LICENSE-HIGGS](../LICENSE-HIGGS).

## Model Downloads

**Qwen3-TTS (qwen3tts_server):**
- Talker: [cstr/qwen3-tts-1.7b-base-GGUF](https://huggingface.co/cstr/qwen3-tts-1.7b-base-GGUF)

- Codec: [cstr/qwen3-tts-tokenizer-12hz-GGUF](https://huggingface.co/cstr/qwen3-tts-tokenizer-12hz-GGUF)

**Fish S2 Pro (fish2_server):**
- Model: [rodrigomt/s2-pro-gguf](https://huggingface.co/rodrigomt/s2-pro-gguf)

**Higgs TTS (higgs_server):**
- Model: [NeemaShioSe/HiggsTTS3.gguf](https://huggingface.co/NeemaShioSe/HiggsTTS3.gguf)

Model conversion scripts and instructions can be found in the respective Hugging Face repos.

## Building (using AMD GPU + Windows as an example)
>
> The process is the same for all GPU backends — just swap the cmake option: `GGML_CUDA=ON` (NVIDIA), `GGML_VULKAN=ON` (any GPU), `GGML_METAL=ON` (macOS).

### Prerequisites

1. **HIP SDK** — download and install from [AMD ROCm Hub](https://www.amd.com/en/developer/resources/rocm-hub/hip-sdk.html). This provides the HIP compiler (`clang++`), ROCm libraries, and runtime.
2. **Visual Studio Build Tools** — install from [Visual Studio Downloads](https://visualstudio.microsoft.com/downloads/). Select "Desktop development with C++" workload. This provides the MSVC linker and Windows SDK.
3. **CMake** (>= 3.21) + **Ninja** — use the CMake bundled with Visual Studio, or install via `winget install CMake Ninja`.

### Build

> The commands below assume ROCm **6.2** installed at `D:/Rocm/6.2/`. Adjust the version and path to match your installation.

Open a Visual Studio Developer Command Prompt (x64) (search "Developer Command Prompt" in the Windows start menu), then run cmake with the HIP toolchain:

```batch
cd /d D:\your\path\NovelReader\BackendServer
cmake -B build -G Ninja -DCMAKE_BUILD_TYPE=Release ^
  -DGGML_HIP=ON -DHIP_PLATFORM=amd ^
  -DCMAKE_C_COMPILER=D:/Rocm/6.2/bin/clang.exe ^
  -DCMAKE_CXX_COMPILER=D:/Rocm/6.2/bin/clang++.exe ^
  -DCMAKE_CXX_SCAN_FOR_MODULES=OFF

cmake --build build --config Release
```

> **Note:** Adjust the compiler paths to match your ROCm installation. Common install locations:
> - Windows: `D:/Rocm/<version>/bin/clang++.exe`
> - Linux: `/opt/rocm/bin/clang++` (typically auto-detected)

Output binaries are in `build/bin/`:
- `qwen3tts_server.exe`
- `fish2_server.exe`
- `higgs_server.exe`
- `higgs_cli.exe`
- `higgs_quantize.exe`
- `ggml.dll`, `ggml-hip.dll`, etc.

