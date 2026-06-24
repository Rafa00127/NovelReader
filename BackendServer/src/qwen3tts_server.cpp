// qwen3tts_server.cpp — Simple TCP TTS server for CrispASR.
// Loads talker+codec once, GPU-resident, serializes synthesis via mutex.
// Protocol: client sends 4-byte big-endian text_len + UTF-8 text, server replies
//           4-byte big-endian n_samples + float32 PCM.
// Compile: add_executable(qwen3tts_server qwen3tts_server.cpp)
//          target_link_libraries(qwen3tts_server qwen3_tts crispasr-core ggml ggml-base ggml-cpu)
//          + ggml-hip if HIP build.

#include "qwen3_tts.h"

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <shellapi.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#define SOCKET int
#define INVALID_SOCKET (-1)
#define SOCKET_ERROR (-1)
#define closesocket close
#endif

#include <atomic>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

// ── config ──────────────────────────────────────────────────────────────────
static const char*  g_talker_path   = nullptr;
static const char*  g_codec_path    = nullptr;
static std::string  g_ref_audio;
static std::string  g_ref_text;
static std::string  g_cv_speaker;
static std::string  g_text_lang     = "auto";
static float        g_speed         = 1.0f;
static int          g_port          = 9988;
static bool         g_xvec_only     = false;

// ── globals ─────────────────────────────────────────────────────────────────
static qwen3_tts_context*        g_ctx          = nullptr;
static std::mutex                g_mutex;
static std::atomic<bool>         g_running{true};

// ── helpers ─────────────────────────────────────────────────────────────────
static void die(const char* msg) {
    fprintf(stderr, "[qwen3tts_server] FATAL: %s\n", msg);
    exit(1);
}

static bool send_all(SOCKET fd, const char* data, int len) {
    int sent = 0;
    while (sent < len) {
        int n = send(fd, data + sent, len - sent, 0);
        if (n == SOCKET_ERROR) return false;
        sent += n;
    }
    return true;
}

static bool load_model() {
    qwen3_tts_context_params cp = qwen3_tts_context_default_params();
    cp.verbosity = 0;
    cp.use_gpu   = true;
    cp.seed      = 42;
    cp.n_threads = 16;

    g_ctx = qwen3_tts_init_from_file(g_talker_path, cp);
    if (!g_ctx) {
        fprintf(stderr, "[qwen3tts_server] failed to load talker: %s\n", g_talker_path);
        return false;
    }
    if (qwen3_tts_set_codec_path(g_ctx, g_codec_path) != 0) {
        fprintf(stderr, "[qwen3tts_server] failed to load codec: %s\n", g_codec_path);
        qwen3_tts_free(g_ctx);
        g_ctx = nullptr;
        return false;
    }
    // Set voice if configured
    if (!g_ref_audio.empty()) {
        if (g_xvec_only) {
            qwen3_tts_set_voice_prompt_xvec_only(g_ctx, g_ref_audio.c_str());
        } else if (!g_ref_text.empty()) {
            qwen3_tts_set_voice_prompt_with_text(g_ctx, g_ref_audio.c_str(), g_ref_text.c_str());
        }
    }
    if (!g_cv_speaker.empty()) {
        qwen3_tts_set_speaker_by_name(g_ctx, g_cv_speaker.c_str());
    }
    // Set language
    qwen3_tts_set_language(g_ctx, g_text_lang == "zh" || g_text_lang == "chinese" ? 2055 :
                                  g_text_lang == "en" || g_text_lang == "english" ? 2050 :
                                  g_text_lang == "ja" || g_text_lang == "japanese" ? 2058 :
                                  g_text_lang == "ko" || g_text_lang == "korean" ? 2064 : -1);

    fprintf(stderr, "[qwen3tts_server] model loaded: %s + %s\n", g_talker_path, g_codec_path);
    return true;
}

static bool synth_one(const char* text, std::vector<float>& pcm) {
    int n_samples = 0;
    float* raw = nullptr;
    fprintf(stderr, "[qwen3tts_server] synth: '%s'\n", text); fflush(stderr);
    {
        std::lock_guard<std::mutex> lock(g_mutex);
        fprintf(stderr, "[qwen3tts_server] calling qwen3_tts_synthesize...\n"); fflush(stderr);
        raw = qwen3_tts_synthesize(g_ctx, text, &n_samples);
        fprintf(stderr, "[qwen3tts_server] synthesize returned raw=%p n_samples=%d\n", (void*)raw, n_samples); fflush(stderr);
        // qwen3_tts_sync(g_ctx);  // drain GPU command queue before next synthesis
    }
    if (!raw || n_samples <= 0) return false;
    pcm.assign(raw, raw + n_samples);
    qwen3_tts_pcm_free(raw);
    return true;
}

