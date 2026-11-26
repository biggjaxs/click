// Click Engine: runs Qwen2-family language models on the processor, written from scratch for Click.
//
// - Weights stay in the model file and are memory-mapped (instant load, shared with the OS file cache).
// - 8-bit weights (Q8_0: blocks of 32 values with one scale) multiplied with 8-bit copies of the activations
//   using AVX2 integer instructions: 4x less memory traffic than 32-bit floats, which is what limits speed.
// - All processor cores work together through a small spin-waiting thread pool (no thread start-up per step).
// - Prompts are processed in chunks of up to 256 tokens at once, so each weight is read once per chunk
//   instead of once per token: long prompts are many times faster than word-by-word.
// - The attention memory (KV cache) is kept between requests, so a conversation that grows only processes
//   the new part. The caller decides where new tokens start (position) to reuse a shared beginning.
#include <atomic>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <thread>
#include <unistd.h>
#include <vector>
#include <immintrin.h>

namespace {

constexpr int QK = 32;
enum { T_F32 = 0, T_F16 = 1, T_Q8_0 = 8 };

struct BlockQ8 {  // one block of activations
    float d;
    int8_t qs[QK];
};

inline float f16_to_f32(uint16_t h) { return _cvtsh_ss(h); }

// ---------------- thread pool ----------------
struct Pool {
    int n = 1;
    std::vector<std::thread> threads;
    std::atomic<int> gen{0}, done{0};
    std::atomic<bool> quit{false};
    void (*fn)(void*, int, int) = nullptr;  // (ctx, thread index, n threads)
    void* ctx = nullptr;

