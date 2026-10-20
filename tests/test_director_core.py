import json
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from director_core import append_line, assemble, contained_file, parse_choices, take_seed, validate_script, write_json


class DirectorCoreTests(unittest.TestCase):
    def line(self, previous=None, text="Hello", pause=100):
        return append_line(previous, text, "Windows-neutral", "EN", "Calm", .7, 1.3, -2, -3, pause)

    def test_chaining_preserves_previous_script(self):
        first = self.line()
        second = self.line(first, "Goodbye")
        self.assertEqual([line["text"] for line in first["lines"]], ["Hello"])
        self.assertEqual([line["text"] for line in second["lines"]], ["Hello", "Goodbye"])

    def test_unknown_voice_cannot_be_used_as_path(self):
        with self.assertRaises(ValueError):
            append_line(None, "Hi", "../../secret", "EN", "", .5, 1, 0, 0, 0)

    def test_nonfinite_control_rejected(self):
        with self.assertRaises(ValueError):
            append_line(None, "Hi", "Windows-neutral", "EN", "", .5, 1, float("nan"), 0, 0)

    def test_empty_script_rejected(self):
        with self.assertRaises(ValueError):
            validate_script({"version": 1, "lines": []})

    def test_choices_default_broadcast_and_per_line(self):
        self.assertEqual(parse_choices("", [2, 2]), [1, 1])
        self.assertEqual(parse_choices("2", [2, 2]), [2, 2])
        self.assertEqual(parse_choices("1, 2,1", [2, 2, 2]), [1, 2, 1])

    def test_invalid_choices(self):
        for choices in ["3", "0", "x", "1,2,1", "1,"]:
            with self.subTest(choices=choices), self.assertRaises(ValueError):
                parse_choices(choices, [2, 2])

    def test_alternate_seeds_are_distinct_and_reproducible(self):
        seeds = [take_seed(42, line, take) for line in range(10) for take in range(8)]
        self.assertEqual(len(seeds), len(set(seeds)))
        self.assertEqual(seeds, [take_seed(42, line, take) for line in range(10) for take in range(8)])
        self.assertLess(take_seed(2147483647, 4, 3), 2147483648)

    def write_wav(self, path, samples, rate=1000):
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(struct.pack("<" + "h" * len(samples), *samples))

    def test_join_uses_selected_take_and_exact_silence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_wav(root / "a.wav", [100, 200])
            self.write_wav(root / "b.wav", [300, 400, 500])
            manifest = {"sample_rate": 1000, "lines": [
                {"settings": {"text": "a", "pause_after_ms": 2}, "takes": [{"take": 1, "file": "a.wav"}, {"take": 2, "file": "b.wav"}]},
                {"settings": {"text": "b", "pause_after_ms": 0}, "takes": [{"take": 1, "file": "a.wav"}]}]}
            timeline = assemble(manifest, root, [2, 1], root / "joined.wav")
            with wave.open(str(root / "joined.wav"), "rb") as audio:
                self.assertEqual(audio.getnframes(), 7)
                self.assertEqual(struct.unpack("<7h", audio.readframes(7)), (300, 400, 500, 0, 0, 100, 200))
            self.assertEqual(timeline[0]["end_seconds"], .003)
            self.assertEqual(timeline[1]["start_seconds"], .005)

    def test_audio_sample_rate_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_wav(root / "a.wav", [1, 2], rate=2000)
            manifest = {"sample_rate": 1000, "lines": [{"settings": {"text": "a", "pause_after_ms": 0}, "takes": [{"take": 1, "file": "a.wav"}]}]}
            with self.assertRaisesRegex(ValueError, "1000 Hz"):
                assemble(manifest, root, [1], root / "joined.wav")

    def test_take_path_cannot_escape_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                contained_file(root, "../outside.wav")

    def test_script_settings_survive_json_roundtrip(self):
        script = self.line(text="Don't… you know English?!")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "request.json"
            write_json(path, script)
            restored = validate_script(json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual(restored, script)
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
