"""Resource usage of the app's processes, or of the whole machine where only that is available.

Each metric is reported as {"value": number, "scope": "app" | "pc" | "mac"}, or None when unavailable.
The app's processes are the server and its descendants: the Voice Isolator worker and FFmpeg.
"""

import ctypes
import logging
import os
import re
import subprocess
import sys
import time

import psutil

LOG = logging.getLogger(__name__)
_PID = re.compile(r"pid_(\d+)_")
_IOREG_UTILIZATION = re.compile(r'"Device Utilization %"\s*=\s*(\d+)')


def usage(value, scope):
    return None if value is None else {"value": value, "scope": scope}


class _ProcessTree:
    def __init__(self):
        self.root = psutil.Process(os.getpid())
        self.processes = {}

    def current(self):
        try:
            found = [self.root, *self.root.children(recursive=True)]
        except psutil.Error:
            found = [self.root]
        self.processes = {process.pid: self.processes.get(process.pid, process) for process in found}
        return list(self.processes.values())

    def sample(self):
        cpu = memory = 0.0
        for process in self.current():
            try:
                with process.oneshot():
                    cpu += process.cpu_percent()
                    memory += process.memory_info().rss
            except psutil.Error:
                continue
        return cpu / (psutil.cpu_count() or 1), memory


class _NvmlUtilization:
    """SM utilization of the app's processes from NVML, or of the whole GPU as a fallback."""

    def __init__(self):
        import pynvml

        self.nvml = pynvml
        pynvml.nvmlInit()
        self.devices = [pynvml.nvmlDeviceGetHandleByIndex(index) for index in range(pynvml.nvmlDeviceGetCount())]

    def sample(self, pids):
        try:
            total = 0.0
            now_us = time.time_ns() // 1000
            for device in self.devices:
                try:
                    samples = self.nvml.nvmlDeviceGetProcessUtilization(device, 0)
                except self.nvml.NVMLError_NotFound:
                    continue
                total += app_sm_utilization(samples, pids, now_us=now_us)
            return usage(min(total, 100.0), "app")
        except self.nvml.NVMLError:
            pass
        try:
            rates = [self.nvml.nvmlDeviceGetUtilizationRates(device).gpu for device in self.devices]
            return usage(max(rates, default=0), "pc")
        except self.nvml.NVMLError:
            return None


def app_sm_utilization(samples, pids, *, now_us, max_age_us=10_000_000):
    """Sum the latest SM utilization of each app process, skipping processes that stopped using the GPU.

    NVML keeps the last sample of every process, so filtering by a shared "last seen" time would
    drop a process whose latest sample is older than another process's.
    """
    latest = {}
    for sample in samples:
        if sample.pid in pids and now_us - sample.timeStamp <= max_age_us:
            if sample.pid not in latest or sample.timeStamp > latest[sample.pid].timeStamp:
                latest[sample.pid] = sample
    return sum(sample.smUtil for sample in latest.values())


class _PdhCounterValue(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_ulong), ("largeValue", ctypes.c_longlong)]


class _PdhCounterItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _PdhCounterValue)]


class _WindowsGpuMemory:
    """Dedicated GPU memory per process from the "GPU Process Memory" performance counter."""

    _FMT_LARGE = 0x400
    _MORE_DATA = 0x800007D2

    def __init__(self):
        self.pdh = ctypes.WinDLL("pdh")
        self.query = ctypes.c_void_p()
        self.counter = ctypes.c_void_p()
        self._check(self.pdh.PdhOpenQueryW(None, None, ctypes.byref(self.query)))
        self._check(self.pdh.PdhAddEnglishCounterW(self.query, r"\GPU Process Memory(*)\Dedicated Usage",
                                                   None, ctypes.byref(self.counter)))

    @staticmethod
    def _check(status):
        if status & 0xFFFFFFFF:
            raise OSError(f"PDH error 0x{status & 0xFFFFFFFF:08X}")

    def sample(self, pids):
        self._check(self.pdh.PdhCollectQueryData(self.query))
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        status = self.pdh.PdhGetFormattedCounterArrayW(self.counter, self._FMT_LARGE, ctypes.byref(size),
                                                       ctypes.byref(count), None)
        if status & 0xFFFFFFFF != self._MORE_DATA:
            self._check(status)
            return usage(0, "app")
        buffer = ctypes.create_string_buffer(size.value)
        self._check(self.pdh.PdhGetFormattedCounterArrayW(self.counter, self._FMT_LARGE, ctypes.byref(size),
                                                          ctypes.byref(count), buffer))
        items = ctypes.cast(buffer, ctypes.POINTER(_PdhCounterItem * count.value)).contents
        return usage(dedicated_memory(((item.szName, item.FmtValue.largeValue) for item in items), pids), "app")


def dedicated_memory(instances, pids):
    total = 0
    for name, value in instances:
        match = _PID.match(name or "")
        if match and int(match.group(1)) in pids:
            total += value
    return total


def ioreg_utilization(output):
    values = [int(value) for value in _IOREG_UTILIZATION.findall(output)]
    return max(values) if values else None


class _MacGpuUtilization:
    def sample(self, pids):
        output = subprocess.run(["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
                                capture_output=True, text=True, timeout=5, check=True).stdout
        return usage(ioreg_utilization(output), "mac")


def _optional(factory, name):
    try:
        return factory()
    except Exception as error:  # Each probe depends on the OS, driver and GPU; a missing one only hides its row.
        LOG.info("%s is unavailable (%s)", name, error)
        return None


class ResourceMonitor:
    def __init__(self, isolator):
        self.isolator = isolator
        self.tree = _ProcessTree()
        if sys.platform == "win32":
            self.gpu_utilization = _optional(_NvmlUtilization, "GPU utilization")
            self.gpu_memory = _optional(_WindowsGpuMemory, "GPU memory usage")
        elif sys.platform == "darwin":
            self.gpu_utilization = _MacGpuUtilization()
            self.gpu_memory = None
        else:
            self.gpu_utilization = self.gpu_memory = None

    def sample(self):
        cpu, memory = self.tree.sample()
        pids = set(self.tree.processes)
        load = getattr(self.isolator, "processing_load", None)
        return {
            "isolation_load_percent": usage(None if load is None else load * 100, "app"),
            "cpu_percent": usage(cpu, "app"),
            "memory_bytes": usage(memory, "app"),
            "gpu_percent": self._probe(self.gpu_utilization, pids),
            "gpu_memory_bytes": (self._probe(self.gpu_memory, pids) if self.gpu_memory
                                 else usage(getattr(self.isolator, "gpu_memory", None), "app")),
        }

    @staticmethod
    def _probe(probe, pids):
        if probe is None:
            return None
        try:
            return probe.sample(pids)
        except Exception:  # A failed sample hides the row until the next one succeeds.
            LOG.debug("Resource sample failed", exc_info=True)
            return None
