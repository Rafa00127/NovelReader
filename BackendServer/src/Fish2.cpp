// Fish2.cpp — TCP TTS server for Fish Audio S2 Pro (via s2::Pipeline).
// Protocol: client sends 4-byte big-endian text_len + UTF-8 text, server replies
//           4-byte big-endian n_samples + float32 PCM.
// Model: qwen3tts_server.cpp — same TCP pattern, different model backend.

#include "s2/s2_pipeline.h"
#include "s2/s2_backend.h"
#include "s2/s2_audio.h"

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
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
static const char*  g_model_path     = nullptr;
static const char*  g_tokenizer_path = nullptr;
static std::string  g_ref_audio_path;
static std::string  g_ref_text;
static int          g_port           = 9988;
static int          g_gpu_layers     = -1;
static int          g_n_threads      = 8;
static bool         g_cpu_only       = false;

// ── globals ─────────────────────────────────────────────────────────────────
static std::unique_ptr<s2::Pipeline> g_pipeline;
static std::mutex                    g_mutex;
static std::atomic<bool>             g_running{true};
static int                           g_sample_rate = 0;

// ── helpers ─────────────────────────────────────────────────────────────────
static void die(const char* msg) {
    fprintf(stderr, "[fish2_server] FATAL: %s\n", msg);
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
    s2::PipelineParams params;
    params.model_path     = g_model_path;
    params.tokenizer_path = g_tokenizer_path;
    params.gen.n_threads  = g_n_threads;
    params.n_gpu_layers   = g_gpu_layers;

    if (g_cpu_only) {
        params.backend_type = s2::BackendType::CPU;
        params.codec_auto_backend = false;
        params.codec_follow_backend = false;
    } else {
        params.gpu_device   = 0;   // required: device index >= 0 enables GPU path
#ifdef GGML_USE_VULKAN
        params.backend_type = s2::BackendType::Vulkan;
#elif defined(GGML_USE_CUDA)
        params.backend_type = s2::BackendType::CUDA;
#else
        params.backend_type = s2::BackendType::CPU;
        fprintf(stderr, "[fish2_server] No GPU backend compiled in; using CPU.\n");
#endif
    }

    g_pipeline = std::make_unique<s2::Pipeline>();
    if (!g_pipeline->init(params)) {
        fprintf(stderr, "[fish2_server] Pipeline init failed.\n");
        return false;
    }

    g_sample_rate = g_pipeline->output_sample_rate();
    fprintf(stderr, "[fish2_server] model loaded: %s\n", g_model_path);
    fprintf(stderr, "[fish2_server] sample_rate=%d\n", g_sample_rate);
    return true;
}

static bool synth_one(const char* text, std::vector<float>& pcm) {
    fprintf(stderr, "[fish2_server] synth: '%s'\n", text); fflush(stderr);

    s2::PipelineParams params;
    params.text            = text;
    params.gen.n_threads   = g_n_threads;
    params.gen.max_new_tokens = 1024;
    params.gen.temperature = 0.8f;
    params.gen.top_p       = 0.8f;
    params.gen.top_k       = 30;
    if (!g_ref_text.empty()) {
        params.prompt_text = g_ref_text;
    }

    s2::AudioData ref_audio;
    if (!g_ref_audio_path.empty()) {
        s2::load_audio(g_ref_audio_path, ref_audio, g_sample_rate);
    }

    {
        std::lock_guard<std::mutex> lock(g_mutex);
        if (!g_pipeline->synthesize_raw(params, ref_audio, pcm)) {
            fprintf(stderr, "[fish2_server] synthesize_raw failed\n"); fflush(stderr);
            return false;
        }
    }
    return !pcm.empty();
}

static void handle_client(SOCKET client_fd) {
    int32_t text_len_be = 0;
    int nr = recv(client_fd, (char*)&text_len_be, 4, MSG_WAITALL);
    if (nr != 4) { closesocket(client_fd); return; }
    int32_t text_len = ntohl(text_len_be);
    if (text_len <= 0 || text_len > 10000) { closesocket(client_fd); return; }

    std::string text(text_len, '\0');
    nr = recv(client_fd, &text[0], text_len, MSG_WAITALL);
    if (nr != text_len) { closesocket(client_fd); return; }

    fprintf(stderr, "[fish2_server] received text: '%s' (%d bytes)\n", text.c_str(), text_len); fflush(stderr);

    std::vector<float> pcm;
    if (!synth_one(text.c_str(), pcm)) {
        fprintf(stderr, "[fish2_server] synth_one failed\n"); fflush(stderr);
        int32_t err = htonl(-1);
        send_all(client_fd, (const char*)&err, 4);
        closesocket(client_fd);
        return;
    }

    int32_t ns_be = htonl((int32_t)pcm.size());
    send_all(client_fd, (const char*)&ns_be, 4);
    send_all(client_fd, (const char*)pcm.data(), (int)(pcm.size() * sizeof(float)));
    shutdown(client_fd, SD_SEND);
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

    fprintf(stderr, "[fish2_server] listening on 127.0.0.1:%d\n", g_port);

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
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--model") && i + 1 < argc)
            g_model_path = argv[++i];
        else if (!strcmp(argv[i], "--tokenizer") && i + 1 < argc)
            g_tokenizer_path = argv[++i];
        else if (!strcmp(argv[i], "--port") && i + 1 < argc)
            g_port = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--gpu-layers") && i + 1 < argc)
            g_gpu_layers = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--threads") && i + 1 < argc)
            g_n_threads = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--ref-audio") && i + 1 < argc)
            g_ref_audio_path = argv[++i];
        else if (!strcmp(argv[i], "--ref-text") && i + 1 < argc)
            g_ref_text = argv[++i];
        else if (!strcmp(argv[i], "--cpu"))
            g_cpu_only = true;
    }

    if (!g_model_path || !g_tokenizer_path) {
        fprintf(stderr, "usage: fish2_server --model <model.gguf> --tokenizer <tokenizer.json> \\\n"
                        "                   [--port 9988] [--gpu-layers 20] [--threads 8] [--cpu] \\\n"
                        "                   [--ref-audio <wav> --ref-text <text>]\n");
        return 1;
    }

    if (!load_model())
        return 1;

    server_loop();
    return 0;
}