    void start(int nthreads) {
        n = nthreads < 1 ? 1 : nthreads;
        for (int i = 1; i < n; i++) threads.emplace_back([this, i] { loop(i); });
    }
    void loop(int idx) {
        int seen = 0;
        while (true) {
            int spins = 0;
            while (gen.load(std::memory_order_acquire) == seen) {
                if (quit.load()) return;
                if (++spins < 20000) _mm_pause(); else std::this_thread::yield();
            }
            seen = gen.load(std::memory_order_acquire);
            fn(ctx, idx, n);
            done.fetch_add(1, std::memory_order_acq_rel);
        }
    }
    void run(void (*f)(void*, int, int), void* c) {
        if (n == 1) { f(c, 0, 1); return; }
        fn = f; ctx = c;
        done.store(0);
        gen.fetch_add(1, std::memory_order_acq_rel);
        f(c, 0, n);
        int spins = 0;
        while (done.load(std::memory_order_acquire) < n - 1) {
            if (++spins < 20000) _mm_pause(); else std::this_thread::yield();
        }
    }
    ~Pool() {
        quit = true;
        gen.fetch_add(1);
        for (auto& t : threads) t.join();
    }
};

// ---------------- kernels ----------------
inline float hsum8(__m256 v) {
    __m128 lo = _mm256_castps256_ps128(v), hi = _mm256_extractf128_ps(v, 1);
    lo = _mm_add_ps(lo, hi);
    __m128 sh = _mm_movehdup_ps(lo);
    __m128 s = _mm_add_ps(lo, sh);
    sh = _mm_movehl_ps(sh, s);
    s = _mm_add_ss(s, sh);
    return _mm_cvtss_f32(s);
}

inline __m256 dot_block(const uint8_t* wb, const BlockQ8& a) {
    const __m256i qx = _mm256_loadu_si256((const __m256i*)(wb + 2));
    const __m256i qy = _mm256_loadu_si256((const __m256i*)a.qs);
    const __m256i ax = _mm256_sign_epi8(qx, qx);
    const __m256i sy = _mm256_sign_epi8(qy, qx);
    const __m256i p16 = _mm256_maddubs_epi16(ax, sy);
    const __m256i p32 = _mm256_madd_epi16(p16, _mm256_set1_epi16(1));
    return _mm256_cvtepi32_ps(p32);
}

// one weight row (Q8_0) with up to 4 activation rows at once (each weight block is loaded once)
inline void dot_row_q8(const uint8_t* w, int nb, const BlockQ8* const* xs, int nx, float* out) {
    __m256 acc0 = _mm256_setzero_ps(), acc1 = acc0, acc2 = acc0, acc3 = acc0;
    for (int b = 0; b < nb; b++) {
        const uint8_t* wb = w + (size_t)b * 34;
        const float dw = f16_to_f32(*(const uint16_t*)wb);
        const __m256i qx = _mm256_loadu_si256((const __m256i*)(wb + 2));
        const __m256i ax = _mm256_sign_epi8(qx, qx);
        const __m256i ones = _mm256_set1_epi16(1);
#define ACC(k, acc)                                                                                  \
        {                                                                                            \
            const BlockQ8& a = xs[k][b];                                                             \
            const __m256i qy = _mm256_loadu_si256((const __m256i*)a.qs);                            \
            const __m256i p = _mm256_madd_epi16(_mm256_maddubs_epi16(ax, _mm256_sign_epi8(qy, qx)), ones); \
            acc = _mm256_fmadd_ps(_mm256_set1_ps(dw * a.d), _mm256_cvtepi32_ps(p), acc);             \
        }
        ACC(0, acc0)
        if (nx > 1) ACC(1, acc1)
        if (nx > 2) ACC(2, acc2)
        if (nx > 3) ACC(3, acc3)
#undef ACC
    }
    out[0] = hsum8(acc0);
    if (nx > 1) out[1] = hsum8(acc1);
    if (nx > 2) out[2] = hsum8(acc2);
    if (nx > 3) out[3] = hsum8(acc3);
}

void quantize_row(const float* x, BlockQ8* y, int n) {
    for (int b = 0; b < n / QK; b++) {
        const float* v = x + b * QK;
        __m256 mx = _mm256_setzero_ps();
        const __m256 sign = _mm256_set1_ps(-0.0f);
        for (int i = 0; i < QK; i += 8) mx = _mm256_max_ps(mx, _mm256_andnot_ps(sign, _mm256_loadu_ps(v + i)));
        float amax = 0;
        alignas(32) float tmp[8];
        _mm256_store_ps(tmp, mx);
        for (float t : tmp) amax = t > amax ? t : amax;
        const float d = amax / 127.0f, id = d ? 1.0f / d : 0.0f;
        y[b].d = d;
        for (int i = 0; i < QK; i++) y[b].qs[i] = (int8_t)lrintf(v[i] * id);
    }
}

void dequant_row_q8(const uint8_t* w, float* out, int n) {
    for (int b = 0; b < n / QK; b++) {
        const uint8_t* wb = w + (size_t)b * 34;
        const float d = f16_to_f32(*(const uint16_t*)wb);
        const int8_t* q = (const int8_t*)(wb + 2);
        for (int i = 0; i < QK; i++) out[b * QK + i] = d * q[i];
    }
}

struct Mat {  // a weight matrix in the file: `rows` outputs, each a row of `cols` inputs
    const uint8_t* data = nullptr;
    int type = -1, rows = 0, cols = 0;
    size_t row_bytes() const { return type == T_Q8_0 ? (size_t)cols / QK * 34 : (size_t)cols * 4; }
};

struct Layer {
    const float *attn_norm = nullptr, *ffn_norm = nullptr, *bq = nullptr, *bk = nullptr, *bv = nullptr;
    Mat wq, wk, wv, wo, gate, up, down;
};

struct MatmulJob {
    const Mat* w;
    const BlockQ8* x;  // n rows of activations, each cols/QK blocks
    int n;
    float* y;          // n rows of w->rows outputs
    const float* bias;
    std::atomic<int> next{0};
};

// 2 weight rows x 4 activation rows at a time: every weight block and every activation block that's loaded
// is used several times, which matters most when reading long prompts.
inline void dot_2x4(const uint8_t* w0, const uint8_t* w1, int nb, const BlockQ8* const* xs, int nx,
                    float* o0, float* o1) {
    __m256 a00 = _mm256_setzero_ps(), a01 = a00, a02 = a00, a03 = a00, a10 = a00, a11 = a00, a12 = a00, a13 = a00;
    const __m256i ones = _mm256_set1_epi16(1);
    for (int b = 0; b < nb; b++) {
        const uint8_t* b0 = w0 + (size_t)b * 34;
        const uint8_t* b1 = w1 + (size_t)b * 34;
        const float d0 = f16_to_f32(*(const uint16_t*)b0), d1 = f16_to_f32(*(const uint16_t*)b1);
        const __m256i q0 = _mm256_loadu_si256((const __m256i*)(b0 + 2));
        const __m256i q1 = _mm256_loadu_si256((const __m256i*)(b1 + 2));
        const __m256i x0 = _mm256_sign_epi8(q0, q0), x1 = _mm256_sign_epi8(q1, q1);
#define PAIR(k, A0, A1)                                                                                     \
        {                                                                                                   \
            const BlockQ8& a = xs[k][b];                                                                    \
            const __m256i qy = _mm256_loadu_si256((const __m256i*)a.qs);                                   \
            const __m256i p0 = _mm256_madd_epi16(_mm256_maddubs_epi16(x0, _mm256_sign_epi8(qy, q0)), ones); \
            const __m256i p1 = _mm256_madd_epi16(_mm256_maddubs_epi16(x1, _mm256_sign_epi8(qy, q1)), ones); \
            A0 = _mm256_fmadd_ps(_mm256_set1_ps(d0 * a.d), _mm256_cvtepi32_ps(p0), A0);                     \
            A1 = _mm256_fmadd_ps(_mm256_set1_ps(d1 * a.d), _mm256_cvtepi32_ps(p1), A1);                     \
        }
        PAIR(0, a00, a10)
        if (nx > 1) PAIR(1, a01, a11)
        if (nx > 2) PAIR(2, a02, a12)
        if (nx > 3) PAIR(3, a03, a13)
#undef PAIR
    }
    o0[0] = hsum8(a00); o1[0] = hsum8(a10);
    if (nx > 1) { o0[1] = hsum8(a01); o1[1] = hsum8(a11); }
    if (nx > 2) { o0[2] = hsum8(a02); o1[2] = hsum8(a12); }
    if (nx > 3) { o0[3] = hsum8(a03); o1[3] = hsum8(a13); }
}

void matmul_worker(void* c, int, int) {
    MatmulJob* j = (MatmulJob*)c;
    const int nb = j->w->cols / QK;
    const int rows = j->w->rows;
    const size_t rb = j->w->row_bytes();
    // a tile of weight rows that fits in the core's own cache (~96 KB); the words stream past it 8 at a time,
    // so both the weights and the words are mostly read from fast cache, not main memory
    int tile = (int)(98304 / rb);
    tile = tile < 4 ? 4 : (tile > 64 ? 64 : tile & ~1);
    if (j->n == 1) tile = 32;
    while (true) {
        const int r0 = j->next.fetch_add(tile);
        if (r0 >= rows) break;
        const int r1 = r0 + tile < rows ? r0 + tile : rows;
        for (int t0 = 0; t0 < j->n; t0 += 8) {
            const int t1 = t0 + 8 < j->n ? t0 + 8 : j->n;
            int r = r0;
            for (; r + 1 < r1; r += 2) {
                const uint8_t* w0 = j->w->data + (size_t)r * rb;
                const float bb0 = j->bias ? j->bias[r] : 0.0f, bb1 = j->bias ? j->bias[r + 1] : 0.0f;
                for (int t = t0; t < t1; t += 4) {
                    const int k = t1 - t < 4 ? t1 - t : 4;
                    const BlockQ8* xs[4];
                    for (int i = 0; i < k; i++) xs[i] = j->x + (size_t)(t + i) * nb;
                    float o0[4], o1[4];
                    dot_2x4(w0, w0 + rb, nb, xs, k, o0, o1);
                    for (int i = 0; i < k; i++) {
                        j->y[(size_t)(t + i) * rows + r] = o0[i] + bb0;
                        j->y[(size_t)(t + i) * rows + r + 1] = o1[i] + bb1;
                    }
                }
            }
            for (; r < r1; r++) {
                const uint8_t* wr = j->w->data + (size_t)r * rb;
                const float b = j->bias ? j->bias[r] : 0.0f;
                for (int t = t0; t < t1; t += 4) {
                    const int k = t1 - t < 4 ? t1 - t : 4;
                    const BlockQ8* xs[4];
                    for (int i = 0; i < k; i++) xs[i] = j->x + (size_t)(t + i) * nb;
                    float out[4];
                    dot_row_q8(wr, nb, xs, k, out);
                    for (int i = 0; i < k; i++) j->y[(size_t)(t + i) * rows + r] = out[i] + b;
                }
            }
        }
    }
}

}  // namespace

