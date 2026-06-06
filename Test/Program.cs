// TTS GPU smoke test — C# equivalent of test_tts.py
// Tests whether the qwen3_tts_shared.dll GPU init crash is Python-specific.

using System.Runtime.InteropServices;
using System.Text;

// ── Struct matching qwen3_tts_context_params ──
[StructLayout(LayoutKind.Sequential, Pack = 8)]
struct TtsParams
{
    public int    n_threads;
    public int    verbosity;      // 0=silent, 1=normal
    public byte   use_gpu;        // c_bool = 1 byte
    // 3 bytes implicit padding — handled by LayoutKind.Sequential
    public float  temperature;    // 0 = greedy
    public ulong  seed;           // uint64_t
    public int    max_codec_steps; // 0 = use default (1500)
    public byte   flash_attn;
    // 3 bytes implicit padding at end
}

static class Native
{
    const string DllName = "qwen3_tts_shared.dll";

    [DllImport(DllName, CallingConvention = CallingConvention.Cdecl)]
    public static extern TtsParams qwen3_tts_context_default_params();

    [DllImport(DllName, CallingConvention = CallingConvention.Cdecl)]
    public static extern nint qwen3_tts_init_from_file(byte[] path, TtsParams p);

    [DllImport(DllName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int qwen3_tts_set_codec_path(nint ctx, byte[] path);

    [DllImport(DllName, CallingConvention = CallingConvention.Cdecl)]
    public static extern int qwen3_tts_set_voice_prompt_with_text(
        nint ctx, byte[] wavPath, byte[] refText);

    [DllImport(DllName, CallingConvention = CallingConvention.Cdecl)]
    public static extern nint qwen3_tts_synthesize(
        nint ctx, byte[] text, out int nSamples);

    [DllImport(DllName, CallingConvention = CallingConvention.Cdecl)]
    public static extern void qwen3_tts_pcm_free(nint pcm);

    [DllImport(DllName, CallingConvention = CallingConvention.Cdecl)]
    public static extern void qwen3_tts_free(nint ctx);

    // ── DLL search path helpers ──
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern nint LoadLibraryEx(string lpFileName, nint hFile, uint dwFlags);
    [DllImport("kernel32.dll", SetLastError = true)]
    public static extern bool SetDllDirectoryW(string? lpPathName);

    
}

class Program
{
    static string DllDir = AppContext.BaseDirectory;

    static byte[] Enc(string s) => Encoding.UTF8.GetBytes(s);
    // On Windows the DLL's C runtime uses the ANSI code page for fopen,
    // but for ASCII paths UTF-8 bytes == ANSI bytes.
    // If non-ASCII paths are needed, use Encoding.GetEncoding(0) for the
    // system ANSI code page (equivalent to Python "mbcs").

    static void WriteWav(string path, float[] samples, int sr = 24000)
    {
        using var fs = File.OpenWrite(path);
        using var bw = new BinaryWriter(fs);
        int dataLen = samples.Length * 2;
        bw.Write(Encoding.ASCII.GetBytes("RIFF"));
        bw.Write(36 + dataLen);
        bw.Write(Encoding.ASCII.GetBytes("WAVE"));
        bw.Write(Encoding.ASCII.GetBytes("fmt "));
        bw.Write(16);          // chunk size
        bw.Write((short)1);    // PCM
        bw.Write((short)1);    // mono
        bw.Write(sr);
        bw.Write(sr * 2);      // byte rate
        bw.Write((short)2);    // block align
        bw.Write((short)16);   // bits per sample
        bw.Write(Encoding.ASCII.GetBytes("data"));
        bw.Write(dataLen);
        foreach (var s in samples)
        {
            int v = (int)(s * 32767.0f);
            v = Math.Clamp(v, -32768, 32767);
            bw.Write((short)v);
        }
    }

    static void ShowHelp()
    {
        Console.WriteLine("Usage: TTS-TEST <talker.gguf> <codec.gguf> <ref.wav> <ref_text> <tts_text>");
        Console.WriteLine();
        Console.WriteLine("  TTS GPU smoke test — mirrors test_tts.py");
        Console.WriteLine();
        Console.WriteLine("  talker.gguf   Path to Qwen3-TTS talker model");
        Console.WriteLine("  codec.gguf    Path to Qwen3-TTS codec model");
        Console.WriteLine("  ref.wav       Reference voice WAV (24kHz mono)");
        Console.WriteLine("  ref_text      Transcription of the reference audio");
        Console.WriteLine("  tts_text      Text to synthesise");
        Console.WriteLine();
        Console.WriteLine("  DLLs (qwen3_tts_shared.dll + ggml*.dll) must be next to the exe.");
    }

    static void Main(string []args)
    {
        if (args.Length == 0 || args[0] == "--help" || args[0] == "-h")
        {
            ShowHelp();
            return;
        }
        if (args.Length < 5)
        {
            Console.WriteLine("ERROR: expected 5 arguments, got " + args.Length);
            Console.WriteLine("Run with --help for usage.");
            return;
        }

        Console.WriteLine("C# TTS GPU test");
        Console.WriteLine($"  sizeof(TtsParams) = {Marshal.SizeOf<TtsParams>()}");
        Console.WriteLine($"  DLL dir: {DllDir}");

        // Set DLL search directory so Windows finds ggml*.dll etc.
        if (!Native.SetDllDirectoryW(DllDir))
            Console.WriteLine($"  WARNING: SetDllDirectory failed: {Marshal.GetLastWin32Error()}");

        // Manually load the main DLL (pulls in deps from DllDir)
        nint hDll = Native.LoadLibraryEx(
            Path.Combine(DllDir, "qwen3_tts_shared.dll"),
            0, 0);  // hFile=0, dwFlags=0 (SetDllDirectoryW already set search path)
        Console.WriteLine($"  qwen3_tts_shared handle: 0x{hDll:X}");
        if (hDll == 0)
        {
            Console.WriteLine($"  ERROR: {Marshal.GetLastWin32Error()}");
            return;
        }

        string talker  = args[0];
        string codec   = args[1];
        string refWav  = args[2];
        string refText = args[3];
        string ttsText = args[4];

        Console.WriteLine("Init talker...");
        var p = Native.qwen3_tts_context_default_params();
        Console.WriteLine($"  default: n_threads={p.n_threads} verbosity={p.verbosity} "
            + $"use_gpu={p.use_gpu} temperature={p.temperature} seed={p.seed} "
            + $"flash_attn={p.flash_attn}");

        p.verbosity = 1;
        p.use_gpu   = 1;
        p.seed      = 42;
        p.n_threads = 16;
        p.flash_attn = 0;

        nint ctx = Native.qwen3_tts_init_from_file(Enc(talker), p);
        if (ctx == 0)
        {
            Console.WriteLine("ERROR: talker init returned null");
            return;
        }

        Console.WriteLine("Codec...");
        if (Native.qwen3_tts_set_codec_path(ctx, Enc(codec)) != 0)
        {
            Console.WriteLine("ERROR: codec");
            return;
        }

        Console.WriteLine("Voice...");
        if (Native.qwen3_tts_set_voice_prompt_with_text(ctx, Enc(refWav), Enc(refText)) != 0)
        {
            Console.WriteLine("ERROR: voice");
            return;
        }

        Console.WriteLine("Synth: " + ttsText);
        int nSamples;
        nint pcm = Native.qwen3_tts_synthesize(ctx, Enc(ttsText), out nSamples);
        if (pcm == 0 || nSamples <= 0)
        {
            Console.WriteLine("ERROR: synth returned no audio");
            return;
        }

        float dur = nSamples / 24000.0f;
        Console.WriteLine($"  {nSamples} samples, {dur:F2}s");

        float[] samples = new float[nSamples];
        Marshal.Copy(pcm, samples, 0, nSamples);
        Native.qwen3_tts_pcm_free(pcm);
        Native.qwen3_tts_free(ctx);

        string outWav = Path.Combine(
            Path.GetDirectoryName(System.Reflection.Assembly.GetExecutingAssembly().Location)!,
            "cs_test_out.wav");
        WriteWav(outWav, samples);
        Console.WriteLine($"Wrote {outWav}");
        Console.WriteLine("Done.");
    }
}
