"""Executed only by an explicitly queued Voice Director generation."""
import hashlib
import json
import os
import sys
from pathlib import Path

from director_core import VOICES, assemble, take_seed, validate_script, write_json


def generate(request):
    runtime = Path(request["runtime"]).resolve()
    sys.path.insert(0, str(runtime))
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    from filelock import FileLock
    import librosa
    import numpy as np
    import soundfile as sf
    import torch
    from indextts.infer_v2_5 import IndexTTS2

    root = Path(request["session"]).resolve()
    script = validate_script(request["script"])
    takes_per_line = int(request["takes_per_line"])
    if takes_per_line < 1:
        raise ValueError("Generate at least one take per line.")
    total = len(script["lines"]) * takes_per_line
    manifest = {"version": 1, "sample_rate": 22050, "seed": request["seed"], "lines": [], "complete": False}
    torch.set_num_threads(4)
    with FileLock(str(runtime / "generation.lock"), timeout=1):
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable in the isolated TTS runtime.")
        free, _ = torch.cuda.mem_get_info()
        if free < 5.5 * 1024**3:
            raise RuntimeError("Free at least 5.5 GB VRAM before generating. Use Free VRAM while ComfyUI is idle.")
        models = runtime / "models" / "IndexTTS-2.5"
        write_json(root / "progress.json", {"completed": 0, "total": total, "message": "Loading speech models"})
        model = IndexTTS2(cfg_path=str(models / "config.yaml"), model_dir=str(models), device="cuda:0",
                          reference_device="cpu", use_bf16=True, use_cuda_kernel=False,
                          use_deepspeed=False, use_torch_compile=False,
                          reference_cache_dir=str(runtime / "reference_cache"))
        model.low_vram = True
        if any(line["delivery"] for line in script["lines"]):
            model.ensure_qwen_emotion()
        completed = 0
        for index, line in enumerate(script["lines"]):
            voice = runtime / "voices" / VOICES[line["voice"]]
            line_record = {"line": index + 1, "settings": line, "takes": []}
            manifest["lines"].append(line_record)
            for take_index in range(takes_per_line):
                filename = f"line-{index + 1:03d}-take-{take_index + 1:02d}.wav"
                output = root / filename
                seed = take_seed(request["seed"], index, take_index)
                write_json(root / "progress.json", {"completed": completed, "total": total,
                           "message": f"Line {index + 1}, take {take_index + 1}"})
                sample_rate, pcm = model.infer(spk_audio_prompt=str(voice), text=line["text"], output_path=None,
                            lang=line["language"], use_emo_text=bool(line["delivery"]),
                            emo_text=line["delivery"] or None, emo_alpha=line["strength"],
                            duration_factor=line["duration_factor"], seed=seed,
                            do_sample=True, top_p=0.8, top_k=30, temperature=0.8,
                            max_text_tokens_per_segment=100, diffusion_steps=25, verbose=False)
                samples = np.asarray(pcm, dtype=np.float32).reshape(-1, 1).mean(axis=1) / 32768.0
                if sample_rate != manifest["sample_rate"]:
                    raise RuntimeError(f"Unexpected sample rate: {sample_rate}")
                if line["pitch_semitones"]:
                    samples = librosa.effects.pitch_shift(samples, sr=sample_rate, n_steps=line["pitch_semitones"])
                samples *= 10 ** (line["gain_db"] / 20)
                clipped = int(np.count_nonzero(np.abs(samples) > 1))
                sf.write(output, np.clip(samples, -1, 1), sample_rate, subtype="PCM_16")
                line_record["takes"].append(dict(take=take_index + 1, file=filename, seed=seed,
                    seconds=len(samples) / sample_rate, clipped_samples=clipped,
                    sha256=hashlib.sha256(output.read_bytes()).hexdigest()))
                completed += 1
                write_json(root / "takes.json", manifest)
        timeline = assemble(manifest, root, [1] * len(script["lines"]), root / "joined.wav")
        manifest.update(complete=True, default_choices=[1] * len(script["lines"]), timeline=timeline)
        write_json(root / "takes.json", manifest)
        write_json(root / "progress.json", {"completed": total, "total": total, "message": "Finished"})
        print("Voice Director finished. All takes saved; worker exiting releases GPU memory.", flush=True)


if __name__ == "__main__":
    generate(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
