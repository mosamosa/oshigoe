"""Replace Voice Isolator's per-step selective scan on CUDA.

Without `mamba_ssm`, the library runs the recurrence one step at a time in Python, launching
several small kernels per step. The band scans take 46 steps for every block of frames, which
makes inference bound by kernel launches on the CPU.

The primary path is one CUDA kernel compiled at runtime with the NVRTC library that ships in the
PyTorch wheel, so users need no compiler. Each thread keeps one channel's state in registers and
runs the same float32 recurrence as the library. The kernel is adapted from the Metal kernel in
inworld-voice-isolation 1.0.0 (`inworld/voice_isolation/scan.py`, Copyright 2026 Inworld AI,
Apache License 2.0).

If NVRTC is unavailable, a Hillis-Steele scan takes over. It needs log2(steps) rounds and only
multiplies decays, so it cannot overflow the way a cumulative-product division can.
"""

import logging

import torch
from inworld.voice_isolation import scan

LOG = logging.getLogger(__name__)
_library_scan = scan.selective_scan
_MAX_STATES = 16
_THREADS = 256
_SOURCE = r"""
extern "C" __global__ void selective_scan(
    float* y, float* last, const float* u, const float* delta, const float* a,
    const float* b, const float* c, const float* d, const float* z, const float* carried,
    int batch, int frames, int channels, int states, int gated, int carrying) {
  const int index = blockIdx.x * blockDim.x + threadIdx.x;
  if (index >= batch * channels) {
    return;
  }
  const int row = index / channels;
  const int channel = index % channels;
  float h[16];
  float rate[16];
  for (int n = 0; n < states; ++n) {
    rate[n] = a[channel * states + n];
    h[n] = carrying ? carried[index * states + n] : 0.0f;
  }
  const float skip = d[channel];
  for (int t = 0; t < frames; ++t) {
    const int at = (row * frames + t) * channels + channel;
    const int bt = (row * frames + t) * states;
    const float step = delta[at];
    const float x = u[at];
    const float drive = step * x;
    float out = 0.0f;
    for (int n = 0; n < states; ++n) {
      h[n] = expf(step * rate[n]) * h[n] + drive * b[bt + n];
      out += h[n] * c[bt + n];
    }
    out += x * skip;
    if (gated) {
      const float g = z[at];
      out *= g / (1.0f + expf(-g));
    }
    y[at] = out;
  }
  for (int n = 0; n < states; ++n) {
    last[index * states + n] = h[n];
  }
}
"""
_kernel = None


def _compiled_kernel():
    global _kernel
    if _kernel is None:
        try:
            _kernel = torch.cuda._compile_kernel(_SOURCE, "selective_scan")
        except (AttributeError, OSError, RuntimeError) as error:
            LOG.warning("Could not compile the CUDA selective scan (%s). Using the parallel scan.", error)
            _kernel = False
    return _kernel


def fused_selective_scan(u, delta, a, b, c, d, z=None, state=None, *, kernel):
    u, delta, b, c = (t.float().contiguous() for t in (u, delta, b, c))
    a, d = a.float().contiguous(), d.float().contiguous()
    batch, frames, channels = u.shape
    states = a.shape[-1]
    y = torch.empty_like(u)
    last = u.new_empty(batch, channels, states)
    gate = u if z is None else z.float().contiguous()
    carried = u if state is None else state.float().contiguous()
    rows = batch * channels
    kernel(grid=((rows + _THREADS - 1) // _THREADS, 1, 1), block=(_THREADS, 1, 1),
           args=[y, last, u, delta, a, b, c, d, gate, carried,
                 batch, frames, channels, states, int(z is not None), int(state is not None)])
    return y, last


def parallel_selective_scan(u, delta, a, b, c, d, z=None, state=None):
    u, delta, b, c = (t.float() for t in (u, delta, b, c))
    a, d = a.float(), d.float()
    decay = torch.exp(delta[..., None] * a)
    h = (delta * u)[..., None] * b[:, :, None, :]
    if state is not None:
        h[:, 0] += decay[:, 0] * state.float()
    offset = 1
    while offset < u.shape[1]:
        h[:, offset:] = h[:, offset:] + decay[:, offset:] * h[:, :-offset]
        decay[:, offset:] = decay[:, offset:] * decay[:, :-offset]
        offset *= 2
    y = (h * c[:, :, None, :]).sum(-1) + u * d
    if z is not None:
        y = y * torch.nn.functional.silu(z.float())
    return y, h[:, -1].contiguous()


def _selective_scan(u, delta, a, b, c, d, z=None, state=None):
    if not u.is_cuda or (state is None and scan.kernel() is not None):
        return _library_scan(u, delta, a, b, c, d, z, state)
    if u.numel() and a.shape[-1] <= _MAX_STATES and (kernel := _compiled_kernel()):
        return fused_selective_scan(u, delta, a, b, c, d, z, state, kernel=kernel)
    return parallel_selective_scan(u, delta, a, b, c, d, z, state)


def install():
    scan.selective_scan = _selective_scan