struct CEModel {
    int n_layer, n_embd, n_ff, n_head, n_head_kv, n_vocab, n_ctx, head_dim;
    float rope_base, eps;
    void* map = nullptr;
    size_t map_size = 0;
    const uint8_t* base = nullptr;
    Mat tok_embd, output;
    const float* output_norm = nullptr;
    std::vector<Layer> layers;
    std::vector<float> k_cache, v_cache;  // [layer][pos][kv_dim]
    std::vector<float> rope_cos, rope_sin;  // [pos][head_dim/2]
    Pool pool;
    // scratch (sized for the largest chunk)
    int max_chunk = 256;
    std::vector<float> x, xn, q, k, v, att, hb, hb2, logits;
    std::vector<BlockQ8> xq;
    std::vector<float> scores;  // per thread
};

extern "C" {

CEModel* ce_create(const char* path, int n_layer, int n_embd, int n_ff, int n_head, int n_head_kv, int n_vocab,
                   float rope_base, float eps, int n_ctx, int n_threads) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return nullptr;
    struct stat st;
    fstat(fd, &st);
    void* m = mmap(nullptr, st.st_size, PROT_READ, MAP_SHARED, fd, 0);
    close(fd);
    if (m == MAP_FAILED) return nullptr;
    CEModel* M = new CEModel();
    M->map = m;
    M->map_size = st.st_size;
    M->base = (const uint8_t*)m;
    M->n_layer = n_layer; M->n_embd = n_embd; M->n_ff = n_ff; M->n_head = n_head; M->n_head_kv = n_head_kv;
    M->n_vocab = n_vocab; M->n_ctx = n_ctx; M->rope_base = rope_base; M->eps = eps;
    M->head_dim = n_embd / n_head;
    M->layers.resize(n_layer);
    const int kv_dim = n_head_kv * M->head_dim;
    M->k_cache.assign((size_t)n_layer * n_ctx * kv_dim, 0.0f);
    M->v_cache.assign((size_t)n_layer * n_ctx * kv_dim, 0.0f);
    const int half = M->head_dim / 2;
    M->rope_cos.resize((size_t)n_ctx * half);
    M->rope_sin.resize((size_t)n_ctx * half);
    for (int p = 0; p < n_ctx; p++)
        for (int i = 0; i < half; i++) {
            const double f = std::pow((double)rope_base, -2.0 * i / M->head_dim);
            M->rope_cos[(size_t)p * half + i] = (float)std::cos(p * f);
            M->rope_sin[(size_t)p * half + i] = (float)std::sin(p * f);
        }
    const int C = M->max_chunk;
    M->x.resize((size_t)C * n_embd);
    M->xn.resize((size_t)C * n_embd);
    M->q.resize((size_t)C * n_embd);
    M->k.resize((size_t)C * kv_dim);
    M->v.resize((size_t)C * kv_dim);
    M->att.resize((size_t)C * n_embd);
    M->hb.resize((size_t)C * n_ff);
    M->hb2.resize((size_t)C * n_ff);
    M->xq.resize((size_t)C * (n_ff > n_embd ? n_ff : n_embd) / QK);
    M->logits.resize(n_vocab);
    M->pool.start(n_threads);
    M->scores.resize((size_t)M->pool.n * n_ctx);
    return M;
}

