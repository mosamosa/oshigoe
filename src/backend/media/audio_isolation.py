import logging
import multiprocessing
import select
import socket
import struct
import time
from dataclasses import dataclass

import numpy as np


LOG = logging.getLogger(__name__)
SAMPLE_RATE = 48_000
# Inference cost per request barely depends on its length, so longer packets cut the cost per
# second of audio. Playback starts later by the packet length; PTS pacing keeps audio and video in sync.
PACKET_SAMPLES = SAMPLE_RATE * 200 // 1000
_HEADER = struct.Struct("!cI")
# Each answer starts with the worker's GPU memory in bytes, followed by the voice samples.
_GPU_MEMORY = struct.Struct("!Q")
_STARTUP_TIMEOUT = 90
_READY_REPLY_TIMEOUT = 0.15
_MAX_PROCESSING_LAG = 0.12
_INFERENCE_REPLY_TIMEOUT = PACKET_SAMPLES / SAMPLE_RATE + _MAX_PROCESSING_LAG
_STOP_TIMEOUT = 0.25
_LOAD_SMOOTHING = 0.2
_LOAD_STALE_AFTER = 5
_MAX_MESSAGE_BYTES = 1 << 20


class IsolationUnavailable(Exception):
    pass


def _remaining(deadline):
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise TimeoutError("The Voice Isolator worker did not respond in time")
    return timeout


def _read_exact(connection, size, deadline):
    data = bytearray()
    while len(data) < size:
        if deadline is not None:
            connection.settimeout(_remaining(deadline))
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise EOFError("Voice Isolator worker closed its connection")
        data.extend(chunk)
    return data


def _send(connection, kind, payload=b"", deadline=None):
    if deadline is not None:
        connection.settimeout(_remaining(deadline))
    connection.sendall(_HEADER.pack(kind, len(payload)) + payload)


def _receive(connection, deadline=None):
    kind, size = _HEADER.unpack(_read_exact(connection, _HEADER.size, deadline))
    if size > _MAX_MESSAGE_BYTES:
        raise ValueError("Voice Isolator message is too large")
    return kind, _read_exact(connection, size, deadline)


