"""Script and PCM helpers. Importing this module never starts inference."""
import json
import math
import wave
from pathlib import Path

VOICES = {"Windows-neutral": "Windows-neutral.wav", "Windows-Zira": "Windows-Zira.wav"}
LANGUAGES = ("EN", "ZH", "JA", "ES", "AR")


def append_line(script, text, voice, language, delivery, strength, duration, pitch, gain, pause):
    if not text.strip():
        raise ValueError("Voice Line: enter dialogue for this line.")
    if voice not in VOICES or language not in LANGUAGES:
        raise ValueError("Voice Line: choose a supported voice and language.")
    if not all(math.isfinite(float(v)) for v in [strength, duration, pitch, gain, pause]):
        raise ValueError("Voice Line: controls must contain finite numbers.")
    if not 0 <= strength <= 1 or not 0.5 <= duration <= 2 or pause < 0:
        raise ValueError("Voice Line: strength must be 0–1, duration 0.5–2, and pause nonnegative.")
    lines = [] if script is None else list(script["lines"])
    lines.append(dict(text=text.strip(), voice=voice, language=language, delivery=delivery.strip(),
                      strength=float(strength), duration_factor=float(duration), pitch_semitones=float(pitch),
                      gain_db=float(gain), pause_after_ms=int(pause)))
    return {"version": 1, "lines": lines}


def validate_script(script):
    if script.get("version") != 1 or not script.get("lines"):
        raise ValueError("Connect at least one Voice Line to Voice Director.")
    checked = None
    for line in script["lines"]:
        checked = append_line(checked, line["text"], line["voice"], line["language"], line["delivery"],
                              line["strength"], line["duration_factor"], line["pitch_semitones"],
                              line["gain_db"], line["pause_after_ms"])
    return checked


def take_seed(seed, line_index, take_index):
    return (int(seed) + line_index * 1009 + take_index) % 2147483648


def parse_choices(text, counts):
    parts = [part.strip() for part in text.split(",")]
    if len(parts) == 1 and not parts[0]:
        return [1] * len(counts)
    if len(parts) == 1:
        parts *= len(counts)
    if len(parts) != len(counts):
        raise ValueError(f"Expected {len(counts)} comma-separated take numbers, or one number for all lines.")
    try:
        chosen = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError("Take choices must be numbers, for example: 1,2,1.") from exc
    for index, (take, count) in enumerate(zip(chosen, counts), 1):
        if not 1 <= take <= count:
            raise ValueError(f"Line {index} has takes 1–{count}; take {take} does not exist.")
    return chosen


def contained_file(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Audio take must stay inside its director session folder.")
    if not path.is_file():
        raise FileNotFoundError(f"Audio take is missing: {path}")
    return path


def assemble(manifest, root, choices, output):
    """Stream mono PCM16 takes into a WAV, preserving sample-exact pauses."""
    lines = manifest["lines"]
    if len(choices) != len(lines):
        raise ValueError("Choose one take for each script line.")
    rate = int(manifest["sample_rate"])
    position = 0
    timeline = []
    with wave.open(str(output), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        for index, (line, chosen) in enumerate(zip(lines, choices), 1):
            take = next((item for item in line["takes"] if item["take"] == chosen), None)
            if take is None:
                raise ValueError(f"Line {index}: take {chosen} does not exist.")
            source = contained_file(root, take["file"])
            with wave.open(str(source), "rb") as audio:
                if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, rate):
                    raise ValueError(f"Line {index}: take must be mono PCM16 at {rate} Hz.")
                frames = audio.getnframes()
                block = audio.readframes(65536)
                while block:
                    target.writeframesraw(block)
                    block = audio.readframes(65536)
            pause_frames = round(rate * line["settings"]["pause_after_ms"] / 1000)
            timeline.append(dict(line=index, take=chosen, text=line["settings"]["text"],
                                 start_seconds=position / rate, end_seconds=(position + frames) / rate,
                                 pause_after_ms=line["settings"]["pause_after_ms"]))
            silence_left = pause_frames
            while silence_left:
                block_frames = min(silence_left, 65536)
                target.writeframesraw(b"\x00\x00" * block_frames)
                silence_left -= block_frames
            position += frames + pause_frames
    return timeline


def write_json(path, value):
    path = Path(path)
    pending = path.with_suffix(path.suffix + ".tmp")
    pending.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    pending.replace(path)
