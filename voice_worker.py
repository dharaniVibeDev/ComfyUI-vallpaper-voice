"""One generation per process: GPU allocations disappear when the worker exits."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("VALLPAPER_TTS_HOME", str(Path.home() / "Apps" / "VallpaperTTS"))).expanduser()
sys.path.insert(0, str(ROOT))
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


def generate(request):
    import torch
    import soundfile as sf
    from indextts.infer_v2_5 import IndexTTS2

    torch.set_num_threads(4)
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in the TTS environment.")
    free, _ = torch.cuda.mem_get_info()
    if free < 5.5 * 1024**3:
        raise RuntimeError("TTS needs at least 5.5 GB free VRAM. When ComfyUI is idle, use its Free VRAM button, then retry.")
    model_dir = ROOT / "models" / "IndexTTS-2.5"
    print("Loading IndexTTS 2.5. The first run takes longer.", flush=True)
    model = IndexTTS2(
        cfg_path=str(model_dir / "config.yaml"), model_dir=str(model_dir),
        device="cuda:0", reference_device="cpu", use_bf16=True,
        use_cuda_kernel=False, use_deepspeed=False, use_torch_compile=False,
        reference_cache_dir=str(ROOT / "reference_cache"),
    )
    model.low_vram = True
    use_text = request["mode"] == "Emotion description"
    if use_text:
        print("Loading the emotion interpreter.", flush=True)
        model.ensure_qwen_emotion()
    print("Generating speech...", flush=True)
    sample_rate, samples = model.infer(
        spk_audio_prompt=request["voice"], text=request["text"],
        output_path=None, lang=request.get("language", "EN"),
        use_emo_text=use_text, emo_text=request.get("emotion_text") or request["text"],
        emo_vector=request["vector"] if request["mode"] == "Emotion sliders" else None,
        emo_alpha=float(request["strength"]), duration_factor=float(request["duration_factor"]),
        seed=int(request["seed"]), do_sample=False, max_text_tokens_per_segment=100,
        diffusion_steps=25, verbose=False,
    )
    sf.write(request["output"], samples, sample_rate, subtype="PCM_16")
    if not Path(request["output"]).is_file():
        raise RuntimeError("The model did not produce an audio file. Check the generation log.")
    print("Finished. GPU memory is released as this worker exits.", flush=True)


if __name__ == "__main__":
    from filelock import FileLock

    with FileLock(str(ROOT / "generation.lock"), timeout=1):
        generate(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")))