def _run_worker(connection, model, device):
    with connection:
        try:
            import torch
            from inworld.voice_isolation import VoiceIsolator

            from .parallel_scan import install as install_parallel_scan

            install_parallel_scan()
            isolator = VoiceIsolator(model, mode="streaming", lookahead_frames=0, device=device)
            if isolator.device.type == "cpu":
                torch.set_num_threads(1)
            block_frames = PACKET_SAMPLES // isolator.model.config.hop_length
            warmup = isolator.stream(block_frames=block_frames)
            silence = np.zeros(PACKET_SAMPLES, dtype=np.float32)
            # Warm up with the first two packets of a stream.
            warmup_packets = 2
            if isolator.device.type == "mps" and warmup.window_frames:
                # On MPS, continue until inference reaches the attention window boundary.
                window_samples = warmup.window_frames * warmup.hop + warmup.n_fft // 2
                warmup_packets = max(warmup_packets, (window_samples + len(silence) - 1) // len(silence))
            for _ in range(warmup_packets):
                warmup.push(silence)
            warmup.flush(as_numpy=True)
            _send(connection, b"R", str(isolator.device).encode())
            session = None
            while True:
                kind, payload = _receive(connection)
                if kind == b"F":
                    voice = session.flush(as_numpy=True)
                    session = None
                else:
                    if kind == b"S":
                        session = isolator.stream(block_frames=block_frames)
                    elif kind != b"P":
                        raise ValueError("Unknown Voice Isolator request")
                    samples = np.frombuffer(payload, dtype="<f4")
                    voice = session.push(samples)
                memory = _GPU_MEMORY.pack(_gpu_memory(torch, isolator.device))
                _send(connection, b"A", memory + np.asarray(voice, dtype="<f4").tobytes())
        except (EOFError, BrokenPipeError, ConnectionResetError):
            return
        except Exception:
            LOG.exception("Voice Isolator worker failed")


def _gpu_memory(torch, device):
    if device.type == "cuda":
        return torch.cuda.memory_reserved(device)
    if device.type == "mps":
        return torch.mps.driver_allocated_memory()
    return 0


@dataclass(eq=False)
class _Worker:
    process: multiprocessing.Process
    connection: socket.socket
    started: float
    ready: bool = False
    healthy_since: float | None = None
    processing_lag: float = 0


class AudioIsolation:
    """Manage a single active model worker. Only the active media reader sends inference requests."""

    def __init__(self, model, device, *, worker_target=None):
        self.model = model
        self.requested_device = device
        self.worker_target = worker_target or _run_worker
        self.device = None
        self.gpu_memory = None
        self.load = 0.0
        self.load_at = None
        self.mode = "raw"
        self.worker = None
        self.retired = []
        self.retry_at = 0
        self.retry_delay = 0.5
        self.context = multiprocessing.get_context("spawn")
        self.on_mode_change = None

    def start(self):
        try:
            parent, child = socket.socketpair()
        except OSError as error:
            self._schedule_restart(error)
            return
        process = self.context.Process(
            target=self.worker_target, args=(child, self.model, self.requested_device),
            daemon=True, name="voice-isolator",
        )
        try:
            process.start()
        except (OSError, RuntimeError) as error:
            parent.close()
            self._schedule_restart(error)
            return
        finally:
            child.close()
        self.worker = _Worker(process, parent, time.monotonic())

    def stream(self):
        return _Stream(self)

    def _set_mode(self, mode):
        if self.mode != mode:
            self.mode = mode
            if self.on_mode_change is not None:
                self.on_mode_change()

    def _schedule_restart(self, error):
        self.device = None
        self.gpu_memory = None
        self.load_at = None
        self._set_mode("raw")
        self.retry_at = time.monotonic() + self.retry_delay
        LOG.warning("Voice Isolator is unavailable. Playing the original audio. Retrying in %.1f s (%s).",
                    self.retry_delay, error)
        self.retry_delay = min(self.retry_delay * 2, 30)

    def _discard(self, error):
        worker = self.worker
        self.worker = None
        worker.connection.close()
        if worker.process.exitcode is None:
            worker.process.kill()
        self.retired.append(worker.process)
        self._schedule_restart(error)

    def _reap(self):
        living = []
        for process in self.retired:
            if process.exitcode is not None:
                process.close()
            else:
                living.append(process)
        self.retired = living

    def _ready_worker(self):
        self._reap()
        if self.worker is None and time.monotonic() >= self.retry_at:
            self.start()
        worker = self.worker
        if worker is None:
            return None
        try:
            if worker.process.exitcode is not None:
                raise EOFError("Voice Isolator process exited")
            if not worker.ready:
                if not select.select([worker.connection], [], [], 0)[0]:
                    if time.monotonic() - worker.started >= _STARTUP_TIMEOUT:
                        raise TimeoutError("Voice Isolator startup timed out")
                    return None
                kind, data = _receive(worker.connection, time.monotonic() + _READY_REPLY_TIMEOUT)
                if kind != b"R":
                    raise ValueError("Invalid Voice Isolator startup response")
                self.device = data.decode()
                worker.ready = True
            return worker
        except (EOFError, OSError, ValueError) as error:
            self._discard(error)
            return None

    def _request(self, worker, kind, payload=b""):
        started = time.monotonic()
        # time.monotonic() ticks every ~15.6 ms on Windows, too coarse to time a single inference.
        measured_from = time.perf_counter()
        deadline = started + _INFERENCE_REPLY_TIMEOUT
        try:
            _send(worker.connection, kind, payload, deadline)
            response, data = _receive(worker.connection, deadline)
            if response != b"A" or len(data) < _GPU_MEMORY.size:
                raise ValueError("Invalid Voice Isolator response")
            self.gpu_memory = _GPU_MEMORY.unpack_from(data)[0] or None
            voice = np.frombuffer(data, dtype="<f4", offset=_GPU_MEMORY.size)
            if kind != b"F":
                previous_lag = 0 if kind == b"S" else worker.processing_lag
                samples_duration = len(payload) / (4 * SAMPLE_RATE)
                elapsed = time.perf_counter() - measured_from
                worker.processing_lag = max(0, previous_lag + elapsed - samples_duration)
                self._record_load(elapsed / samples_duration if samples_duration else None)
                if worker.processing_lag > _MAX_PROCESSING_LAG:
                    raise TimeoutError("Voice Isolator could not keep up with playback")
        except (EOFError, OSError, ValueError) as error:
            self._discard(error)
            raise IsolationUnavailable from error

        if voice.size:
            if self.mode != "isolated":
                self._set_mode("isolated")
                LOG.info("Voice Isolator is running on %s.", self.device)
            now = time.monotonic()
            if worker.healthy_since is None:
                worker.healthy_since = now
            elif now - worker.healthy_since >= 10:
                self.retry_delay = 0.5
        return voice

    def _record_load(self, ratio):
        if ratio is None:
            return
        previous = self.load_at is not None and time.monotonic() - self.load_at < _LOAD_STALE_AFTER
        self.load = ratio if not previous else self.load + _LOAD_SMOOTHING * (ratio - self.load)
        self.load_at = time.monotonic()

    @property
    def processing_load(self):
        """Inference time per second of audio, smoothed; None when no inference ran recently."""
        load_at = self.load_at
        if load_at is None or time.monotonic() - load_at >= _LOAD_STALE_AFTER:
            return None
        return self.load

    def close(self):
        if self.worker is not None:
            worker = self.worker
            self.worker = None
            worker.connection.close()
            self.retired.append(worker.process)
        for process in self.retired:
            process.join(_STOP_TIMEOUT)
            if process.exitcode is None:
                process.kill()
                process.join(_STOP_TIMEOUT)
            if process.exitcode is not None:
                process.close()
        self.retired.clear()


class _Stream:
    def __init__(self, isolator):
        self.isolator = isolator
        self.worker = None

    def push(self, samples):
        worker = self.isolator._ready_worker()
        if worker is None:
            raise IsolationUnavailable
        kind = b"P" if worker is self.worker else b"S"
        voice = self.isolator._request(worker, kind, samples.tobytes())
        self.worker = worker
        return voice

    def flush(self, *, as_numpy):
        if self.worker is None or self.worker is not self.isolator.worker:
            raise IsolationUnavailable
        return self.isolator._request(self.worker, b"F")