static void handle_client(SOCKET client_fd) {
    // Read 4-byte length
    int32_t text_len_be = 0;
    int nr = recv(client_fd, (char*)&text_len_be, 4, MSG_WAITALL);
    if (nr != 4) { closesocket(client_fd); return; }
    int32_t text_len = ntohl(text_len_be);  // network byte order
    if (text_len <= 0 || text_len > 10000) { closesocket(client_fd); return; }

    std::string text(text_len, '\0');
    nr = recv(client_fd, &text[0], text_len, MSG_WAITALL);
    if (nr != text_len) { closesocket(client_fd); return; }

    fprintf(stderr, "[qwen3tts_server] received text: '%s' (%d bytes)\n", text.c_str(), text_len); fflush(stderr);

    // Synthesize
    std::vector<float> pcm;
    if (!synth_one(text.c_str(), pcm)) {
        fprintf(stderr, "[qwen3tts_server] synth_one failed\n"); fflush(stderr);
        int32_t err = htonl(-1);
        send_all(client_fd, (const char*)&err, 4);
        closesocket(client_fd);
        return;
    }

    // Reply: 4-byte n_samples (network byte order) + float32 PCM
    int32_t ns_be = htonl((int32_t)pcm.size());
    send_all(client_fd, (const char*)&ns_be, 4);
    send_all(client_fd, (const char*)pcm.data(), (int)(pcm.size() * sizeof(float)));
    shutdown(client_fd, SD_SEND);  // graceful close — client sees EOF, not RST
    closesocket(client_fd);
}

static void server_loop() {
#ifdef _WIN32
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
#endif

    SOCKET listen_fd = socket(AF_INET, SOCK_STREAM, 0);
    if (listen_fd == INVALID_SOCKET) die("socket() failed");

    int opt = 1;
    setsockopt(listen_fd, SOL_SOCKET, SO_REUSEADDR, (const char*)&opt, sizeof(opt));

    struct sockaddr_in addr = {};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port        = htons((uint16_t)g_port);

    if (bind(listen_fd, (struct sockaddr*)&addr, sizeof(addr)) == SOCKET_ERROR)
        die("bind() failed");
    if (listen(listen_fd, 8) == SOCKET_ERROR)
        die("listen() failed");

    fprintf(stderr, "[qwen3tts_server] listening on 127.0.0.1:%d\n", g_port);

    while (g_running) {
        SOCKET client = accept(listen_fd, nullptr, nullptr);
        if (client == INVALID_SOCKET) continue;
        std::thread(handle_client, client).detach();
    }

    closesocket(listen_fd);
#ifdef _WIN32
    WSACleanup();
#endif
}

int main(int argc, char** argv) {
#ifdef _WIN32
    // Windows main() receives argv in the system ANSI code page (e.g. GBK),
    // which corrupts non-ASCII paths. Use GetCommandLineW + CommandLineToArgvW
    // to get the raw UTF-16 command line and convert to UTF-8 argv.
    {
        int argc_w = 0;
        LPWSTR* argv_w = CommandLineToArgvW(GetCommandLineW(), &argc_w);
        if (argv_w) {
            static std::vector<std::string> utf8_args;
            static std::vector<char*> new_argv;
            for (int i = 0; i < argc_w; i++) {
                int len = WideCharToMultiByte(CP_UTF8, 0, argv_w[i], -1, nullptr, 0, nullptr, nullptr);
                std::string s(len - 1, '\0');
                WideCharToMultiByte(CP_UTF8, 0, argv_w[i], -1, &s[0], len, nullptr, nullptr);
                utf8_args.push_back(std::move(s));
            }
            LocalFree(argv_w);
            for (auto& s : utf8_args) new_argv.push_back(s.data());
            argv = new_argv.data();
            argc = argc_w;
        }
    }
#endif
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--model") && i + 1 < argc)
            g_talker_path = argv[++i];
        else if (!strcmp(argv[i], "--codec") && i + 1 < argc)
            g_codec_path = argv[++i];
        else if (!strcmp(argv[i], "--port") && i + 1 < argc)
            g_port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--ref-audio") && i + 1 < argc)
            g_ref_audio = argv[++i];
        else if (!strcmp(argv[i], "--ref-text") && i + 1 < argc)
            g_ref_text = argv[++i];
        else if (!strcmp(argv[i], "--cv-speaker") && i + 1 < argc)
            g_cv_speaker = argv[++i];
        else if (!strcmp(argv[i], "--lang") && i + 1 < argc)
            g_text_lang = argv[++i];
        else if (!strcmp(argv[i], "--xvec-only"))
            g_xvec_only = true;
        else if (!strcmp(argv[i], "--speed") && i + 1 < argc)
            g_speed = (float)atof(argv[++i]);
    }

    if (!g_talker_path || !g_codec_path) {
        fprintf(stderr, "usage: qwen3tts_server --model <talker.gguf> --codec <codec.gguf> [--port 9988] \\\n"
                        "                   [--ref-audio <wav> --ref-text <text>] [--cv-speaker <name>] \\\n"
                        "                   [--lang auto|zh|en|ja|ko] [--xvec-only]\n");
        return 1;
    }

    if (!load_model())
        return 1;

    server_loop();

    qwen3_tts_free(g_ctx);
    return 0;
}
