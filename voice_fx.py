import json

import torch

from .voice_fx_core import PARAMETERS, PRESETS, process_audio, settings_for


class VallpaperVoiceFX:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "audio": ("AUDIO",),
            "preset": (list(PRESETS), {"default": "Small room"}),
            "reverb_amount": ("FLOAT", {"default": 1, "min": 0, "max": 3, "step": .05,
                                        "tooltip": "Scales the preset reverb. 0 removes it."}),
            "bass_adjust_db": ("FLOAT", {"default": 0, "min": -18, "max": 18, "step": .5,
                                         "tooltip": "Adds bass boost/cut to any preset."}),
            "custom_controls": ("BOOLEAN", {"default": False,
                "tooltip": "Enable to use the controls below instead of the preset values. Custom preset also enables them."}),
            "wet": ("FLOAT", {"default": .2, "min": 0, "max": 1, "step": .01}),
            "decay": ("FLOAT", {"default": .6, "min": .05, "max": 10, "step": .05}),
            "predelay_ms": ("FLOAT", {"default": 12, "min": 0, "max": 250, "step": 1}),
            "damping_hz": ("FLOAT", {"default": 5500, "min": 100, "max": 16000, "step": 100}),
            "room_size": ("FLOAT", {"default": 1, "min": .1, "max": 3, "step": .05}),
            "robot_mix": ("FLOAT", {"default": 0, "min": 0, "max": 1, "step": .05}),
            "robot_hz": ("FLOAT", {"default": 70, "min": 10, "max": 500, "step": 5}),
            "bass_db": ("FLOAT", {"default": 0, "min": -18, "max": 18, "step": .5}),
            "echo_ms": ("FLOAT", {"default": 0, "min": 0, "max": 1000, "step": 5}),
            "echo_mix": ("FLOAT", {"default": 0, "min": 0, "max": 1, "step": .05}),
            "normalize_peaks": ("BOOLEAN", {"default": True}),
        }}

    RETURN_TYPES = ("AUDIO", "STRING")
    RETURN_NAMES = ("audio", "effect_profile_json")
    FUNCTION = "apply"
    CATEGORY = "vallpaper.store/audio"
    DESCRIPTION = "CPU reverb, bass shelf, metallic robot modulation and echo. Works with any AUDIO input. Custom controls replace preset values when enabled. Delivery and acting still belong in Voice Line, before generation."

    def apply(self, audio, preset, reverb_amount, bass_adjust_db, custom_controls, normalize_peaks, **controls):
        settings = settings_for(preset, custom_controls, controls, reverb_amount, bass_adjust_db)
        rate = int(audio["sample_rate"])
        samples = audio["waveform"].detach().cpu().float().numpy()
        output = process_audio(samples, rate, settings, normalize_peaks)
        profile = dict(preset=preset, settings=settings, normalize_peaks=normalize_peaks,
                       sample_rate=rate, processing="CPU algorithmic effects; no voice model")
        return {"waveform": torch.from_numpy(output), "sample_rate": rate}, json.dumps(profile, indent=2)


FX_NODE_CLASSES = {"VallpaperVoiceFX": VallpaperVoiceFX}
FX_NODE_NAMES = {"VallpaperVoiceFX": "vallpaper.store · Voice FX"}
