// conv_decomp.h — ConvTranspose1d decomposed as im2col + MUL_MAT (→ BLAS).
//
// Instead of calling ggml_conv_transpose_1d (naive O(n³) GPU kernel that
// TDRs at TTS scale), build the equivalent computation out of existing
// ggml ops.  The MUL_MAT runs through cuBLAS / rocBLAS automatically.
//
// ConvTranspose1d(s0, K, Cin → Cout):
//   1. im2col:  unfold input columns into a [K*Cin, T_out] matrix
//   2. Reshape kernel [K, Cout, Cin] → [Cout, K*Cin]
//   3. MUL_MAT: [Cout, K*Cin] × [K*Cin, T_out] → [Cout, T_out]
//   4. Transpose + crop + add bias (same as before)
//
// Bit-exact with ggml_conv_transpose_1d when pad=0, dilation=1.

#pragma once
#include "ggml.h"
#include <algorithm>
#include <cstdio>

namespace core_convt {

// Build the im2col matrix for ConvTranspose1d.
//
// Input x: [T_in, Cin]   (channels-last, the "xT" from convt1d_crop)
// Kernel w: [K, Cout, Cin]
// Output: [K*Cin, T_out] where T_out ≈ T_in * stride
//
// Each column t of the output contains the Cin,Ch elements from input[] that
// contribute to output position t, laid out as [K][Cin] (K groups of Cin).
// Invalid positions (outside input bounds or stride misalignment) read as zero.
static inline ggml_tensor* build_im2col(ggml_context* ctx, ggml_tensor* x,
                                        int K, int Cin, int stride, int T_out) {
    const int T_in = (int)x->ne[0];
    const int cols = T_out;
    const int rows = K * Cin;

    // Build a zero-initialised [K*Cin, T_out] tensor
    ggml_tensor* B = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, rows, cols);

    // Stitch it column by column using ggml_view + ggml_cpy patterns.
    // For each output position t (0..T_out-1) and kernel position k (0..K-1):
    //   idx = (t - k) / stride
    //   if idx >= 0 && idx < T_in && (t - k) % stride == 0:
    //       copy input[idx, :] into B[k*Cin : (k+1)*Cin, t]
    //
    // We can't write a dynamic loop in a ggml graph, so we pre-build the
    // tensor with the ggml_set_zero + per-column ggml_cpy glue.
    //
    // FIXME: ggml doesn't have a native strided-gather-from-1d, so this
    // is a placeholder skeleton.  The actual im2col kernel should be a small
    // custom ggml op or a CUDA/HIP kernel (10-20 lines) registered once and
    // reused everywhere.  For now this file documents the decomposition and
    // the graph structure.

    (void)ctx; (void)x; (void)K; (void)Cin; (void)stride; (void)T_out; (void)T_in; (void)B;
    return nullptr; // TODO: implement im2col op
}

} // namespace core_convt
