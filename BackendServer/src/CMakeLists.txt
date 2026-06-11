# ===========================================================================
# Server-only build — no DLL, no standalone_codec, no tests.
# Produces: qwen3tts_server.exe, fish2_server.exe
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

# --- s2_core (Fish S2 Pro model library) ------------------------------------
add_library(s2_core STATIC
    s2/s2_model.cpp    s2/s2_model.h
    s2/s2_codec.cpp    s2/s2_codec.h
    s2/s2_pipeline.cpp s2/s2_pipeline.h
    s2/s2_tokenizer.cpp s2/s2_tokenizer.h
    s2/s2_generate.cpp s2/s2_generate.h
    s2/s2_sampler.cpp  s2/s2_sampler.h
    s2/s2_prompt.cpp   s2/s2_prompt.h
    s2/s2_audio.cpp    s2/s2_audio.h
    s2/s2_voice.cpp    s2/s2_voice.h
    s2/s2_log.cpp      s2/s2_log.h
    s2/s2_backend.h    s2/s2_config.h
    s2/s2_ggml_utils.h
    s2/third_party/json.hpp
    s2/third_party/dr_wav.h
    s2/third_party/dr_mp3.h
    s2/third_party/filesystem.hpp
)
target_include_directories(s2_core PUBLIC
    s2
    s2/third_party
    ../include
    ../ggml/src
)
target_compile_features(s2_core PUBLIC cxx_std_17)
target_link_libraries(s2_core PUBLIC ggml ggml-base ggml-cpu)
if(TARGET ggml-hip)
    target_link_libraries(s2_core PUBLIC ggml-hip)
endif()
if(TARGET ggml-vulkan)
    target_link_libraries(s2_core PUBLIC ggml-vulkan)
endif()
if(TARGET ggml-cuda)
    target_link_libraries(s2_core PUBLIC ggml-cuda)
endif()
set_target_properties(s2_core PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(NOT MSVC)
    target_compile_options(s2_core PRIVATE -O3)
endif()

# --- fish2_server ------------------------------------------------------------
add_executable(fish2_server Fish2.cpp)
target_compile_definitions(fish2_server PRIVATE _CRT_SECURE_NO_WARNINGS)
target_include_directories(fish2_server PRIVATE . ../include ../ggml/src)
target_compile_features(fish2_server PRIVATE cxx_std_17)
target_link_libraries(fish2_server PRIVATE s2_core ggml ggml-base ggml-cpu)
if(TARGET ggml-hip)
    target_link_libraries(fish2_server PRIVATE ggml-hip)
endif()
if(TARGET ggml-vulkan)
    target_link_libraries(fish2_server PRIVATE ggml-vulkan)
endif()
if(NOT MSVC)
    target_compile_options(fish2_server PRIVATE -O3)
endif()