// kind: 0 tok_embd, 1 output, 2 output_norm, 10.. per layer (10 attn_norm, 11 ffn_norm, 12 bq, 13 bk, 14 bv,
//       20 wq, 21 wk, 22 wv, 23 wo, 24 gate, 25 up, 26 down)
int ce_tensor(CEModel* M, int kind, int layer, uint64_t offset, int type, int rows, int cols) {
    const uint8_t* p = M->base + offset;
    if (type != T_F32 && type != T_Q8_0) return -1;
    Mat m{p, type, rows, cols};
    if (kind == 0) { M->tok_embd = m; return 0; }
    if (kind == 1) { M->output = m; return 0; }
    if (kind == 2) { M->output_norm = (const float*)p; return 0; }
    if (layer < 0 || layer >= M->n_layer) return -2;
    Layer& L = M->layers[layer];
    switch (kind) {
        case 10: L.attn_norm = (const float*)p; break;
        case 11: L.ffn_norm = (const float*)p; break;
        case 12: L.bq = (const float*)p; break;
        case 13: L.bk = (const float*)p; break;
        case 14: L.bv = (const float*)p; break;
        case 20: L.wq = m; break;
        case 21: L.wk = m; break;
        case 22: L.wv = m; break;
        case 23: L.wo = m; break;
        case 24: L.gate = m; break;
        case 25: L.up = m; break;
        case 26: L.down = m; break;
        default: return -3;
    }
    return 0;
}

}  // extern "C"

