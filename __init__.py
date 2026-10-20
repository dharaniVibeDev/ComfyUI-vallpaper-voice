"""ComfyUI bridge. Dependencies and model execution stay in the isolated worker."""
import json
import os
import subprocess
import time
import uuid
import wave
from pathlib import Path

import numpy as np
import torch
import folder_paths

ROOT = Path(os.environ.get("VALLPAPER_TTS_HOME", str(Path.home() / "Apps" / "VallpaperTTS"))).expanduser()
VOICES = {"Windows-neutral": "Windows-neutral.wav", "Windows-Zira": "Windows-Zira.wav"}


class VallpaperVoice:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "text": ("STRING", {"multiline": True, "default": "Stay behind me. We are going to make it home."}),
            "voice": (["Windows-neutral", "Windows-Zira"],),
            "language": (["EN", "ZH", "JA", "ES", "AR"],),
            "emotion_mode": (["Emotion description", "Emotion sliders", "Voice reference emotion"],),
            "emotion_description": ("STRING", {"multiline": True, "default": "Afraid but determined, speaking softly with urgency."}),
            "emotion_strength": ("FLOAT", {"default": 0.7, "min": 0, "max": 1, "step": 0.05}),
            "duration_factor": ("FLOAT", {"default": 1, "min": 0.5, "max": 2, "step": 0.05}),
            "seed": ("INT", {"default": 42, "min": 0, "max": 2147483647}),
            **{name: ("FLOAT", {"default": 0.3 if name == "calm" else 0, "min": 0, "max": 1, "step": 0.05})
               for name in ["happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm"]},
        }, "optional": {"reference_audio": ("AUDIO",)}}

    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("audio", "saved_path")
    FUNCTION = "generate"
    CATEGORY = "vallpaper.store/audio"
    DESCRIPTION = "IndexTTS 2.5 in an isolated runtime. No recording required. Set up in Apps/VallpaperTTS."

    def generate(self, text, voice, language, emotion_mode, emotion_description,
                 emotion_strength, duration_factor, seed, reference_audio=None, **emotions):
        if not text.strip():
            raise ValueError("Enter dialogue to generate.")
        python = ROOT / ".venv" / "Scripts" / "python.exe"
        if not python.is_file():
            raise RuntimeError(f"TTS runtime missing: {python}")
        output_dir = Path(folder_paths.get_output_directory()) / "vallpaper_voice"
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = output_dir / ("voice-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
        if voice not in VOICES:
            raise ValueError("Choose a listed built-in voice.")
        voice_path = ROOT / "voices" / VOICES[voice]
        if reference_audio is not None:
            waveform = reference_audio["waveform"][0].detach().cpu().float()
            pcm = (waveform.clamp(-1, 1).T.numpy() * 32767).astype("<i2")
            voice_path = stem.with_suffix(".reference.wav")
            with wave.open(str(voice_path), "wb") as wav:
                wav.setnchannels(pcm.shape[1])
                wav.setsampwidth(2)
                wav.setframerate(int(reference_audio["sample_rate"]))
                wav.writeframes(pcm.tobytes())
        output = stem.with_suffix(".wav")
        request = dict(text=text, voice=str(voice_path), output=str(output), language=language,
                       mode=emotion_mode, emotion_text=emotion_description, strength=emotion_strength,
                       duration_factor=duration_factor, seed=seed,
                       vector=[emotions[n] for n in ["happy", "angry", "sad", "afraid", "disgusted", "melancholic", "surprised", "calm"]])
        request_path = stem.with_suffix(".json")
        request_path.write_text(json.dumps(request, indent=2, ensure_ascii=False), encoding="utf-8")
        log_path = stem.with_suffix(".log")
        from comfy.model_management import throw_exception_if_processing_interrupted

        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen([str(python), "-u", str(Path(__file__).with_name("voice_worker.py")), str(request_path)],
                                       cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                while process.poll() is None:
                    throw_exception_if_processing_interrupted()
                    time.sleep(0.3)
            except BaseException:
                process.terminate()
                process.wait(timeout=30)
                raise
        if process.returncode or not output.is_file():
            raise RuntimeError(f"TTS failed. {log_path}\n" + log_path.read_text(encoding="utf-8", errors="replace")[-1600:])
        with wave.open(str(output), "rb") as wav:
            sr, channels = wav.getframerate(), wav.getnchannels()
            data = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").astype(np.float32) / 32768
        tensor = torch.from_numpy(data.reshape(-1, channels).T.copy()).unsqueeze(0)
        return {"waveform": tensor, "sample_rate": sr}, str(output)


NODE_CLASS_MAPPINGS = {"VallpaperVoice": VallpaperVoice}
NODE_DISPLAY_NAME_MAPPINGS = {"VallpaperVoice": "vallpaper.store · Voice"}

from .director import DIRECTOR_NODE_CLASSES, DIRECTOR_NODE_NAMES
from .voice_fx import FX_NODE_CLASSES, FX_NODE_NAMES

NODE_CLASS_MAPPINGS.update(DIRECTOR_NODE_CLASSES)
NODE_DISPLAY_NAME_MAPPINGS.update(DIRECTOR_NODE_NAMES)
NODE_CLASS_MAPPINGS.update(FX_NODE_CLASSES)
NODE_DISPLAY_NAME_MAPPINGS.update(FX_NODE_NAMES)
