import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from voice_fx_core import PARAMETERS, process_audio, settings_for


class VoiceFXTests(unittest.TestCase):
    def test_dry_preserves_waveform_and_channels(self):
        audio = np.random.default_rng(42).uniform(-.2, .2, (2, 2, 4000)).astype(np.float32)
        result = process_audio(audio, 8000, settings_for("Dry", False, {}), False)
        np.testing.assert_array_equal(result, audio)

    def test_reverb_preserves_dry_onset_and_adds_tail(self):
        audio = np.zeros((1, 1, 800), dtype=np.float32)
        audio[0, 0, 0] = 1
        result = process_audio(audio, 8000, settings_for("Small room", False, {}), False)
        self.assertAlmostEqual(result[0, 0, 0], .82, places=5)
        self.assertGreater(result.shape[-1], audio.shape[-1])
        self.assertGreater(float(np.max(np.abs(result[..., 800:]))), 0)
        self.assertTrue(np.isfinite(result).all())

    def test_outside_echo_is_delayed(self):
        audio = np.zeros((1, 1, 1000))
        audio[..., 0] = .5
        result = process_audio(audio, 1000, settings_for("Outside", False, {}), False)
        self.assertAlmostEqual(result[0, 0, 95], .035, places=6)
        self.assertEqual(result.shape[-1], 1095)

    def test_bass_boosts_low_frequency(self):
        rate = 8000
        audio = (.1 * np.sin(2 * np.pi * 50 * np.arange(rate) / rate))[None, None, :]
        result = process_audio(audio, rate, settings_for("Bass", False, {}), False)
        self.assertGreater(np.std(result[..., rate//2:]), np.std(audio[..., rate//2:]) * 1.7)

    def test_custom_settings_and_combined_robot_bass(self):
        custom = dict(PARAMETERS, wet=.8, bass_db=4)
        result = settings_for("Hall", True, custom, reverb_amount=.5, bass_adjust_db=2)
        self.assertEqual(result["wet"], .4)
        self.assertEqual(result["bass_db"], 6)
        combined = settings_for("Robot + bass", False, {})
        self.assertGreater(combined["robot_mix"], 0)
        self.assertGreater(combined["bass_db"], 0)

    def test_peak_normalization_prevents_clipping(self):
        audio = np.ones((1, 1, 1000), dtype=np.float32) * 3
        result = process_audio(audio, 8000, settings_for("Dry", False, {}), True)
        self.assertLessEqual(float(np.max(np.abs(result))), .980001)


if __name__ == "__main__":
    unittest.main()
