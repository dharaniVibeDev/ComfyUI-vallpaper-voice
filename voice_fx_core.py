"""CPU audio effects. No model loading, network access or server startup."""
import math

import numpy as np
from scipy.signal import butter, fftconvolve, lfilter, sosfilt

PARAMETERS = dict(wet=0.2, decay=0.6, predelay_ms=12, damping_hz=5500,
                  room_size=1, robot_mix=0, robot_hz=70, bass_db=0,
                  echo_ms=0, echo_mix=0)
PRESETS = {
    "Dry": dict(wet=0, decay=0.1, predelay_ms=0),
    "Small room": dict(wet=0.18, decay=0.45, predelay_ms=8, room_size=0.65),
    "Large room": dict(wet=0.25, decay=1.2, predelay_ms=20, room_size=1.3),
    "Hall": dict(wet=0.32, decay=2.6, predelay_ms=35, room_size=1.8, damping_hz=4200),
    "Outside": dict(wet=0, decay=0.1, predelay_ms=0, echo_ms=95, echo_mix=0.07),
    "Robot": dict(wet=0.08, decay=0.25, robot_mix=0.6, robot_hz=70),
    "Bass": dict(wet=0, bass_db=6),
    "Robot + bass": dict(wet=0.12, decay=0.4, robot_mix=0.55, robot_hz=55, bass_db=6),
    "Metallic / clang": dict(wet=0.3, decay=1.1, room_size=0.4, damping_hz=10000,
                              robot_mix=0.3, robot_hz=155, bass_db=-4, predelay_ms=4),
    "Custom": {},
}


def settings_for(preset, custom, controls, reverb_amount=1, bass_adjust_db=0):
    if preset not in PRESETS:
        raise ValueError("Choose a listed Voice FX preset.")
    settings = dict(PARAMETERS)
    settings.update(PRESETS[preset])
    if custom or preset == "Custom":
        settings.update({key: float(controls[key]) for key in PARAMETERS})
    if not all(math.isfinite(value) for value in settings.values()):
        raise ValueError("Voice FX controls must be finite numbers.")
    settings["wet"] = float(np.clip(settings["wet"] * reverb_amount, 0, 1))
    settings["bass_db"] += bass_adjust_db
    return settings


def low_shelf(samples, rate, db):
    if not db:
        return samples
    a = 10 ** (db / 40)
    w = 2 * np.pi * min(180, rate * .2) / rate
    c, s = np.cos(w), np.sin(w)
    alpha = s / 2 * np.sqrt((a + 1/a) * (1/.7 - 1) + 2)
    beta = 2 * np.sqrt(a) * alpha
    b = a * np.array([(a+1)-(a-1)*c+beta, 2*((a-1)-(a+1)*c), (a+1)-(a-1)*c-beta])
    denominator = np.array([(a+1)+(a-1)*c+beta, -2*((a-1)+(a+1)*c), (a+1)+(a-1)*c-beta])
    return lfilter(b / denominator[0], denominator / denominator[0], samples, axis=-1)


def room_impulse(rate, settings):
    decay = max(.05, settings["decay"])
    length = max(32, round(rate * decay * 1.5))
    ir = np.zeros(length, dtype=np.float64)
    # Eight feedback-comb echo trains, damped to -60 dB at the requested decay.
    for milliseconds in [29.7, 37.1, 41.1, 43.7, 53.1, 59.3, 67.7, 79.1]:
        delay = max(1, round(rate * milliseconds / 1000 * max(.1, settings["room_size"])))
        positions = np.arange(delay, length, delay)
        ir[positions] += 10 ** (-3 * positions / (rate * decay)) / 8
    cutoff = min(max(settings["damping_hz"], 100), rate * .45)
    ir = sosfilt(butter(2, cutoff, fs=rate, output="sos"), ir)
    # Short all-pass diffusers spread discrete echoes into a denser room tail.
    for milliseconds in [5.1, 1.7]:
        delay = max(1, round(rate * milliseconds / 1000))
        diffuse = -0.5 * ir.copy()
        for repeat in range(1, min(32, length // delay)):
            offset = repeat * delay
            diffuse[offset:] += .75 * .5 ** (repeat - 1) * ir[:-offset]
        ir = diffuse
    energy = np.sqrt(np.sum(ir * ir))
    if energy:
        ir /= energy
    return np.pad(ir, (max(0, round(rate * settings["predelay_ms"] / 1000)), 0))


def process_audio(waveform, rate, settings, normalize_peaks=True):
    samples = np.asarray(waveform, dtype=np.float64)
    if samples.ndim != 3 or samples.shape[-1] == 0:
        raise ValueError("Connect nonempty AUDIO with shape [batch, channels, samples].")
    if rate <= 0 or not np.isfinite(samples).all():
        raise ValueError("Voice FX needs a valid sample rate and finite audio samples.")
    samples = low_shelf(samples, rate, settings["bass_db"])
    mix = np.clip(settings["robot_mix"], 0, 1)
    if mix:
        carrier = np.sin(2 * np.pi * settings["robot_hz"] * np.arange(samples.shape[-1]) / rate)
        samples = samples * ((1 - mix) + mix * carrier)
    if settings["echo_ms"] > 0 and settings["echo_mix"]:
        delay = max(1, round(rate * settings["echo_ms"] / 1000))
        dry = samples
        samples = np.pad(dry, ((0, 0), (0, 0), (0, delay)))
        samples[..., delay:] += dry * settings["echo_mix"]
    wet = np.clip(settings["wet"], 0, 1)
    if wet:
        impulse = room_impulse(rate, settings)
        reverberated = fftconvolve(samples, impulse[None, None, :], axes=-1)
        samples = (1 - wet) * np.pad(samples, ((0, 0), (0, 0), (0, len(impulse)-1))) + wet * reverberated
    if normalize_peaks:
        peak = np.max(np.abs(samples), axis=(1, 2), keepdims=True)
        samples /= np.maximum(1, peak / .98)
    return samples.astype(np.float32)
