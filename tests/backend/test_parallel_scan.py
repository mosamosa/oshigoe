import unittest

import torch

from src.backend.media.parallel_scan import (_compiled_kernel, _library_scan, fused_selective_scan,
                                             parallel_selective_scan)


def scan_inputs(batch, frames, *, channels=24, states=16, seed=0):
    generator = torch.Generator().manual_seed(seed)

    def random(*shape):
        return torch.randn(*shape, generator=generator)

    return dict(
        u=random(batch, frames, channels),
        delta=torch.nn.functional.softplus(random(batch, frames, channels)),
        a=-torch.exp(random(channels, states)),
        b=random(batch, frames, states),
        c=random(batch, frames, states),
        d=random(channels),
        z=random(batch, frames, channels),
        state=random(batch, channels, states),
    )


class LibraryEquivalence:
    device = "cpu"

    def scan(self, inputs):
        raise NotImplementedError

    def assert_matches_library(self, inputs):
        inputs = {name: None if value is None else value.to(self.device) for name, value in inputs.items()}
        expected_y, expected_state = _library_scan(**inputs)
        y, state = self.scan(inputs)
        torch.testing.assert_close(y, expected_y, rtol=1e-4, atol=1e-5)
        torch.testing.assert_close(state, expected_state, rtol=1e-4, atol=1e-5)

    def test_matches_library_across_band_scan_without_state(self):
        inputs = scan_inputs(4, 46)
        inputs["state"] = None
        self.assert_matches_library(inputs)

    def test_matches_library_when_carrying_state_across_frames(self):
        self.assert_matches_library(scan_inputs(46, 4, seed=1))

    def test_matches_library_without_gate_for_single_frame(self):
        inputs = scan_inputs(3, 1, seed=2)
        inputs["z"] = None
        self.assert_matches_library(inputs)


class ParallelScanTest(LibraryEquivalence, unittest.TestCase):
    def scan(self, inputs):
        return parallel_selective_scan(**inputs)


@unittest.skipUnless(torch.cuda.is_available(), "requires CUDA")
class FusedScanTest(LibraryEquivalence, unittest.TestCase):
    device = "cuda"

    def scan(self, inputs):
        kernel = _compiled_kernel()
        self.assertTrue(kernel, "NVRTC could not compile the selective scan")
        return fused_selective_scan(**inputs, kernel=kernel)


if __name__ == "__main__":
    unittest.main()
