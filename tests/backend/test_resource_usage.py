import subprocess
import sys
import time
import unittest
from types import SimpleNamespace

from src.backend.resource_usage import ResourceMonitor, app_sm_utilization, dedicated_memory, ioreg_utilization


def nvml_sample(pid, sm, age_s, now_us=100_000_000):
    return SimpleNamespace(pid=pid, smUtil=sm, timeStamp=now_us - int(age_s * 1_000_000))


class ResourceUsageTest(unittest.TestCase):
    def test_sums_the_latest_recent_gpu_sample_of_each_app_process(self):
        samples = [
            nvml_sample(100, 9, age_s=3),
            nvml_sample(100, 4, age_s=1),
            nvml_sample(200, 2, age_s=0.5),
            nvml_sample(300, 50, age_s=0.1),
            nvml_sample(400, 30, age_s=12),
        ]
        self.assertEqual(app_sm_utilization(samples, {100, 200, 400}, now_us=100_000_000), 6)
    def test_counts_gpu_memory_of_app_processes_only(self):
        instances = [
            ("pid_100_luid_0x00000000_0x0000D5E1_phys_0", 1_000),
            ("pid_100_luid_0x00000000_0x0000D5E1_phys_1", 24),
            ("pid_200_luid_0x00000000_0x0000D5E1_phys_0", 2_000),
            ("pid_300_luid_0x00000000_0x0000D5E1_phys_0", 4_000),
            (None, 8_000),
        ]
        self.assertEqual(dedicated_memory(instances, {100, 300}), 5_024)

    def test_reads_gpu_utilization_from_ioreg(self):
        output = '''
        "PerformanceStatistics" = {"In use system memory"=123456,"Device Utilization %"=37,"Renderer Utilization %"=30}
        '''
        self.assertEqual(ioreg_utilization(output), 37)
        self.assertIsNone(ioreg_utilization('"PerformanceStatistics" = {}'))

    def test_includes_child_processes_in_app_cpu_and_memory(self):
        monitor = ResourceMonitor(None)
        child = subprocess.Popen([sys.executable, "-c", "while True: pass"])
        self.addCleanup(child.kill)
        deadline = time.monotonic() + 10
        usage = monitor.sample()
        while usage["cpu_percent"]["value"] == 0 and time.monotonic() < deadline:
            time.sleep(0.5)
            usage = monitor.sample()
        self.assertIn(child.pid, monitor.tree.processes)
        self.assertEqual(usage["cpu_percent"]["scope"], "app")
        self.assertGreater(usage["cpu_percent"]["value"], 0)
        self.assertGreater(usage["memory_bytes"]["value"], 0)

    def test_falls_back_to_gpu_memory_reported_by_the_worker(self):
        monitor = ResourceMonitor(SimpleNamespace(gpu_memory=512, processing_load=0.25))
        monitor.gpu_memory = monitor.gpu_utilization = None
        usage = monitor.sample()
        self.assertEqual(usage["gpu_memory_bytes"], {"value": 512, "scope": "app"})
        self.assertEqual(usage["isolation_load_percent"], {"value": 25.0, "scope": "app"})
        self.assertIsNone(usage["gpu_percent"])


if __name__ == "__main__":
    unittest.main()