namespace {

void rmsnorm(const float* x, const float* w, float* out, int n, float eps) {
    double ss = 0;
    for (int i = 0; i < n; i++) ss += (double)x[i] * x[i];
    const float s = 1.0f / std::sqrt((float)(ss / n) + eps);
    for (int i = 0; i < n; i++) out[i] = x[i] * s * w[i];
}

void matmul(CEModel* M, const Mat& w, const float* x, int n, float* y, const float* bias) {
    const int nb = w.cols / QK;
    for (int t = 0; t < n; t++) quantize_row(x + (size_t)t * w.cols, M->xq.data() + (size_t)t * nb, w.cols);
    MatmulJob j{&w, M->xq.data(), n, y, bias};
    M->pool.run(matmul_worker, &j);
}

void rope(CEModel* M, float* vec, int n_heads, int pos) {
    const int hd = M->head_dim, half = hd / 2;
    const float* c = &M->rope_cos[(size_t)pos * half];
    const float* s = &M->rope_sin[(size_t)pos * half];
    for (int h = 0; h < n_heads; h++) {
        float* v = vec + h * hd;
        for (int i = 0; i < half; i++) {  // rotate the two halves (the "neox" layout Qwen uses)
            const float a = v[i], b = v[i + half];
            v[i] = a * c[i] - b * s[i];
            v[i + half] = a * s[i] + b * c[i];
        }
    }
}

struct AttnJob {
    CEModel* M;
    int layer, n, pos;
    std::atomic<int> next{0};
};

void attn_worker(void* c, int tid, int) {
    AttnJob* j = (AttnJob*)c;
    CEModel* M = j->M;
    const int hd = M->head_dim, kv_dim = M->n_head_kv * hd, group = M->n_head / M->n_head_kv;
    const float scale = 1.0f / std::sqrt((float)hd);
    float* sc = &M->scores[(size_t)tid * M->n_ctx];
    const float* kc = &M->k_cache[(size_t)j->layer * M->n_ctx * kv_dim];
    const float* vc = &M->v_cache[(size_t)j->layer * M->n_ctx * kv_dim];
    const int total = j->n * M->n_head;
    while (true) {
        const int w = j->next.fetch_add(1);
        if (w >= total) break;
        const int t = w / M->n_head, h = w % M->n_head, kvh = h / group;
        const int upto = j->pos + t;  // causal: sees itself and everything before
        const float* qv = &M->q[(size_t)t * M->n_embd + h * hd];
        float mx = -1e30f;
        for (int p = 0; p <= upto; p++) {
            const float* kv = kc + (size_t)p * kv_dim + kvh * hd;
            __m256 acc = _mm256_setzero_ps();
            for (int i = 0; i < hd; i += 8) acc = _mm256_fmadd_ps(_mm256_loadu_ps(qv + i), _mm256_loadu_ps(kv + i), acc);
            const float s = hsum8(acc) * scale;
            sc[p] = s;
            if (s > mx) mx = s;
        }
        float sum = 0;
        for (int p = 0; p <= upto; p++) { sc[p] = std::exp(sc[p] - mx); sum += sc[p]; }
        const float inv = 1.0f / sum;
        float* out = &M->att[(size_t)t * M->n_embd + h * hd];
        for (int i = 0; i < hd; i += 8) {
            __m256 acc = _mm256_setzero_ps();
            for (int p = 0; p <= upto; p++)
                acc = _mm256_fmadd_ps(_mm256_set1_ps(sc[p]), _mm256_loadu_ps(vc + (size_t)p * kv_dim + kvh * hd + i), acc);
            _mm256_storeu_ps(out + i, _mm256_mul_ps(acc, _mm256_set1_ps(inv)));
        }
    }
}

void forward_chunk(CEModel* M, const int* tokens, int n, int pos, bool want_logits) {
    const int E = M->n_embd, hd = M->head_dim, kv_dim = M->n_head_kv * hd, F = M->n_ff;
    for (int t = 0; t < n; t++) dequant_row_q8(M->tok_embd.data + (size_t)tokens[t] * M->tok_embd.row_bytes(),
                                               &M->x[(size_t)t * E], E);
    for (int l = 0; l < M->n_layer; l++) {
        Layer& L = M->layers[l];
        for (int t = 0; t < n; t++) rmsnorm(&M->x[(size_t)t * E], L.attn_norm, &M->xn[(size_t)t * E], E, M->eps);
        matmul(M, L.wq, M->xn.data(), n, M->q.data(), L.bq);
        // k and v reuse the same quantized input: quantize once by calling matmul twice (cheap relative to math)
        matmul(M, L.wk, M->xn.data(), n, M->k.data(), L.bk);
        matmul(M, L.wv, M->xn.data(), n, M->v.data(), L.bv);
        for (int t = 0; t < n; t++) {
            rope(M, &M->q[(size_t)t * E], M->n_head, pos + t);
            rope(M, &M->k[(size_t)t * kv_dim], M->n_head_kv, pos + t);
            float* kc = &M->k_cache[((size_t)l * M->n_ctx + pos + t) * kv_dim];
            float* vc = &M->v_cache[((size_t)l * M->n_ctx + pos + t) * kv_dim];
            std::memcpy(kc, &M->k[(size_t)t * kv_dim], kv_dim * sizeof(float));
            std::memcpy(vc, &M->v[(size_t)t * kv_dim], kv_dim * sizeof(float));
        }
        AttnJob aj{M, l, n, pos};
        M->pool.run(attn_worker, &aj);
        matmul(M, L.wo, M->att.data(), n, M->xn.data(), nullptr);
        for (size_t i = 0; i < (size_t)n * E; i++) M->x[i] += M->xn[i];
        for (int t = 0; t < n; t++) rmsnorm(&M->x[(size_t)t * E], L.ffn_norm, &M->xn[(size_t)t * E], E, M->eps);
        matmul(M, L.gate, M->xn.data(), n, M->hb.data(), nullptr);
        matmul(M, L.up, M->xn.data(), n, M->hb2.data(), nullptr);
        for (size_t i = 0; i < (size_t)n * F; i++) {
            const float g = M->hb[i];
            M->hb[i] = g / (1.0f + std::exp(-g)) * M->hb2[i];  // SwiGLU
        }
        matmul(M, L.down, M->hb.data(), n, M->xn.data(), nullptr);
        for (size_t i = 0; i < (size_t)n * E; i++) M->x[i] += M->xn[i];
    }
    if (want_logits) {
        rmsnorm(&M->x[(size_t)(n - 1) * E], M->output_norm, M->xn.data(), E, M->eps);
        matmul(M, M->output, M->xn.data(), 1, M->logits.data(), nullptr);
    }
}

}  // namespace

extern "C" {

// Process `n` tokens starting at position `pos` (everything before `pos` must already be in the cache).
// Writes the next-token scores (n_vocab floats) for the last token into `logits` if it isn't null.
int ce_eval(CEModel* M, const int* tokens, int n, int pos, float* logits) {
    if (n <= 0) return 0;
    if (pos + n > M->n_ctx) return -1;
    for (int i = 0; i < n; i += M->max_chunk) {
        const int c = n - i < M->max_chunk ? n - i : M->max_chunk;
        const bool last = i + c >= n;
        forward_chunk(M, tokens + i, c, pos + i, last && logits != nullptr);
    }
    if (logits) std::memcpy(logits, M->logits.data(), sizeof(float) * M->n_vocab);
    return 0;
}

int ce_n_ctx(CEModel* M) { return M->n_ctx; }
int ce_threads(CEModel* M) { return M->pool.n; }

void ce_free(CEModel* M) {
    if (!M) return;
    munmap(M->map, M->map_size);
    delete M;
}

}  // extern "C"
