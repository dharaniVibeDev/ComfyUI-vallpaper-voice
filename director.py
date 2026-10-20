import json
import os
import subprocess
import time
import uuid
import wave
from pathlib import Path

import folder_paths
import numpy as np
import torch
from comfy.model_management import throw_exception_if_processing_interrupted
from comfy.utils import ProgressBar

from .director_core import LANGUAGES, VOICES, append_line, assemble, contained_file, parse_choices, validate_script, write_json

RUNTIME = Path(os.environ.get("VALLPAPER_TTS_HOME", str(Path.home() / "Apps" / "VallpaperTTS"))).expanduser()


def read_audio(path):
    with wave.open(str(path), "rb") as audio:
        channels, width, rate = audio.getnchannels(), audio.getsampwidth(), audio.getframerate()
        if width != 2:
            raise ValueError("Director audio must be PCM16 WAV.")
        samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768
    waveform = torch.from_numpy(samples.reshape(-1, channels).T.copy()).unsqueeze(0)
    return {"waveform": waveform, "sample_rate": rate}


class VallpaperVoiceLine:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "text": ("STRING", {"multiline": True, "default": "Don't…"}),
            "delivery": ("STRING", {"multiline": True, "default": "A cinematic villain. Low, quiet, cold menace. Speak slowly and deliberately."}),
            "voice": (list(VOICES),), "language": (list(LANGUAGES),),
            "emotion_strength": ("FLOAT", {"default": 0.7, "min": 0, "max": 1, "step": 0.05}),
            "duration_factor": ("FLOAT", {"default": 1.3, "min": 0.5, "max": 2, "step": 0.05}),
            "pitch_semitones": ("FLOAT", {"default": 0, "min": -12, "max": 12, "step": 0.5}),
            "gain_db": ("FLOAT", {"default": 0, "min": -24, "max": 12, "step": 0.5}),
            "pause_after_ms": ("INT", {"default": 350, "min": 0, "max": 30000, "step": 25}),
        }, "optional": {"previous_lines": ("VALLPAPER_VOICE_SCRIPT",)}}

    RETURN_TYPES = ("VALLPAPER_VOICE_SCRIPT",)
    RETURN_NAMES = ("script",)
    FUNCTION = "build"
    CATEGORY = "vallpaper.store/audio"
    DESCRIPTION = "Append a directed line. Chain script into the next line's previous_lines. Larger duration factors speak more slowly. Pitch and gain are audio effects applied after synthesis."

    def build(self, text, delivery, voice, language, emotion_strength, duration_factor,
              pitch_semitones, gain_db, pause_after_ms, previous_lines=None):
        return (append_line(previous_lines, text, voice, language, delivery, emotion_strength,
                            duration_factor, pitch_semitones, gain_db, pause_after_ms),)


