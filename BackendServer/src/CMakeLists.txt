# ===========================================================================
# Server-only build — no DLL, no standalone_codec, no tests.
# Produces: qwen3tts_server.exe (statically links qwen3_tts + crispasr-core)
# ===========================================================================

# --- crispasr-core ---------------------------------------------------------
add_library(crispasr-core STATIC
    core/mel.h       core/mel.cpp
    core/ffn.h
    core/attention.h
    core/greedy_decode.h
    core/gguf_loader.h core/gguf_loader.cpp
    core/rvq.h       core/rvq.cpp
    core/kaldi_fbank.h core/kaldi_fbank.cpp
    core/audio_resample.h core/audio_resample.cpp
)
target_include_directories(crispasr-core PUBLIC . ../include)
target_include_directories(crispasr-core PRIVATE ../ggml/src)
target_compile_features(crispasr-core PUBLIC cxx_std_17)
target_link_libraries(crispasr-core PUBLIC ggml-base ggml)
set_target_properties(crispasr-core PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(NOT MSVC)
    target_compile_options(crispasr-core PRIVATE -O3)
endif()

# --- qwen3_tts (static) ----------------------------------------------------
add_library(qwen3_tts STATIC qwen3_tts.h qwen3_tts.cpp)
set_target_properties(qwen3_tts PROPERTIES POSITION_INDEPENDENT_CODE ON)
target_include_directories(qwen3_tts PUBLIC .)
target_compile_features(qwen3_tts PUBLIC cxx_std_17)
target_link_libraries(qwen3_tts PUBLIC crispasr-core ggml ggml-base ggml-cpu)
if(TARGET ggml-hip)
    target_link_libraries(qwen3_tts PUBLIC ggml-hip)
endif()
if(NOT MSVC)
    target_compile_options(qwen3_tts PRIVATE -O3)
endif()

# --- qwen3tts_server ----------------------------------------------------------
add_executable(qwen3tts_server qwen3tts_server.cpp)
target_compile_definitions(qwen3tts_server PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(qwen3tts_server PRIVATE . ../include ../ggml/src)
target_compile_features(qwen3tts_server PRIVATE cxx_std_17)
target_link_libraries(qwen3tts_server PRIVATE qwen3_tts crispasr-core ggml ggml-base ggml-cpu)
if(TARGET ggml-hip)
    target_link_libraries(qwen3tts_server PRIVATE ggml-hip)
endif()
if(NOT MSVC)
    target_compile_options(qwen3tts_server PRIVATE -O3)
endif()