class VallpaperVoiceDirector:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"script": ("VALLPAPER_VOICE_SCRIPT",),
            "takes_per_line": ("INT", {"default": 2, "min": 1, "max": 8}),
            "seed": ("INT", {"default": 42, "min": 0, "max": 2147483647}),
        }}

    RETURN_TYPES = ("AUDIO", "VALLPAPER_VOICE_TAKES", "STRING")
    RETURN_NAMES = ("first_takes_joined", "takes", "session_folder")
    FUNCTION = "generate"
    CATEGORY = "vallpaper.store/audio"
    DESCRIPTION = "Generate alternate takes for every Voice Line using the isolated IndexTTS runtime. Models load once per session and the worker exits afterward. Connect takes to Voice Select to choose without regenerating."

    def generate(self, script, takes_per_line, seed):
        script = validate_script(script)
        if int(takes_per_line) < 1:
            raise ValueError("Generate at least one take per line.")
        python = RUNTIME / ".venv" / "Scripts" / "python.exe"
        if not python.is_file():
            raise RuntimeError(f"The isolated TTS runtime is missing: {python}")
        for line in script["lines"]:
            if not (RUNTIME / "voices" / VOICES[line["voice"]]).is_file():
                raise FileNotFoundError(f"Built-in voice is missing: {line['voice']}")
        root = Path(folder_paths.get_output_directory()) / "vallpaper_voice_director"
        session = root / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        session.mkdir(parents=True)
        request = dict(runtime=str(RUNTIME), session=str(session), script=script,
                       takes_per_line=int(takes_per_line), seed=int(seed))
        request_file = session / "request.json"
        write_json(request_file, request)
        total = len(script["lines"]) * takes_per_line
        progress = ProgressBar(total)
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with (session / "generation.log").open("w", encoding="utf-8") as log:
            worker = subprocess.Popen([str(python), "-u", str(Path(__file__).with_name("director_worker.py")), str(request_file)],
                                      cwd=RUNTIME, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
            try:
                while worker.poll() is None:
                    throw_exception_if_processing_interrupted()
                    state_file = session / "progress.json"
                    if state_file.is_file():
                        state = json.loads(state_file.read_text(encoding="utf-8"))
                        progress.update_absolute(state["completed"], total)
                    time.sleep(0.3)
            finally:
                if worker.poll() is None:
                    worker.terminate()
                    try:
                        worker.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        worker.kill()
                        worker.wait()
        if worker.returncode:
            detail = (session / "generation.log").read_text(encoding="utf-8", errors="replace")[-1800:]
            raise RuntimeError(f"Voice Director failed. Saved takes remain in {session}.\n{detail}")
        manifest = session / "takes.json"
        state = json.loads(manifest.read_text(encoding="utf-8"))
        if not state["complete"]:
            raise RuntimeError(f"Voice Director session did not finish: {session}")
        progress.update_absolute(total, total)
        return read_audio(session / "joined.wav"), {"manifest": str(manifest)}, str(session)


class VallpaperVoiceSelect:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"takes": ("VALLPAPER_VOICE_TAKES",),
            "choices": ("STRING", {"default": "1", "tooltip": "One take for all lines: 2. Different takes per line: 1,2,1. Blank selects take 1."}),
        }}

    RETURN_TYPES = ("AUDIO", "STRING", "STRING")
    RETURN_NAMES = ("audio", "saved_wav", "timeline_json")
    FUNCTION = "select"
    CATEGORY = "vallpaper.store/audio"
    DESCRIPTION = "Choose a numbered take for each line and join them with their pauses. No model inference. Keep the Director seed fixed so editing choices reuses cached takes."

    def select(self, takes, choices):
        allowed = (Path(folder_paths.get_output_directory()) / "vallpaper_voice_director").resolve()
        manifest_file = contained_file(allowed, takes["manifest"])
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        if not manifest.get("complete"):
            raise ValueError("This session is unfinished. Generate a complete director session first.")
        selected = parse_choices(choices, [len(line["takes"]) for line in manifest["lines"]])
        session = manifest_file.parent
        stem = "selected-" + uuid.uuid4().hex[:8]
        output = session / (stem + ".wav")
        timeline_file = session / (stem + ".json")
        timeline = assemble(manifest, session, selected, output)
        write_json(timeline_file, {"source_manifest": str(manifest_file), "choices": selected,
                                  "audio": str(output), "timeline": timeline})
        return read_audio(output), str(output), str(timeline_file)


class VallpaperVoiceSession:
    @classmethod
    def INPUT_TYPES(cls):
        root = Path(folder_paths.get_output_directory()) / "vallpaper_voice_director"
        sessions = sorted((path.parent.name for path in root.glob("*/takes.json")), reverse=True)
        return {"required": {"session": (sessions or ["No saved sessions yet"],)}}

    RETURN_TYPES = ("VALLPAPER_VOICE_TAKES", "STRING")
    RETURN_NAMES = ("takes", "summary")
    FUNCTION = "load"
    CATEGORY = "vallpaper.store/audio"
    DESCRIPTION = "Reopen a finished saved session without loading a speech model. Connect takes to Voice Select. Refresh the browser to update the session list."

    def load(self, session):
        root = (Path(folder_paths.get_output_directory()) / "vallpaper_voice_director").resolve()
        sessions = {path.parent.name: path for path in root.glob("*/takes.json")}
        if session not in sessions:
            raise ValueError("Choose an existing director session. Refresh ComfyUI to update the list.")
        path = contained_file(root, sessions[session])
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if not manifest.get("complete"):
            raise ValueError("This session did not finish. Its completed individual WAVs remain in its folder.")
        summary = "\n".join(f"Line {line['line']}: {len(line['takes'])} takes — {line['settings']['text']}"
                            for line in manifest["lines"])
        return {"manifest": str(path)}, summary


DIRECTOR_NODE_CLASSES = {"VallpaperVoiceLine": VallpaperVoiceLine,
                         "VallpaperVoiceDirector": VallpaperVoiceDirector,
                         "VallpaperVoiceSelect": VallpaperVoiceSelect,
                         "VallpaperVoiceSession": VallpaperVoiceSession}
DIRECTOR_NODE_NAMES = {"VallpaperVoiceLine": "vallpaper.store · Voice Line",
                       "VallpaperVoiceDirector": "vallpaper.store · Voice Director",
                       "VallpaperVoiceSelect": "vallpaper.store · Voice Select",
                       "VallpaperVoiceSession": "vallpaper.store · Voice Session"}
