"""Original, royalty-free backing track for the preview videos.

Everything is synthesised from scratch with numpy/scipy (no samples, no
copyrighted material), so the track can be used on TikTok, Instagram and
YouTube without claims.

    track(duration, cues, bpm=120) -> float32 stereo array (n, 2) at SR

`cues` is a list of times (seconds) where a new section starts (each game);
they get an impact and a whoosh leading into them (no rising build-up at the start).

    series_track(series, duration, cues=(), seed=None) -> float32 stereo (n, 2)
    style_track(style, duration, cues=(), seed=None)   -> float32 stereo (n, 2)
    SERIES_STYLES: {series: [style, ...]}

The series engine (bottom of the file) has 15 styles (3 per series); key, tempo, progression,
drum variant, bass/arp patterns and melody are all derived from `seed`, so every day sounds different.
"""
import datetime
import hashlib
import secrets
import zlib

import numpy as np
from scipy import signal

SR = 44100
rng = np.random.default_rng(7)


def _env(n, attack, decay, sr=SR):
    t = np.arange(n) / sr
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-t / max(decay, 1e-4))


def _lp(x, fc, order=2):
    b, a = signal.butter(order, min(fc, SR / 2 - 100) / (SR / 2), "low")
    return signal.lfilter(b, a, x)


def _hp(x, fc, order=2):
    b, a = signal.butter(order, fc / (SR / 2), "high")
    return signal.lfilter(b, a, x)


def _bp(x, lo, hi, order=2):
    b, a = signal.butter(order, [lo / (SR / 2), min(hi, SR / 2 - 100) / (SR / 2)], "band")
    return signal.lfilter(b, a, x)


def _saw(freq, n, phase=0.0):
    t = np.arange(n) / SR
    return 2 * ((t * freq + phase) % 1.0) - 1


def _note(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def _add(buf, x, at):
    i = int(round(at * SR))
    if i >= len(buf):
        return
    j = min(len(buf), i + len(x))
    buf[i:j] += x[: j - i]


def kick():
    n = int(0.45 * SR)
    t = np.arange(n) / SR
    f = 45 + 110 * np.exp(-t / 0.035)
    ph = 2 * np.pi * np.cumsum(f) / SR
    body = np.sin(ph) * np.exp(-t / 0.28)
    click = _hp(rng.standard_normal(n), 2500) * np.exp(-t / 0.004) * 0.35
    return np.tanh(1.6 * (body + click))


def clap():
    n = int(0.35 * SR)
    noise = _bp(rng.standard_normal(n), 900, 5200)
    t = np.arange(n) / SR
    e = np.zeros(n)
    for k, d in enumerate([0, 0.011, 0.022]):
        e += (t >= d) * np.exp(-np.clip(t - d, 0, None) / (0.006 if k < 2 else 0.12))
    tone = np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.05) * 0.4
    return (noise * e * 0.7 + tone) * 0.8


def hat(open_=False):
    n = int((0.18 if open_ else 0.05) * SR)
    x = _hp(rng.standard_normal(n), 7500, 3)
    return x * _env(n, 0.0005, 0.06 if open_ else 0.012) * (0.5 if open_ else 0.35)


def impact():
    n = int(1.6 * SR)
    t = np.arange(n) / SR
    boom = np.sin(2 * np.pi * (38 + 30 * np.exp(-t / 0.08)) * t) * np.exp(-t / 0.55)
    crash = _hp(rng.standard_normal(n), 3000) * np.exp(-t / 0.5) * 0.25
    return np.tanh(1.3 * (boom + crash)) * 0.9


def whoosh(dur=0.5):
    n = int(dur * SR)
    x = rng.standard_normal(n)
    out = np.zeros(n)
    seg = 512
    for i in range(0, n, seg):
        p = i / n
        fc = 400 + 7000 * p
        y = _bp(x[max(0, i - 2048): i + seg], fc * 0.7, fc * 1.3)[-seg:]
        out[i:i + len(y)] = y[: n - i]
    return out * np.sin(np.pi * np.linspace(0, 1, n)) ** 2 * 0.6


def riser(dur=1.0):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 200 * 2 ** (3 * t / dur)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.15
    noise = _hp(rng.standard_normal(n), 1500) * 0.25
    return (tone + noise) * (t / dur) ** 2


def _reverb(x, decay=1.4, mix=0.25):
    n = int(decay * SR)
    ir = rng.standard_normal(n) * np.exp(-np.arange(n) / SR * (6.9 / decay))
    ir = _lp(ir, 6000)
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    wet = signal.fftconvolve(x, ir)[: len(x)]
    return x * (1 - mix) + wet * mix


# A minor: Am - F - C - G (one chord per bar)
PROG = [(57, [57, 60, 64]), (53, [53, 57, 60]), (48, [55, 60, 64]), (55, [55, 59, 62])]


def track(duration, cues=(), bpm=120, intro=None):
    beat = 60.0 / bpm
    bar = beat * 4
    n = int((duration + 0.1) * SR)
    drums = np.zeros(n)
    bass = np.zeros(n)
    music = np.zeros(n)
    fx = np.zeros(n)
    duck = np.ones(n)
    intro = cues[0] if (intro is None and cues) else (intro or 0)
    outro = duration - 4.0

    K, C, H, O = kick(), clap(), hat(), hat(True)
    # sidechain envelope per kick
    dk = 1 - 0.75 * np.exp(-np.arange(int(beat * SR)) / SR / 0.09)

    nb = int(duration / beat) + 1
    for b in range(nb):
        t = b * beat
        in_drop = intro <= t < outro
        if in_drop or (t < intro and b % 2 == 0 and t >= intro - 2 * bar):
            _add(drums, K, t)
            i = int(t * SR)
            j = min(n, i + len(dk))
            duck[i:j] = np.minimum(duck[i:j], dk[: j - i])
        if in_drop and b % 4 in (1, 3):
            _add(drums, C, t)
        if t < duration - 1.0:
            sw = 0.018
            _add(drums, H * (0.8 if in_drop else 0.45), t)
            if in_drop:
                _add(drums, O * 0.7, t + beat / 2 + sw)
            else:
                _add(drums, H * 0.35, t + beat / 2 + sw)

    # bass: pumping 8ths following the root
    for b in range(int(duration / bar) + 1):
        t0 = b * bar
        root, chord = PROG[b % 4]
        for k in range(8):
            t = t0 + k * beat / 2
            if t >= duration:
                break
            m = root - 12 + (12 if k in (3, 7) else 0)
            ln = int(beat / 2 * 0.9 * SR)
            x = (_saw(_note(m), ln) + 0.5 * np.sign(np.sin(2 * np.pi * _note(m - 12) * np.arange(ln) / SR)))
            x = _lp(x, 900 if intro <= t < outro else 350) * _env(ln, 0.003, 0.18)
            _add(bass, x * 0.55, t)
        # pad chord (long, soft)
        ln = int(bar * SR)
        pad = sum(_saw(_note(c) * d, ln, rng.random()) for c in chord for d in (0.997, 1.003))
        pad = _lp(pad, 1800) * (0.5 - 0.5 * np.cos(2 * np.pi * np.minimum(np.arange(ln) / ln, 1))) ** 0.5
        _add(music, pad * 0.05, t0)
        # pluck arpeggio 16ths in the drop
        if intro <= t0 < outro or t0 >= intro - bar:
            pat = [0, 1, 2, 1, 2, 0, 1, 2]
            for k in range(16):
                t = t0 + k * beat / 4
                if t >= duration - 0.5:
                    break
                m = chord[pat[k % 8]] + 12 + (12 if k in (6, 14) else 0)
                ln2 = int(0.22 * SR)
                x = sum(_saw(_note(m) * d, ln2) for d in (0.995, 1.0, 1.005)) / 3
                x = _lp(x, 2600) * _env(ln2, 0.002, 0.07)
                g = 0.11 if intro <= t < outro else 0.05
                _add(music, x * g, t)

    # simple 3/16 echo on the music bus
    d = int(beat * 0.75 * SR)
    echo = np.zeros(n)
    echo[d:] = music[:-d] * 0.32
    echo[2 * d:] += music[:-2 * d] * 0.12
    music = _reverb(music + echo, 1.6, 0.3)

    for c in cues:
        _add(fx, impact() * 0.8, c)
        _add(fx, whoosh(0.45), max(0, c - 0.42))

    mix = drums * 0.9 + (bass + music) * duck + fx
    mix = _hp(mix, 28)
    # outro fade
    fo = int(2.5 * SR)
    mix[-fo:] *= np.linspace(1, 0, fo) ** 1.5
    fi = int(0.03 * SR)
    mix[:fi] *= np.linspace(0, 1, fi)

    # stereo: widen the music/reverb a little with a Haas delay
    hd = int(0.012 * SR)
    wide = np.zeros(n)
    wide[hd:] = (music * duck)[:-hd]
    L = mix
    R = mix - (music * duck) * 0.35 + wide * 0.35
    st = np.stack([L, R], 1)
    st = np.tanh(st * 1.1) / np.tanh(1.1)
    st /= np.max(np.abs(st)) + 1e-9
    return (st * 0.89).astype(np.float32)[: int(duration * SR)]


# ---------------------------------------------------------------- review track (a different song)
# Warm, premium half-time groove for the "last night's results" video: FM electric piano, deep 808 bass,
# rim + clap backbeat, rolling hats and bell sparkles on the winning picks. D-flat major / F minor colours.
REV_PROG = [(49, [53, 56, 60, 63]), (48, [51, 55, 58, 62]), (53, [56, 60, 63, 67]), (51, [55, 58, 62, 65])]


def _epiano(m, dur, vel=1.0):
    """FM electric piano: carrier + 1:1 modulator with a decaying index, slight tremolo."""
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = _note(m)
    idx = 1.8 * np.exp(-t / 0.35) + 0.25
    mod = np.sin(2 * np.pi * f * t) * idx
    x = np.sin(2 * np.pi * f * t + mod) + 0.25 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t / 0.2)
    x *= _env(n, 0.004, 1.6) * (1 + 0.12 * np.sin(2 * np.pi * 4.5 * t)) * vel
    return x


def _bell(m, dur=1.6):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = _note(m)
    x = np.sin(2 * np.pi * f * t + 2.2 * np.exp(-t / 0.5) * np.sin(2 * np.pi * f * 3.5 * t))
    return x * _env(n, 0.002, 0.55)


def _808(m, dur):
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = _note(m) * (1 + 0.6 * np.exp(-t / 0.03))
    x = np.sin(2 * np.pi * np.cumsum(f) / SR)
    x = np.tanh(2.2 * x) * _env(n, 0.003, min(dur * 0.7, 1.1))
    return _lp(x, 700)


def rim():
    n = int(0.12 * SR)
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * 1650 * t) * np.exp(-t / 0.012) + _bp(rng.standard_normal(n), 2000, 6000) * np.exp(-t / 0.008) * 0.6
    return x * 0.5


def review_track(duration, cues=(), wins=(), bpm=96):
    """`cues`: section starts (impact + whoosh). `wins`: times of winning-pick reveals (bell sparkle)."""
    beat = 60.0 / bpm
    bar = beat * 4
    n = int((duration + 0.1) * SR)
    drums, bass, keys, fx = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
    duck = np.ones(n)
    intro = cues[0] if cues else 0
    outro = duration - 5.0
    K, C, H, O, RM = kick(), clap(), hat(), hat(True), rim()
    dk = 1 - 0.55 * np.exp(-np.arange(int(beat * SR)) / SR / 0.12)
    nb = int(duration / beat) + 1
    for b in range(nb):
        t = b * beat
        groove = intro - bar <= t < outro + bar
        pos = b % 8           # two-bar pattern in half time
        if groove and pos in (0, 5):
            _add(drums, K, t)
            i = int(t * SR); j = min(n, i + len(dk)); duck[i:j] = np.minimum(duck[i:j], dk[: j - i])
        if groove and pos in (0,) and b % 16 == 8:
            _add(drums, K * 0.7, t + beat * 0.75)
        if groove and pos in (2, 6):
            _add(drums, C * 0.75, t); _add(drums, RM * 0.5, t + 0.006)
        if t < duration - 1.2:
            for k in range(4):   # 16th hats with velocity, a triplet roll every 4 bars
                v = (0.55, 0.25, 0.4, 0.22)[k] * (1.0 if groove else 0.4)
                _add(drums, H * v, t + k * beat / 4 + (0.012 if k % 2 else 0))
            if groove and b % 16 == 15:
                for k in range(6):
                    _add(drums, H * 0.3, t + k * beat / 6)
            if groove and pos == 7:
                _add(drums, O * 0.35, t + beat / 2)
    for b in range(int(duration / bar) + 1):
        t0 = b * bar
        root, chord = REV_PROG[b % 4]
        on = intro - bar <= t0 < outro + bar
        # electric piano: chord on 1, soft re-hit on the "and" of 3
        for at, vel in ((0, 1.0), (beat * 2.5, 0.55)):
            for k, c in enumerate(chord):
                _add(keys, _epiano(c, bar * 0.95, vel) * 0.06, t0 + at + k * 0.012)
        if on:
            for at, ln, mm in ((0, beat * 1.4, root - 24), (beat * 1.5, beat * 0.5, root - 24), (beat * 2.5, beat * 1.3, root - 17)):
                _add(bass, _808(mm, ln) * 0.6, t0 + at)
            # sparse top melody
            mel = [chord[3] + 12, chord[2] + 12, chord[1] + 12]
            for k, mm in enumerate(mel):
                _add(keys, _bell(mm, 1.0) * 0.025, t0 + beat * (1 + k * 1.0))
    d = int(beat * 0.75 * SR)
    echo = np.zeros(n); echo[d:] = keys[:-d] * 0.28
    keys = _reverb(keys + echo, 2.2, 0.35)
    for c in cues:
        _add(fx, impact() * 0.6, c)
        _add(fx, whoosh(0.5) * 0.8, max(0, c - 0.45))
    for w in wins:
        for k, mm in enumerate((84, 88, 91, 96)):
            _add(fx, _bell(mm, 1.2) * 0.05, w + k * 0.045)
    fx = _reverb(fx, 1.2, 0.2)
    mix = drums * 0.8 + (bass + keys) * duck + fx
    mix = _hp(mix, 26)
    fo = int(3.0 * SR); mix[-fo:] *= np.linspace(1, 0, fo) ** 1.5
    fi = int(0.03 * SR); mix[:fi] *= np.linspace(0, 1, fi)
    hd = int(0.014 * SR); wide = np.zeros(n); wide[hd:] = keys[:-hd]
    st = np.stack([mix, mix - keys * 0.3 + wide * 0.3], 1)
    st = np.tanh(st * 1.1) / np.tanh(1.1)
    st /= np.max(np.abs(st)) + 1e-9
    return (st * 0.89).astype(np.float32)[: int(duration * SR)]


# ===================================================================================== series engine
# Varied, seeded songs for the different video series. Everything below uses its own random generator
# (never the module-level `rng`), so track() / review_track() stay bit-identical.

MODES = {
    "major": [0, 2, 4, 5, 7, 9, 11], "minor": [0, 2, 3, 5, 7, 8, 10], "dorian": [0, 2, 3, 5, 7, 9, 10],
    "phrygian": [0, 1, 3, 5, 7, 8, 10], "harm": [0, 2, 3, 5, 7, 8, 11], "mixo": [0, 2, 4, 5, 7, 9, 10],
    "lydian": [0, 2, 4, 6, 7, 9, 11],
}
_VEL = {"X": 1.0, "x": 0.72, "o": 0.45, "g": 0.25, "r": 0.6, "R": 0.6, "q": 0.55}


def _seed_int(seed):
    if seed is None:
        return secrets.randbits(63)
    if isinstance(seed, (int, np.integer)) and not isinstance(seed, bool):
        return int(seed) % (2 ** 63)
    return int.from_bytes(hashlib.sha256(str(seed).encode("utf-8")).digest()[:8], "little") >> 1


def _tt(n):
    return np.arange(n) / SR


def _gate(x, n_on):
    """Fade (squared) from sample n_on to the end, so notes never stop with a click."""
    if len(x) > n_on + 1:
        x = x.copy()
        x[n_on:] *= np.linspace(1, 0, len(x) - n_on) ** 2
    return x


def _pk(x):
    return x / (np.max(np.abs(x)) + 1e-9)


def _st(fn, x, *a):
    """Apply a mono filter helper to each channel of an (n, 2) array."""
    return np.stack([fn(x[:, 0], *a), fn(x[:, 1], *a)], 1)


# ------------------------------------------------------------------ drums (local rng `r`)
def _d_kick(r, f0=160, f1=48, pd=0.035, dec=0.3, click=0.35, drive=1.8, lp=None, ln=0.5):
    n = int(ln * SR)
    t = _tt(n)
    f = f1 + (f0 - f1) * np.exp(-t / pd)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / dec)
    cl = _hp(r.standard_normal(n), 2500) * np.exp(-t / 0.004) * click
    x = np.tanh(drive * (body + cl)) / np.tanh(drive)
    if lp:
        x = _lp(x, lp)
    return _gate(_pk(x), n - int(0.03 * SR))


def _d_snare(r, tone=190, nd=0.13, td=0.05, lo=1200, hi=8000, ln=0.45, lp=None):
    n = int(ln * SR)
    t = _tt(n)
    body = (np.sin(2 * np.pi * tone * t) + 0.5 * np.sin(2 * np.pi * tone * 1.72 * t)) * np.exp(-t / td)
    nz = _bp(r.standard_normal(n), lo, hi) * np.exp(-t / nd)
    x = body * 0.55 + _pk(nz) * 0.9
    if lp:
        x = _lp(x, lp)
    return _gate(_pk(x), n - int(0.04 * SR))


def _d_clap(r):
    n = int(0.4 * SR)
    t = _tt(n)
    noise = _bp(r.standard_normal(n), 900, 5200)
    e = np.zeros(n)
    for k, d in enumerate([0, 0.010, 0.021, 0.030]):
        e += (t >= d) * np.exp(-np.clip(t - d, 0, None) / (0.006 if k < 3 else 0.13))
    x = noise * e + np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.04) * 0.3
    return _gate(_pk(x), n - int(0.04 * SR))


def _metal(r, n, scale=1.0):
    t = _tt(n)
    return sum(np.sign(np.sin(2 * np.pi * f * scale * t + r.random() * 6.28))
               for f in (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)) / 6


def _d_hat(r, dec=0.03, ln=0.12, hp=7000, metal=0.5, lp=None):
    n = int(ln * SR)
    t = _tt(n)
    x = metal * _metal(r, n) + (1 - metal) * r.standard_normal(n)
    x = _hp(x, hp, 3) * np.exp(-t / dec) * np.clip(t / 0.0006, 0, 1)
    if lp:
        x = _lp(x, lp)
    return _gate(_pk(x), n - int(0.01 * SR))


def _d_shaker(r):
    n = int(0.12 * SR)
    t = _tt(n)
    x = _bp(r.standard_normal(n), 3500, 11000) * np.clip(t / 0.012, 0, 1) * np.exp(-t / 0.035)
    return _gate(_pk(x), n - int(0.01 * SR))


def _d_rim(r):
    n = int(0.1 * SR)
    t = _tt(n)
    x = np.sin(2 * np.pi * 1650 * t) * np.exp(-t / 0.011) + _bp(r.standard_normal(n), 2000, 6000) * np.exp(-t / 0.007) * 0.6
    return _gate(_pk(x), n - int(0.01 * SR))


def _d_snap(r):
    n = int(0.15 * SR)
    t = _tt(n)
    x = _bp(r.standard_normal(n), 1300, 4200) * np.exp(-t / 0.018) + np.sin(2 * np.pi * 1100 * t) * np.exp(-t / 0.006) * 0.4
    x += _bp(r.standard_normal(n), 800, 3000) * np.exp(-t / 0.06) * 0.15
    return _gate(_pk(x), n - int(0.01 * SR))


def _d_tom(r, f=120, dec=0.3):
    n = int(0.8 * SR)
    t = _tt(n)
    ff = f * (1 + 0.5 * np.exp(-t / 0.04))
    x = np.sin(2 * np.pi * np.cumsum(ff) / SR) * np.exp(-t / dec)
    x += _bp(r.standard_normal(n), 300, 3000) * np.exp(-t / 0.025) * 0.25
    return _gate(_pk(np.tanh(1.4 * x)), n - int(0.05 * SR))


def _d_crash(r, dec=1.1, ln=2.2):
    n = int(ln * SR)
    t = _tt(n)
    x = 0.5 * _metal(r, n, 1.9) + r.standard_normal(n)
    x = _hp(x, 4200, 2) * np.exp(-t / dec) * np.clip(t / 0.002, 0, 1)
    return _gate(_pk(_lp(x, 13000)), n - int(0.3 * SR))


def _d_wood(r, f=880):
    n = int(0.12 * SR)
    t = _tt(n)
    x = np.sin(2 * np.pi * f * t) * np.exp(-t / 0.03) + 0.45 * np.sin(2 * np.pi * 2.76 * f * t) * np.exp(-t / 0.01)
    return _gate(_pk(x), n - int(0.01 * SR))


def _d_tick(r):
    n = int(0.05 * SR)
    t = _tt(n)
    x = _hp(r.standard_normal(n), 5000) * np.exp(-t / 0.0025) + np.sin(2 * np.pi * 4200 * t) * np.exp(-t / 0.004) * 0.5
    return _gate(_pk(x), n - int(0.005 * SR))


def _d_chipnoise(r, dec=0.1, hold=4, hp=None):
    n = int(max(0.06, dec * 4) * SR)
    t = _tt(n)
    x = np.repeat(r.uniform(-1, 1, n // hold + 1), hold)[:n]
    x = np.round(x * 7) / 7 * np.exp(-t / dec)
    if hp:
        x = _hp(x, hp)
    return _gate(_pk(x), n - int(0.01 * SR))


def _d_impact(r, size=1.0):
    n = int(1.8 * SR)
    t = _tt(n)
    boom = np.sin(2 * np.pi * np.cumsum(36 + 34 * np.exp(-t / 0.08)) / SR) * np.exp(-t / (0.45 + 0.25 * size))
    crash = _hp(r.standard_normal(n), 2500) * np.exp(-t / 0.45) * 0.25
    x = np.tanh(1.3 * (boom + crash))
    return _gate(_pk(_lp(x, 9000)), n - int(0.3 * SR))


def _d_whoosh(r, dur=0.45):
    n = int(dur * SR)
    x = r.standard_normal(n + 2048)
    out = np.zeros(n)
    seg = 512
    for i in range(0, n, seg):
        fc = 400 + 7000 * (i / n)
        y = _bp(x[i: i + 2048 + seg], fc * 0.7, fc * 1.3)[-seg:]
        out[i:i + seg] = y[: len(out[i:i + seg])]
    return _pk(out * np.sin(np.pi * np.linspace(0, 1, n)) ** 2)


def _d_riser(r, dur=2.0):
    n = int(dur * SR)
    t = _tt(n)
    f = 220 * 2 ** (2.5 * t / dur)
    tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.25
    noise = _hp(r.standard_normal(n), 1800) * 0.6
    return _pk((tone + noise) * (t / dur) ** 2.2)


def _vinyl(r, n):
    mask = r.random(n) < 9.0 / SR
    x = np.zeros(n)
    k = int(mask.sum())
    x[mask] = r.standard_normal(k) * r.random(k) ** 2
    return _bp(x, 900, 7000) * 0.35 + _lp(r.standard_normal(n), 2500) * 0.012


_KICKS = {
    "punch": dict(f0=160, f1=48, pd=0.035, dec=0.30, click=0.35, drive=1.8),
    "house": dict(f0=150, f1=50, pd=0.028, dec=0.24, click=0.30, drive=2.2),
    "trap": dict(f0=190, f1=50, pd=0.022, dec=0.22, click=0.45, drive=2.0),
    "lofi": dict(f0=115, f1=52, pd=0.030, dec=0.20, click=0.10, drive=1.3, lp=1500),
    "acoustic": dict(f0=120, f1=55, pd=0.020, dec=0.16, click=0.15, drive=1.2, lp=2500),
    "soft": dict(f0=125, f1=50, pd=0.030, dec=0.20, click=0.12, drive=1.4, lp=4000),
    "cine": dict(f0=95, f1=38, pd=0.080, dec=0.60, click=0.10, drive=1.5, ln=1.0),
    "hybrid": dict(f0=140, f1=42, pd=0.050, dec=0.45, click=0.30, drive=2.0, ln=0.8),
    "chip": dict(f0=220, f1=55, pd=0.020, dec=0.12, click=0.0, drive=3.0, ln=0.3),
}
_SNARES = {
    "lofi": dict(tone=180, nd=0.10, lo=900, hi=5000, lp=4500),
    "cine": dict(tone=160, nd=0.25, td=0.08, lo=600, hi=7000, ln=0.7),
    "hybrid": dict(tone=170, nd=0.2, td=0.07, lo=700, hi=8000, ln=0.6),
    "trap": dict(tone=205, nd=0.12, lo=1500, hi=9000),
}
_PAN = {"h": 0.2, "o": -0.2, "sh": -0.25, "r": 0.15, "sn": -0.1, "tk": 0.3, "w": 0.25, "t1": -0.35, "t3": 0.35, "nh": 0.15}
_SEND = {"s": 0.25, "c": 0.25, "sn": 0.2, "r": 0.15, "t1": 0.3, "t2": 0.3, "t3": 0.3, "w": 0.2, "n": 0.1, "k": 0.0}


def _make_drum(r, kit, lane):
    if lane.startswith("k"):
        return _d_kick(r, **_KICKS.get(kit, _KICKS["punch"]))
    if lane == "s":
        return _d_snare(r, **_SNARES.get(kit, {}))
    if lane == "c":
        return _d_clap(r)
    if lane == "h":
        return _d_hat(r, dec=0.032, metal=0.7 if kit in ("trap", "hybrid", "punch") else 0.3, lp=6500 if kit == "lofi" else None)
    if lane == "o":
        return _d_hat(r, dec=0.16, ln=0.45, metal=0.6, lp=7000 if kit == "lofi" else None)
    if lane == "sh":
        return _d_shaker(r)
    if lane == "r":
        return _d_rim(r)
    if lane == "sn":
        return _d_snap(r)
    if lane in ("t1", "t2", "t3"):
        return _d_tom(r, {"t1": 175, "t2": 128, "t3": 92}[lane], 0.3 if kit != "cine" else 0.45)
    if lane == "w":
        return _d_wood(r)
    if lane == "tk":
        return _d_tick(r)
    if lane == "n":
        return _d_chipnoise(r, 0.09, 3)
    if lane == "nh":
        return _d_chipnoise(r, 0.018, 1, hp=5000)
    if lane == "cr":
        return _d_crash(r)
    raise KeyError(lane)


# ------------------------------------------------------------------ tonal instruments (mono, peak 1)
def _i_saws(r, m, dur, voices=5, det=0.010, lp=2000, atk=0.03, dec=None, sus=1.0, rel=0.35, vib=0.0, hp=None):
    f = _note(m)
    n_on = int(dur * SR)
    n = n_on + int(rel * SR)
    t = _tt(n)
    if vib:
        ph = np.cumsum(1 + vib * np.sin(2 * np.pi * 5.1 * t) * np.clip((t - 0.25) / 0.5, 0, 1)) / SR
    else:
        ph = t
    x = np.zeros(n)
    for v in range(voices):
        d = 1 + (det * (2 * v / (voices - 1) - 1) if voices > 1 else 0)
        x += 2 * ((ph * f * d + r.random()) % 1.0) - 1
    x = _lp(x, lp, 2)
    if hp:
        x = _hp(x, hp)
    env = np.clip(t / max(atk, 1e-4), 0, 1)
    if dec:
        env = env * (sus + (1 - sus) * np.exp(-t / dec))
    return _gate(_pk(x * env), n_on)


def _i_add(r, m, dur, kmax=40, decay=0.3, bright=1.0, odd=False, rel=0.06, atk=0.002, fmax=9000, floor=0.0, sub=0.0):
    """Additive 'filtered saw' pluck: higher harmonics die faster (a natural filter envelope)."""
    f = _note(m)
    n_on = int(dur * SR)
    n = n_on + int(rel * SR)
    t = _tt(n)
    x = np.zeros(n)
    K = max(1, min(kmax, int(fmax / f)))
    for k in range(1, K + 1):
        if odd and k % 2 == 0:
            continue
        tau = decay / (1 + (k - 1) * 0.25 / bright)
        x += np.sin(2 * np.pi * k * f * t) / k * (floor + (1 - floor) * np.exp(-t / tau))
    if sub:
        x += sub * np.sin(np.pi * f * t) * (0.5 + 0.5 * np.exp(-t / (decay * 2)))
    x *= np.clip(t / atk, 0, 1)
    return _gate(_pk(x), n_on)


def _i_pulse(r, m, dur, duty=0.5, fmax=8000, vib=0.0, atk=0.004, dec=None, sus=1.0, rel=0.05):
    f = _note(m)
    n_on = int(dur * SR)
    n = n_on + int(rel * SR)
    t = _tt(n)
    ph = 2 * np.pi * f * np.cumsum(1 + vib * np.sin(2 * np.pi * 5.5 * t) * np.clip((t - 0.15) / 0.3, 0, 1)) / SR
    K = max(1, min(60, int(fmax / f)))
    x = np.zeros(n)
    for k in range(1, K + 1):
        a = 2 / (k * np.pi) * np.sin(np.pi * k * duty)
        if abs(a) > 1e-4:
            x += a * np.cos(k * ph)
    env = np.clip(t / atk, 0, 1)
    if dec:
        env = env * (sus + (1 - sus) * np.exp(-t / dec))
    return _gate(_pk(x * env), n_on)


def _i_organ(r, m, dur, rel=0.05):
    f = _note(m)
    n_on = int(dur * SR)
    n = n_on + int(rel * SR)
    t = _tt(n)
    x = sum(a * np.sin(2 * np.pi * k * f * t) for k, a in ((1, 1), (2, 0.6), (3, 0.45), (4, 0.3), (6, 0.18), (8, 0.1)))
    x = x * (0.25 + 0.75 * np.exp(-t / 0.22)) * np.clip(t / 0.002, 0, 1)
    x += _hp(r.standard_normal(n), 2000) * np.exp(-t / 0.003) * 0.15
    return _gate(_pk(x), n_on)


def _i_marimba(r, m, dur, rel=0.05):
    f = _note(m)
    n = int((dur + rel) * SR)
    t = _tt(n)
    tau = float(np.clip(0.45 * (262 / f) ** 0.5, 0.12, 0.9))
    x = np.sin(2 * np.pi * f * t) * np.exp(-t / tau) + 0.22 * np.sin(2 * np.pi * 3.93 * f * t) * np.exp(-t / (tau / 5))
    x += 0.08 * np.sin(2 * np.pi * 9.2 * f * t) * np.exp(-t / 0.008)
    return _gate(_pk(x * np.clip(t / 0.0015, 0, 1)), int(dur * SR))


def _i_ks(r, m, dur, g=0.996, bright=0.6, rel=0.08):
    """Karplus-Strong string, computed one period-block at a time (vectorised)."""
    f = _note(m)
    n_on = int(dur * SR)
    n = n_on + int(rel * SR)
    N = max(2, int(round(SR / f - 0.5)))
    exc = _lp(r.uniform(-1, 1, N), 800 + 7000 * bright, 1)
    exc -= exc.mean()
    P = N + 1
    Y = np.zeros(n + P)
    X = np.zeros(n)
    X[:min(N, n)] = exc[:min(N, n)]
    h = 0.5 * g
    for s in range(0, n, N):
        e = min(s + N, n)
        Y[P + s:P + e] = X[s:e] + h * (Y[P + s - N:P + e - N] + Y[P + s - N - 1:P + e - N - 1])
    y = Y[P:]
    y = y + 0.3 * _lp(y, 500)
    return _gate(_pk(y), n_on)


def _i_choir(r, m, dur, rel=0.6):
    x = _i_saws(r, m, dur, voices=3, det=0.005, lp=5000, atk=0.35, rel=rel, vib=0.005)
    y = _bp(x, 550, 950) + 0.6 * _bp(x, 1000, 1400) + 0.25 * _bp(x, 2300, 3000)
    return _pk(y)


def _i_brass(r, m, dur, atk=0.08, rel=0.15, bmax=10.0, drive=0.0, fmax=7000):
    f = _note(m)
    n_on = int(dur * SR)
    n = n_on + int(rel * SR)
    t = _tt(n)
    e = np.clip(t / atk, 0, 1) ** 0.7
    b = 1.2 + bmax * e * (0.75 + 0.25 * np.exp(-t / 0.4))
    ph = 2 * np.pi * f * np.cumsum(1 + 0.003 * np.sin(2 * np.pi * 5 * t) * np.clip((t - 0.3) / 0.4, 0, 1)) / SR
    K = max(1, min(30, int(fmax / f)))
    x = sum(np.exp(-(k - 1) / b) / k * np.sin(k * ph) for k in range(1, K + 1))
    x *= e
    if drive:
        x = np.tanh(drive * _pk(x))
    return _gate(_pk(x), n_on)


def _i_808(r, m, dur, glide=0, drive=2.2, rel=0.05):
    f0 = _note(m)
    n_on = int(dur * SR)
    n = n_on + int(rel * SR)
    t = _tt(n)
    f = f0 * (1 + 0.45 * np.exp(-t / 0.02))
    if glide:
        s = np.clip((t - dur * 0.55) / (dur * 0.3 + 1e-3), 0, 1)
        f = f * 2 ** (glide * s * s * (3 - 2 * s) / 12)
    x = np.tanh(drive * np.sin(2 * np.pi * np.cumsum(f) / SR)) / np.tanh(drive)
    env = np.clip(t / 0.002, 0, 1) * (0.35 + 0.65 * np.exp(-t / max(0.25, dur * 0.9)))
    x = _lp(x * env, 900 if drive < 3 else 1800)
    return _gate(_pk(x), n_on)


def _i_sub(r, m, dur, rel=0.06):
    f = _note(m)
    n = int((dur + rel) * SR)
    t = _tt(n)
    x = (np.sin(2 * np.pi * f * t) + 0.18 * np.sin(4 * np.pi * f * t)) * np.clip(t / 0.006, 0, 1) * (0.6 + 0.4 * np.exp(-t / 0.3))
    return _gate(_pk(x), int(dur * SR))


def _i_sawbass(r, m, dur, lp=900, dec=0.18, rel=0.03):
    f = _note(m)
    n = int((dur + rel) * SR)
    t = _tt(n)
    x = 2 * ((t * f) % 1.0) - 1 + 0.5 * np.sign(np.sin(np.pi * f * t))
    x = _lp(x, lp) * np.clip(t / 0.003, 0, 1) * (0.3 + 0.7 * np.exp(-t / dec))
    return _gate(_pk(x), int(dur * SR))


def _i_cowbell(r, m, dur, rel=0.03):
    f = _note(m)
    n = int((dur + rel) * SR)
    t = _tt(n)
    x = np.sign(np.sin(2 * np.pi * f * t)) + 0.8 * np.sign(np.sin(2 * np.pi * f * 1.48 * t))
    x = _bp(x, f * 0.8, min(f * 5, 16000)) * (0.6 * np.exp(-t / 0.05) + 0.4 * np.exp(-t / 0.22)) * np.clip(t / 0.001, 0, 1)
    return _gate(_pk(x), int(dur * SR))


def _i_whistle(r, m, dur, rel=0.08):
    f = _note(m)
    n = int((dur + rel) * SR)
    t = _tt(n)
    ph = 2 * np.pi * f * np.cumsum(1 + 0.006 * np.sin(2 * np.pi * 5.3 * t) * np.clip((t - 0.12) / 0.3, 0, 1)) / SR
    x = (np.sin(ph) + 0.08 * np.sin(2 * ph)) * np.clip(t / 0.035, 0, 1)
    x += _bp(r.standard_normal(n), f * 0.9, f * 1.1) * 0.05
    return _gate(_pk(x), int(dur * SR))


def _i_tri(r, m, dur, rel=0.02):
    f = _note(m)
    n = int((dur + rel) * SR)
    t = _tt(n)
    x = _lp(2 * np.abs(2 * ((t * f) % 1.0) - 1) - 1, 3000)
    return _gate(_pk(x), int(dur * SR))


def _i_epiano(r, m, dur, rel=0.15):
    x = _epiano(m, dur + rel)
    return _gate(_pk(x), int(dur * SR))


def _i_bell(r, m, dur, rel=0.1):
    x = _bell(m, max(dur, 0.5) + rel)
    return _gate(_pk(x), int(max(dur, 0.5) * SR))


INSTRUMENTS = {
    "pad": lambda r, m, d: _i_saws(r, m, d, voices=5, det=0.010, lp=1900, atk=0.06, rel=0.45),
    "pad_soft": lambda r, m, d: _i_saws(r, m, d, voices=4, det=0.008, lp=1100, atk=0.25, rel=0.6),
    "pad_warm": lambda r, m, d: _i_saws(r, m, d, voices=5, det=0.007, lp=1400, atk=0.05, rel=0.5),
    "strings": lambda r, m, d: _i_saws(r, m, d, voices=4, det=0.007, lp=3000, atk=0.3, rel=0.5, vib=0.004, hp=120),
    "strings_dark": lambda r, m, d: _i_saws(r, m, d, voices=4, det=0.006, lp=1500, atk=0.4, rel=0.6, vib=0.003),
    "stacc": lambda r, m, d: _i_saws(r, m, d, voices=3, det=0.006, lp=2600, atk=0.006, dec=0.09, sus=0.0, rel=0.05, hp=90),
    "stab": lambda r, m, d: _i_saws(r, m, d, voices=5, det=0.014, lp=3200, atk=0.003, dec=0.16, sus=0.15, rel=0.06),
    "pluck": lambda r, m, d: _i_add(r, m, d, decay=0.25, bright=1.0),
    "pluck_soft": lambda r, m, d: _i_add(r, m, d, decay=0.35, bright=0.5, kmax=20),
    "pluck_dark": lambda r, m, d: _i_add(r, m, d, decay=0.5, bright=0.4, kmax=16),
    "pluckbass": lambda r, m, d: _i_add(r, m, d, decay=0.22, bright=0.35, kmax=24, floor=0.25, sub=0.6, fmax=4000),
    "lead_sq": lambda r, m, d: _i_pulse(r, m, d, duty=0.3, vib=0.006, atk=0.01, dec=0.4, sus=0.6, fmax=7000),
    "lead_sq50": lambda r, m, d: _i_pulse(r, m, d, duty=0.5, vib=0.005, atk=0.005, dec=0.3, sus=0.55, fmax=7000),
    "chip": lambda r, m, d: _i_pulse(r, m, d, duty=0.25, fmax=12000, atk=0.001, dec=0.12, sus=0.5, rel=0.02),
    "chip_lead": lambda r, m, d: _i_pulse(r, m, d, duty=0.5, fmax=12000, vib=0.01, atk=0.002, rel=0.03),
    "tri": _i_tri,
    "organ": _i_organ,
    "epiano": _i_epiano,
    "bell": _i_bell,
    "marimba": _i_marimba,
    "guitar": lambda r, m, d: _i_ks(r, m, d, g=0.996, bright=0.55, rel=0.12),
    "pizz": lambda r, m, d: _i_ks(r, m, d, g=0.982, bright=0.4, rel=0.05),
    "choir": _i_choir,
    "brass": _i_brass,
    "braam": lambda r, m, d: _i_brass(r, m, d, atk=0.15, bmax=14, drive=2.5, rel=0.4),
    "tuba": lambda r, m, d: _i_brass(r, m, d, atk=0.02, bmax=4, rel=0.05, fmax=3000),
    "808": _i_808,
    "808dist": lambda r, m, d: _i_808(r, m, d, drive=4.0),
    "sub": _i_sub,
    "sawbass": _i_sawbass,
    "pulsebass": lambda r, m, d: _i_saws(r, m, d, voices=2, det=0.004, lp=650, atk=0.003, dec=0.09, sus=0.1, rel=0.03),
    "cowbell": _i_cowbell,
    "whistle": _i_whistle,
}

# ------------------------------------------------------------------ melody rhythm templates (2 bars of 16ths)
R_POP = ["x-x-x---x-x.x---" "x-x-x---x-------", "..x-x-x-x---x-x-" "x-------........",
         "x--x--x-x---x---" "x--x--x-x-x-x---", "x-x-..x-x-x-x---" "x-x-..x-x-------"]
R_SPARSE = ["x---x---x-------" "..x-x---x-------", "x-------..x-x---" "x---------------",
            "..x---x---x-x---" "x-------x-------"]
R_DENSE = ["x.xx.x.xx.x.x.x." "x.xx.x.xx.xx.x.x", "xx.x.xx.x.xx.x.x" "xx.x.xx.x.x.x.x.",
           "x.x.xx.xx.x.xx.x" "x.x.xx.xx.x.x---"]
R_SLOW = ["x-------x---x---" "x---------------", "x---x---x-------" "x---x---x---x---",
          "x-----x-x-------" "x-----x---x-----"]
R_BOUNCE = ["x.x.x...x.x.x..." "x.x.x.x.x---....", "x..x..x.x..x..x." "x.x.x.x.x-------",
            ".x.x.x.x..x.x..." ".x.x.x.xx-------"]
PAD1 = ["X---------------"]


def _L(name, pat, gain, mn=1, mx=9):
    return (name, pat, gain, mn, mx)


STYLES = {
    # ---------------------------------------------------------------- board: energetic, confident
    "synthdrive": dict(
        series="board", bpm=(114, 122), mode=["minor"], sev=False, chord_beats=4, kit="punch",
        progs=[[0, 5, 2, 6], [0, 3, 5, 6], [5, 6, 0, 0], [0, 6, 5, 6]],
        drums=[[_L("k", "X...X...X...X...", 1), _L("s", "....X.......X...", .7, 2), _L("c", "....X.......X...", .45, 2),
                _L("h", "..x...x...x...x.", .32), _L("h", "o.g.o.g.o.g.o.g.", .2, 3), _L("o", "..............x.", .2, 3)],
               [_L("k", "X...X...X...X...", 1), _L("s", "....X.......X...", .7, 2), _L("c", "....X.......X..x", .45, 2),
                _L("h", "x.x.x.x.x.x.x.x.", .28), _L("o", "..x...x...x...x.", .16, 3)]],
        parts=[dict(kind="bass", inst="sawbass", pats=["R.R.R.O.R.R.R.O.", "R.RRR.O.R.RRR.OR", "R-R-R-O-R-R-R-O-"], lo=33, gain=.5, gate=.85),
               dict(kind="chords", inst="pad", pats=PAD1, center=60, gain=.09, send=.35, spread=.6),
               dict(kind="arp", inst="pluck", pats=["0121012101210121", "0120120120120120", "0123210123210123"], center=64,
                    gain=.1, min_e=2, send=.25, bus="lead", alt=True, gate=.8),
               dict(kind="lead", inst="lead_sq", rhythms=R_POP, lo=67, hi=84, gain=.13, min_e=3, send=.3)],
        duck=(.7, .12), rev=(1.6, .3), delay=(.75, .35, .3), fill="s", impact=.8, end="hit",
        desc="driving synthwave: pumping saw bass, wide supersaw pads, echoing 16th plucks, square-lead hook"),
    "house": dict(
        series="board", bpm=(121, 126), mode=["dorian", "minor"], sev=True, chord_beats=4, kit="house", swing=.54,
        progs=[[0, 0, 3, 3], [0, 5, 6, 3], [0, 3, 6, 4], [5, 6, 0, 0]],
        drums=[[_L("k", "X...X...X...X...", 1), _L("c", "....X.......X...", .6, 2), _L("o", "..X...X...X...X.", .22),
                _L("sh", "xoxoxoxoxoxoxoxo", .17, 2), _L("r", "...x..x.....x...", .22, 3)],
               [_L("k", "X...X...X...X...", 1), _L("c", "....X.......X...", .6, 2), _L("o", "..X...X...X...X.", .22),
                _L("h", "x.xxx.xxx.xxx.xx", .14, 2), _L("c", "...........x....", .22, 3)]],
        parts=[dict(kind="bass", inst="pluckbass", pats=["..R...R...R...R.", "..R-..R-..R-..O.", "..R..RR...R..RO."], lo=36, gain=.5, gate=.7),
               dict(kind="chords", inst="organ", pats=["X..X..X...X..X..", "..X..X..X..X..X.", "X..X...X..X..X.."], center=62,
                    gain=.085, send=.3, spread=.4, gate=.8),
               dict(kind="chords", inst="pad_soft", pats=PAD1, center=57, gain=.05, min_e=3, send=.4, spread=.8),
               dict(kind="lead", inst="pluck", rhythms=R_POP, lo=69, hi=86, gain=.1, min_e=3, send=.3)],
        duck=(.75, .13), rev=(1.4, .25), delay=(.75, .3, .25), fill="c", impact=.8, end="hit",
        desc="four-on-the-floor house: offbeat open hats, shaker, syncopated organ 7th-chord stabs, rolling offbeat bass"),
    "electro": dict(
        series="board", bpm=(124, 130), mode=["major"], sev=False, chord_beats=4, kit="punch",
        progs=[[0, 4, 5, 3], [5, 3, 0, 4], [0, 5, 3, 4], [3, 4, 5, 5]],
        drums=[[_L("k", "X...X...X...X...", 1), _L("c", "....X.......X...", .7, 2), _L("s", "....X.......X...", .4, 2),
                _L("h", "..x...x...x...x.", .3), _L("h", "g.g.g.g.g.g.g.g.", .18, 3), _L("o", "..............X.", .22, 2)],
               [_L("k", "X...X...X...X.X.", 1), _L("c", "....X.......X...", .7, 2), _L("s", "....X.......X...", .4, 2),
                _L("h", "x.x.x.x.x.x.x.x.", .26), _L("o", "......X.......X.", .2, 3)]],
        parts=[dict(kind="bass", inst="sawbass", pats=["R.O.R.O.R.O.R.O.", "ROR.ROR.ROR.ROR.", "R.ORR.ORR.ORR.OR"], lo=33, gain=.48, gate=.7),
               dict(kind="chords", inst="stab", pats=["..X...X...X...X.", "..X..X..X...X.X."], center=64, gain=.09, send=.3, spread=.5),
               dict(kind="chords", inst="pad", pats=PAD1, center=60, gain=.05, min_e=2, send=.35, spread=.7),
               dict(kind="lead", inst="lead_sq50", rhythms=R_POP + R_BOUNCE, lo=69, hi=86, gain=.12, min_e=3, send=.25)],
        duck=(.8, .12), rev=(1.4, .25), delay=(.5, .3, .25), fill="s", impact=.8, end="hit",
        desc="bright electro-pop in major: bouncing octave bass, sidechained supersaw offbeat stabs, square-wave hook"),
    # ---------------------------------------------------------------- hot: dark, tense, half-time / trap
    "trap": dict(
        series="hot", bpm=(138, 150), mode=["harm", "minor"], sev=False, chord_beats=4, kit="trap",
        progs=[[0, 0, 5, 5], [0, 0, 3, 4], [0, 5, 3, 4], [0, 3, 5, 4]],
        drums=[[_L("k", "@bass", 1), _L("s", "........X.......", .8), _L("c", "........X.......", .45, 2),
                _L("h", "@trap", .26), _L("o", "..............x.", .18, 3)],
               [_L("k", "@bass", 1), _L("s", "........X.......", .8), _L("c", "........X......x", .45, 2),
                _L("h", "@trap", .26), _L("o", "......x.........", .18, 3)]],
        parts=[dict(kind="bass", inst="808", pats=["R------R--R-----", "R-----R---R--R--", "R-------R-g-----"], lo=30, gain=.42, gate=1.0),
               dict(kind="chords", inst="strings_dark", pats=PAD1, center=55, gain=.06, send=.4, spread=.6),
               dict(kind="arp", inst="bell", pats=["0.2.1.4.....2.1.", "0..2..1..3..2.1.", "0.1.2.4.2.1.0..."], center=72,
                    gain=.08, min_e=2, send=.4, bus="lead"),
               dict(kind="lead", inst="pluck_dark", rhythms=R_SPARSE, lo=64, hi=79, gain=.1, min_e=3, send=.35)],
        duck=(.35, .1), rev=(2.0, .3), delay=(.75, .35, .3), fill="trap", impact=.9, end="hit",
        desc="half-time trap: gliding 808s locked to the kick, hi-hat rolls, harmonic-minor bell loop, dark string bed"),
    "phonk": dict(
        series="hot", bpm=(125, 135), mode=["phrygian"], sev=False, chord_beats=4, kit="trap",
        progs=[[0, 0, 1, 0], [0, 5, 1, 0], [0, 3, 1, 0]],
        drums=[[_L("k", "@bass", 1), _L("c", "....X.......X...", .75), _L("h", "x.x.x.x.x.x.x.x.", .28),
                _L("h", ".o.o.o.o.o.o.o.o", .14, 3), _L("o", "......x.......x.", .16, 2)],
               [_L("k", "@bass", 1), _L("c", "....X.......X..x", .75), _L("h", "x.x.x.x.x.x.x.x.", .28),
                _L("h", ".o.o.o.o.o.o.o.o", .14, 3), _L("o", "..............x.", .16, 2)]],
        parts=[dict(kind="bass", inst="808dist", pats=["R-----R---R--R--", "R-------R-R---R-", "R-----R--R--g---"], lo=30, gain=.4, gate=1.0),
               dict(kind="lead", inst="cowbell", rhythms=R_DENSE, lo=72, hi=86, gain=.12, min_e=2, send=.15, bus="music", gate=.7),
               dict(kind="chords", inst="strings_dark", pats=PAD1, center=55, gain=.045, min_e=3, send=.4, spread=.6)],
        duck=(.3, .1), rev=(1.2, .2), delay=None, fill="trap", impact=.9, end="hit",
        desc="dark phonk: distorted 808, tuned-cowbell phrygian riff, backbeat claps, driving hats"),
    "tension": dict(
        series="hot", bpm=(96, 108), mode=["phrygian", "minor"], sev=False, chord_beats=8, kit="cine",
        progs=[[0, 5], [0, 3], [0, 5, 3, 0]],
        drums=[[_L("k", "X.....X.........", 1), _L("s", "........X.......", .75, 2), _L("tk", "x.x.x.x.x.x.x.x.", .25),
                _L("h", "..x...x...x...x.", .16, 3), _L("t1", "..........x..x.x", .35, 3)],
               [_L("k", "X.......X.x.....", 1), _L("s", "........X.......", .75, 2), _L("tk", "x.o.x.o.x.o.x.o.", .25),
                _L("h", "xoxoxoxoxoxoxoxo", .12, 3), _L("t3", "............x.x.", .35, 3)]],
        parts=[dict(kind="bass", inst="pulsebass", pats=["RRRRRRRRRRRRRRRR", "R.RRR.RRR.RRR.RR", "RRRORRRORRRORRRO"], lo=33, gain=.4, gate=.6),
               dict(kind="chords", inst="strings", pats=["X" + "-" * 31], center=57, gain=.075, send=.45, spread=.6),
               dict(kind="lead", inst="bell", rhythms=R_SPARSE, lo=72, hi=88, gain=.07, min_e=3, send=.5)],
        duck=(.4, .15), rev=(2.4, .35), delay=(.75, .35, .3), fill="tom", impact=1.0, end="hit",
        desc="tense dark pulse: 16th-note low ostinato, ticking clock, half-time kick/snare, long phrygian strings, eerie bells"),
    # ---------------------------------------------------------------- report: calm, warm, steady
    "lofi": dict(
        series="report", bpm=(78, 88), mode=["major", "dorian"], sev=True, chord_beats=4, kit="lofi", swing=.6, human=.006,
        progs=[[1, 4, 0, 5], [3, 2, 1, 0], [0, 5, 1, 4], [3, 4, 2, 5]],
        drums=[[_L("k", "X.....x...X.....", .9), _L("s", "....X.......X...", .6), _L("h", "x.o.x.o.x.o.x.oo", .22),
                _L("r", "..........g.....", .2, 2)],
               [_L("k", "X......xX.......", .9), _L("s", "....X.......X..g", .6), _L("h", "x.x.x.x.x.x.x.x.", .2),
                _L("sh", "..x...x...x...x.", .12, 3)]],
        parts=[dict(kind="bass", inst="sub", pats=["R------.R---F---", "R-----R-----F---", "R--------R--O---"], lo=36, gain=.5, gate=.9),
               dict(kind="chords", inst="epiano", pats=["X-------x-------", "X------x--x-----", "X---------x-----"], center=60,
                    gain=.1, send=.3, spread=.4, strum=.015),
               dict(kind="lead", inst="epiano", rhythms=R_SPARSE, lo=70, hi=84, gain=.09, min_e=3, send=.3)],
        duck=(.25, .15), rev=(1.8, .3), delay=(.75, .3, .25), fill="none", impact=.35, end="soft", crackle=True, lp=9000,
        desc="lo-fi chill hop: swung dusty drums, FM e-piano 7th chords, round sub bass, vinyl crackle"),
    "acoustic": dict(
        series="report", bpm=(94, 104), mode=["major"], sev=False, chord_beats=4, kit="acoustic", swing=.53,
        progs=[[0, 3, 5, 4], [0, 2, 3, 4], [3, 0, 4, 5], [0, 4, 3, 3]],
        drums=[[_L("k", "X.......X.....x.", .9), _L("c", "....X.......X...", .45, 2), _L("sn", "....X.......X...", .35),
                _L("sh", "xoxoxoxoxoxoxoxo", .15)],
               [_L("k", "X.....X.X.......", .9), _L("c", "....X.......X...", .45, 2), _L("sn", "....X.......X...", .35),
                _L("sh", "xoxoxoxoxoxoxoxo", .15), _L("t2", "..............x.", .28, 3)]],
        parts=[dict(kind="chords", inst="guitar", pats=["X.x.x-x.X.x.x-x.", "X..xX.x.X..xX.x.", "X.x.X.x.X.xxX.x."], center=57,
                    gain=.075, send=.25, spread=.5, strum=.012, add_oct=True, gate=1.0),
               dict(kind="bass", inst="pluckbass", pats=["R.......F...R...", "R.....R.F.......", "R...R...F...O..."], lo=36, gain=.45, gate=.8),
               dict(kind="chords", inst="pad_soft", pats=PAD1, center=60, gain=.04, min_e=2, send=.4, spread=.8),
               dict(kind="lead", inst="whistle", rhythms=R_POP, lo=72, hi=86, gain=.075, min_e=3, send=.3)],
        duck=(.2, .1), rev=(1.6, .25), delay=(.75, .3, .25), fill="sn", impact=.5, end="soft",
        desc="warm acoustic pop: strummed Karplus-Strong guitar, stomp kick + snaps, shaker, soft whistle melody"),
    "downtempo": dict(
        series="report", bpm=(98, 108), mode=["major", "lydian"], sev=True, chord_beats=4, kit="soft",
        progs=[[0, 3, 5, 4], [3, 4, 2, 5], [0, 5, 3, 3], [0, 2, 3, 4]],
        drums=[[_L("k", "X...X...X...X...", .85), _L("r", "....x.......x...", .3), _L("sh", "..x...x...x...x.", .18),
                _L("h", "xoxoxoxoxoxoxoxo", .1, 3)],
               [_L("k", "X...X...X...X...", .85), _L("c", "....x.......x...", .35, 2), _L("o", "..x...x...x...x.", .12, 2),
                _L("sh", "xoxoxoxoxoxoxoxo", .14, 3)]],
        parts=[dict(kind="bass", inst="sub", pats=["R-------R---F---", "R---------R-----", "R-----R-----O---"], lo=36, gain=.5),
               dict(kind="chords", inst="pad_warm", pats=PAD1, center=62, gain=.08, send=.35, spread=.7),
               dict(kind="arp", inst="marimba", pats=["0.1.2.1.0.2.1.2.", "0..1..2.3..2..1.", "2.1.0.1.2.3.2.1."], center=67,
                    gain=.1, min_e=2, send=.3, bus="lead", alt=True),
               dict(kind="lead", inst="pluck_soft", rhythms=R_SPARSE, lo=72, hi=86, gain=.1, min_e=3, send=.35)],
        duck=(.6, .18), rev=(2.0, .3), delay=(.75, .3, .25), fill="r", impact=.5, end="soft",
        desc="warm downtempo: soft four-on-the-floor, pumping warm pads with 7ths, mallet arps, sub bass"),
    # ---------------------------------------------------------------- preview: cinematic build
    "epic": dict(
        series="preview", bpm=(88, 100), mode=["minor", "harm"], sev=False, chord_beats=4, kit="cine", arc="build",
        progs=[[0, 5, 2, 6], [0, 5, 3, 4], [0, 3, 5, 6], [5, 3, 0, 4]],
        drums=[[_L("k", "X.......X.......", 1), _L("t1", "X..X..X.X..X..X.", .4, 1), _L("t2", "....x.......x.xx", .4, 2),
                _L("s", "....X.......X...", .6, 3), _L("t3", "x.x.x.x.x.x.x.x.", .2, 3)],
               [_L("k", "X.....X.X.......", 1), _L("t1", "X..X..X...X..X..", .4, 2), _L("t2", "......x.....x.x.", .4, 2),
                _L("s", "....X.......X...", .6, 3), _L("tk", "x.x.x.x.x.x.x.x.", .15)]],
        parts=[dict(kind="arp", inst="stacc", pats=["0020102000201020", "0101020201010202", "0010201000102010"], center=50,
                    gain=.12, send=.3, alt=True, gate=.9),
               dict(kind="chords", inst="strings", pats=PAD1, center=60, gain=.07, send=.45, spread=.7),
               dict(kind="chords", inst="choir", pats=PAD1, center=67, gain=.06, min_e=3, send=.5, spread=.8),
               dict(kind="bass", inst="sub", pats=["R---------------"], lo=33, gain=.45, min_e=2),
               dict(kind="lead", inst="brass", rhythms=R_SLOW, lo=60, hi=76, gain=.14, min_e=3, send=.45)],
        duck=(.3, .2), rev=(2.6, .35), delay=None, fill="tom", impact=1.0, end="hit", riser=True,
        desc="cinematic build: staccato string ostinato, taiko-style toms, booms, choir and brass that join section by section"),
    "hybrid": dict(
        series="preview", bpm=(118, 126), mode=["phrygian", "minor"], sev=False, chord_beats=8, kit="hybrid", arc="build",
        progs=[[0, 5], [0, 3], [0, 5, 3, 0]],
        drums=[[_L("k", "X.......X.......", 1), _L("k", "....X.......X...", .8, 3), _L("c", "....X.......X...", .55, 3),
                _L("s", "....X.......X...", .45, 3), _L("tk", "x.x.x.x.x.x.x.x.", .22), _L("h", "..x...x...x...x.", .2, 2),
                _L("t1", "......x.x.....x.", .4, 2)],
               [_L("k", "X.......X.......", 1), _L("k", "....X.......X...", .8, 3), _L("c", "....X.......X...", .55, 3),
                _L("s", "....X.......X...", .45, 3), _L("tk", "xoxoxoxoxoxoxoxo", .2), _L("h", "..x...x...x...x.", .2, 2),
                _L("t3", "..........x.x.x.", .4, 2)]],
        parts=[dict(kind="bass", inst="pulsebass", pats=["R.R.R.R.R.R.R.R.", "RRR.RRR.RRR.RRR.", "R.RRR.RRR.RRR.RR"], lo=33, gain=.45, gate=.7),
               dict(kind="chords", inst="strings", pats=PAD1, center=57, gain=.06, send=.45, spread=.7),
               dict(kind="chords", inst="braam", pats=["X" + "-" * 31], center=45, gain=.06, min_e=2, send=.4, spread=.3),
               dict(kind="arp", inst="pluck", pats=["0213021302130213", "0123012301230123"], center=69, gain=.09, min_e=3,
                    send=.3, bus="lead", gate=.8)],
        duck=(.6, .12), rev=(2.2, .3), delay=(.75, .3, .25), fill="tom", impact=1.0, end="hit", riser=True,
        desc="hybrid trailer: pulsing 8th bass, ticking clock, distorted brass braams, punchy kick that goes four-on-the-floor"),
    "anthem": dict(
        series="preview", bpm=(118, 126), mode=["major"], sev=False, chord_beats=4, kit="punch", arc="build",
        progs=[[0, 4, 5, 3], [5, 3, 0, 4], [0, 3, 5, 4], [3, 0, 4, 5]],
        drums=[[_L("k", "X.......X.......", 1, 1, 1), _L("k", "X...X...X...X...", 1, 2), _L("c", "....X.......X...", .75),
                _L("s", "....X.......X...", .4, 2), _L("t1", "..............xx", .4, 3), _L("h", "..x...x...x...x.", .25, 2),
                _L("o", "..............X.", .2, 3)],
               [_L("k", "X.......X.......", 1, 1, 1), _L("k", "X...X...X...X...", 1, 2), _L("c", "....X.......X...", .75),
                _L("s", "....X.......X...", .4, 2), _L("t2", "X.....x.........", .35, 3), _L("h", "x.x.x.x.x.x.x.x.", .22, 2)]],
        parts=[dict(kind="bass", inst="sub", pats=["R-------R-------"], lo=36, gain=.45, max_e=1),
               dict(kind="bass", inst="sawbass", pats=["R.R.R.R.R.R.R.R.", "R.R.R.RRR.R.R.RO"], lo=33, gain=.45, min_e=2, gate=.8),
               dict(kind="chords", inst="choir", pats=PAD1, center=64, gain=.07, send=.45, spread=.8),
               dict(kind="chords", inst="pad", pats=PAD1, center=60, gain=.05, min_e=2, send=.35, spread=.6),
               dict(kind="lead", inst="brass", rhythms=R_SLOW + R_BOUNCE, lo=64, hi=79, gain=.14, min_e=3, send=.4)],
        duck=(.55, .15), rev=(2.2, .3), delay=None, fill="tom", impact=.9, end="hit", riser=True,
        desc="stadium anthem in major: big claps, choir pad, kick that opens to four-on-the-floor, heroic brass melody"),
    # ---------------------------------------------------------------- lab: light, curious
    "marimba": dict(
        series="lab", bpm=(100, 112), mode=["major", "lydian"], sev=False, chord_beats=4, kit="soft", swing=.52,
        progs=[[0, 3, 4, 0], [0, 5, 1, 4], [0, 2, 3, 4], [3, 4, 0, 0]],
        drums=[[_L("k", "X.......X.x.....", .8), _L("sn", "....X.......X...", .45), _L("sh", "x.x.x.x.x.x.x.x.", .15, 2),
                _L("w", "......x.......x.", .22, 3)],
               [_L("k", "X.....X...X.....", .8), _L("sn", "....X.......X...", .45), _L("sh", "xoxoxoxoxoxoxoxo", .13, 2),
                _L("w", "..x.....x..x....", .2, 3)]],
        parts=[dict(kind="arp", inst="marimba", pats=["0.1.2.1.0.2.1.3.", "0.2.1.2.0.3.2.1.", "3.2.1.0.1.2.3.2."], center=64,
                    gain=.12, send=.25, alt=True),
               dict(kind="bass", inst="pluckbass", pats=["R...F...R...F.O.", "R..R..F.R...F...", "R.R...F.R.R...O."], lo=36, gain=.45, gate=.5),
               dict(kind="chords", inst="pad_soft", pats=PAD1, center=60, gain=.04, min_e=2, send=.4, spread=.8),
               dict(kind="lead", inst="bell", rhythms=R_BOUNCE, lo=72, hi=88, gain=.085, min_e=3, send=.3)],
        duck=(.2, .1), rev=(1.4, .25), delay=(.75, .3, .25), fill="w", impact=.5, end="hit",
        desc="playful marimba pop: bouncing mallet arps, finger snaps, plucked bass, glockenspiel tune"),
    "chiptune": dict(
        series="lab", bpm=(120, 136), mode=["major", "mixo"], sev=False, chord_beats=4, kit="chip",
        progs=[[0, 5, 3, 4], [0, 3, 0, 4], [5, 3, 4, 0], [0, 4, 5, 3]],
        drums=[[_L("k", "X.......X.X.....", .9), _L("n", "....X.......X...", .6), _L("nh", "x.x.x.x.x.x.x.x.", .18, 2),
                _L("nh", "..x...x...x...x.", .1, 1, 1)],
               [_L("k", "X...X...X...X...", .9), _L("n", "....X.......X..X", .55), _L("nh", "xxxxxxxxxxxxxxxx", .12, 2)]],
        parts=[dict(kind="arp", inst="chip", pats=["0123" * 8, "0213" * 8, "01240124" * 4], center=64, gain=.085, bars=1, send=.15),
               dict(kind="bass", inst="tri", pats=["R.R.O.R.R.R.O.R.", "R.O.R.O.R.O.R.O.", "R...R.O.R...R.O."], lo=36, gain=.5, gate=.7),
               dict(kind="lead", inst="chip_lead", rhythms=R_BOUNCE + R_POP, lo=72, hi=88, gain=.1, min_e=3, send=.15)],
        duck=(.15, .08), rev=(0.8, .12), delay=(.75, .3, .2), fill="n", impact=.5, end="hit",
        desc="8-bit chiptune: fast pulse-wave arpeggios, triangle bass, noise drums, square lead"),
    "pizz": dict(
        series="lab", bpm=(92, 104), mode=["dorian", "minor"], sev=False, chord_beats=4, kit="soft",
        progs=[[0, 3, 0, 4], [0, 3, 5, 4], [0, 5, 3, 4], [0, 0, 3, 3]],
        drums=[[_L("w", "x..x..x...x.x...", .22), _L("k", "X.......X.......", .7, 2), _L("sn", "....x.......x...", .4, 2),
                _L("sh", "..x...x...x...x.", .12, 3)],
               [_L("w", "x.x...x.x.x.....", .22), _L("k", "X.....X.X.......", .7, 2), _L("sn", "....x.......x...", .4, 2),
                _L("sh", "xoxoxoxoxoxoxoxo", .1, 3)]],
        parts=[dict(kind="arp", inst="pizz", pats=["0.2.1.2.3.2.1.2.", "0...2.1.0...2.1.", "0.1.2...3.2.1..."], center=62,
                    gain=.13, send=.3, alt=True),
               dict(kind="bass", inst="tuba", pats=["R.......F.......", "R...F...R...F...", "R.....F.R...F..."], lo=36, gain=.45, gate=.5),
               dict(kind="chords", inst="pad_soft", pats=PAD1, center=60, gain=.035, min_e=2, send=.4, spread=.8),
               dict(kind="lead", inst="bell", rhythms=R_BOUNCE, lo=74, hi=90, gain=.08, min_e=3, send=.35)],
        duck=(.1, .1), rev=(1.5, .28), delay=(.75, .3, .25), fill="w", impact=.45, end="hit",
        desc="curious pizzicato: plucked string arps, staccato tuba bass, woodblock, glockenspiel melody"),
}

SERIES_STYLES = {}
for _name, _sp in STYLES.items():
    SERIES_STYLES.setdefault(_sp["series"], []).append(_name)


_PART_GAIN = {"bass": 1.5, "chords": 3.0, "arp": 3.0, "lead": 2.5}   # mix balance between the part types


def _parse(pat):
    """[(step, char, length_in_steps)] for a pattern string ('-' ties, '.' rest)."""
    out = []
    L = len(pat)
    for i, ch in enumerate(pat):
        if ch in ".-":
            continue
        j = i + 1
        while j < L and pat[j] == "-":
            j += 1
        out.append((i, ch, j - i))
    return out


def _pat_bars(pat, explicit=None):
    return explicit or (2 if len(pat) in (32, 48) else 1)


class _Song:
    BUSES = ("drums", "bass", "music", "lead", "fx", "final", "send")

    def __init__(self, style, duration, cues, seed):
        sp = STYLES[style]
        self.sp = sp
        self.r = r = np.random.default_rng([_seed_int(seed), zlib.crc32(style.encode())])
        self.bpm = int(r.integers(sp["bpm"][0], sp["bpm"][1] + 1))
        self.beat = 60.0 / self.bpm
        self.bar = 4 * self.beat
        self.dur = float(duration)
        self.n = int(round(duration * SR))
        self.N = self.n + SR
        self.b = {k: np.zeros((self.N, 2)) for k in self.BUSES}
        self.kicks = []
        self.mode = sp["mode"][int(r.integers(len(sp["mode"])))]
        self.scale = MODES[self.mode]
        self.key = int(r.integers(0, 12))
        progs = sp["progs"]
        self.progA = progs[int(r.integers(len(progs)))]
        self.progB = progs[int(r.integers(len(progs)))]
        self.dvar = [int(i) for i in r.permutation(len(sp["drums"]))]
        self.pvar = [int(r.integers(len(p["pats"]))) if "pats" in p else 0 for p in sp["parts"]]
        self.leads = {i: self._make_lead(p) for i, p in enumerate(sp["parts"]) if p["kind"] == "lead"}
        self.traphats = [self._trap_hats() for _ in range(2)]
        self.cache, self.dcache, self.vcache = {}, {}, {}
        self._plan(cues)

    # -------------------------------------------------------------- helpers
    def note(self, inst, m, dur):
        dur = max(0.03, round(dur, 2))
        k = (inst, int(m), dur)
        if k not in self.cache:
            self.cache[k] = INSTRUMENTS[inst](self.r, int(m), dur)
        return self.cache[k]

    def drum(self, lane):
        if lane not in self.dcache:
            self.dcache[lane] = _make_drum(self.r, self.sp["kit"], lane)
        return self.dcache[lane]

    def put(self, bus, x, t, g=1.0, pan=0.0, end=None, send=0.0):
        i = int(round(t * SR))
        if i >= self.N or g == 0:
            return
        if i < 0:
            x, i = x[-i:], 0
        if end is not None:
            k = int((end - t) * SR)
            f = int(0.012 * SR)
            if k <= 0:
                return
            if len(x) > k + f:
                x = x[:k + f].copy()
                x[k:] *= np.linspace(1, 0, f)
        j = min(self.N, i + len(x))
        a = np.pi / 4 * (pan + 1)
        gl, gr = np.cos(a) * np.sqrt(2) * g, np.sin(a) * np.sqrt(2) * g
        seg = x[: j - i]
        self.b[bus][i:j, 0] += seg * gl
        self.b[bus][i:j, 1] += seg * gr
        if send:
            self.b["send"][i:j, 0] += seg * gl * send
            self.b["send"][i:j, 1] += seg * gr * send

    def d2m(self, d):
        return 48 + self.key + self.scale[d % 7] + 12 * (d // 7)

    def m2d(self, m):
        return min(range(-21, 50), key=lambda d: abs(self.d2m(d) - m))

    def tones(self, deg):
        return {(deg + o) % 7 for o in ((0, 2, 4, 6) if self.sp.get("sev") else (0, 2, 4))}

    def snap(self, d, deg):
        ts = self.tones(deg)
        for o in (0, -1, 1, -2, 2, 3, -3):
            if (d + o) % 7 in ts:
                return d + o
        return d

    def voicing(self, deg, center):
        k = (deg, center)
        if k in self.vcache:
            return self.vcache[k]
        tones = [deg, deg + 2, deg + 4] + ([deg + 6] if self.sp.get("sev") else [])
        pcs = [(self.key + self.scale[t % 7]) % 12 for t in tones]
        best = None
        for inv in range(len(pcs)):
            rot = pcs[inv:] + pcs[:inv]
            for o in range(2, 8):
                notes = [o * 12 + rot[0]]
                for pc in rot[1:]:
                    notes.append(notes[-1] + ((pc - notes[-1]) % 12 or 12))
                c = abs(np.mean(notes) - center) + 0.01 * inv
                if best is None or c < best[0]:
                    best = (c, notes)
        self.vcache[k] = best[1]
        return best[1]

    def root(self, deg, lo):
        pc = (self.key + self.scale[deg % 7]) % 12
        return lo + (pc - lo) % 12

    def chord_at(self, prog, s0, t):
        cl = self.sp.get("chord_beats", 4) * self.beat
        return prog[int((t - s0 + 1e-6) // cl) % len(prog)]

    def swing(self, i, L, step):
        s = self.sp.get("swing", 0.5)
        if s != 0.5 and L in (16, 32) and i % 2 == 1:
            return (s - 0.5) * 2 * step
        return 0.0

    def human(self):
        return float(self.r.normal(0, self.sp.get("human", 0.0015)))

    def _trap_hats(self):
        r = self.r
        h = list("x.x.x.x.x.x.x.x.")
        for i in range(1, 16, 2):
            if r.random() < 0.35:
                h[i] = "g"
        for i in r.choice([6, 10, 12, 13, 14, 15], size=2, replace=False):
            h[int(i)] = str(r.choice(["r", "R", "q"]))
        return "".join(h)

    def _make_lead(self, p):
        r = self.r
        rh = p["rhythms"][int(r.integers(len(p["rhythms"])))]
        ons = _parse(rh)
        steps = [-2, -1, -1, 0, 1, 1, 2, 2, 3, -3, 4, -4]
        ca = [int(r.choice(steps)) for _ in ons]
        cb = list(ca)
        for j in range(max(0, len(cb) - 3), len(cb)):
            cb[j] = int(r.choice(steps))
        return rh, ca, cb

    # -------------------------------------------------------------- arrangement
    def _plan(self, cues):
        d = self.dur
        cs = sorted({float(c) for c in cues if 0.5 <= float(c) <= d - 2.2})
        starts = [0.0] + cs
        last = starts[-1]
        lim = d - 1.5
        if lim <= last + 0.3:
            t_end = max(last + 0.3, d * 0.6)
        else:
            ks = int((lim - last) / self.beat + 1e-6)
            cand = [last + k * self.beat for k in range(1, ks + 1)]
            t_end = None
            for mult, win in ((4, 1.0), (2, 0.8), (1, 9.0)):
                ok = [t for k, t in enumerate(cand, 1) if k % mult == 0 and t >= lim - win]
                if ok:
                    t_end = ok[-1]
                    break
            if t_end is None:
                t_end = lim
        self.t_end = min(t_end, d)
        self.cues = cs
        self.sections = [(s, e) for s, e in zip(starts, starts[1:] + [self.t_end])]

    def energy(self, si, k):
        ns = len(self.sections)
        if self.sp.get("arc") == "build":
            e = 1 + si if ns > 1 else 1 + k // 4
            return min(3, e + k // 8)
        if si == 0:
            e = 2
        else:
            e = 3 if (si % 2 == 1 or si == ns - 1) else 2
        if (k // 8) % 2 == 1:
            e = 3 if e == 2 else 2
        return e

    # -------------------------------------------------------------- per bar
    def drums_bar(self, si, k, tb, s1, e, cut_t, mini_t):
        variants = self.sp["drums"]
        lanes = variants[self.dvar[si % len(variants)]]
        bass_pat = None
        for pi, p in enumerate(self.sp["parts"]):
            if p["kind"] == "bass" and p.get("min_e", 1) <= e <= p.get("max_e", 9):
                bass_pat = p["pats"][self.pvar[pi]]
                break
        for name, pat, g, mn, mx in lanes:
            if not mn <= e <= mx:
                continue
            if pat == "@trap":
                pat = self.traphats[si % 2]
            elif pat == "@bass":
                pat = "".join("X" if ch not in ".-" else "." for ch in (bass_pat or "X...X...X...X..."))
            bars = _pat_bars(pat)
            if k % bars:
                continue
            L = len(pat)
            step = bars * self.bar / L
            kick = name.startswith("k")
            backbeat = name in ("s", "c", "r", "sn", "n")
            for i, ch in enumerate(pat):
                if ch in ".-":
                    continue
                t = tb + i * step + self.swing(i, L, step) + self.human()
                if t >= s1 - 1e-3 or t < 0:
                    continue
                if cut_t is not None and t >= cut_t - 1e-3 and (kick or backbeat):
                    continue
                if mini_t is not None and t >= mini_t - 1e-3 and backbeat:
                    continue
                v = _VEL.get(ch, 0.7) * (1 + float(self.r.normal(0, 0.05)))
                sub = {"r": 2, "R": 3, "q": 4}.get(ch, 1)
                for j in range(sub):
                    self.hit(name, t + j * step / sub, g * v * (0.7 + 0.3 * j / max(1, sub - 1) if sub > 1 else 1))

    def hit(self, lane, t, g):
        self.put("drums", self.drum(lane), t, g, _PAN.get(lane, 0.0), send=_SEND.get(lane, 0.03))
        if lane.startswith("k") and g >= 0.45:
            self.kicks.append(t)

    def part_bar(self, pi, p, si, k, tb, s0, s1, e, prog, cut_t):
        if not p.get("min_e", 1) <= e <= p.get("max_e", 9):
            return
        kind = p["kind"]
        bus = p.get("bus", {"bass": "bass", "lead": "lead"}.get(kind, "music"))
        send = p.get("send", 0.0)
        p = dict(p, gain=p["gain"] * _PART_GAIN[kind])
        if kind == "lead":
            if k % 2 == 0:
                self.lead_phrase(pi, p, k // 2, tb, s0, s1, prog, bus, send)
            return
        vi = self.pvar[pi]
        if p.get("alt") and si % 2 == 1:
            vi = (vi + 1) % len(p["pats"])
        pat = p["pats"][vi]
        bars = _pat_bars(pat, p.get("bars"))
        if k % bars:
            return
        L = len(pat)
        step = bars * self.bar / L
        gate = p.get("gate", 0.92)
        end = s1
        if kind == "bass" and cut_t is not None:
            end = cut_t
        for i, ch, ln in _parse(pat):
            t = tb + i * step + self.swing(i, L, step)
            if t >= end - 1e-3:
                continue
            deg = self.chord_at(prog, s0, t)
            dur = ln * step * gate
            if kind == "bass":
                rt = self.root(deg, p["lo"])
                third = rt + ((self.d2m(deg + 2) - rt) % 12)
                m = {"R": rt, "O": rt + 12, "F": rt + 7, "f": rt - 5, "T": third, "g": rt}.get(ch, rt)
                x = self.note(p["inst"], m, dur) if ch != "g" else _i_808(self.r, m, round(dur, 2), glide=12,
                                                                        drive=4.0 if "dist" in p["inst"] else 2.2)
                self.put(bus, x, t + self.human(), p["gain"], 0.0, end=end, send=send)
            elif kind == "chords":
                v = _VEL.get(ch, 0.7) if ch in "Xx" else 1.0
                notes = list(self.voicing(deg, p["center"]))
                if p.get("add_oct"):
                    notes.append(notes[0] + 12)
                strum = p.get("strum", 0.0)
                up = strum and i % 2 == 1
                order = notes[::-1] if up else notes
                nv = len(order)
                sp = p.get("spread", 0.0)
                for j, m in enumerate(order):
                    pan = (j / (nv - 1) - 0.5) * sp if nv > 1 else 0.0
                    self.put(bus, self.note(p["inst"], m, dur), t + j * strum + self.human(),
                             p["gain"] * v * (0.8 if up else 1.0), pan, end=end, send=send)
            elif kind == "arp":
                vo = self.voicing(deg, p["center"])
                notes = list(vo) + [m + 12 for m in vo]
                m = notes[min(int(ch), len(notes) - 1)] if ch.isdigit() else notes[0]
                pan = 0.25 * np.sin(i * 1.7)
                self.put(bus, self.note(p["inst"], m, dur), t + self.human(), p["gain"], pan, end=end, send=send)

    def lead_phrase(self, pi, p, ph, tb, s0, s1, prog, bus, send):
        rh, ca, cb = self.leads[pi]
        cont = ca if ph % 2 == 0 else cb
        step = self.bar / 16
        lo, hi = p["lo"], p["hi"]
        center = self.m2d((lo + hi) / 2) + (2 if ph % 4 == 2 else 0)
        d = None
        gate = p.get("gate", 0.9)
        for (i, ch, ln), iv in zip(_parse(rh), cont):
            t = tb + i * step + self.swing(i, 32, step)
            if t >= s1 - 1e-3:
                break
            deg = self.chord_at(prog, s0, t)
            d = self.snap(center, deg) if d is None else d + iv
            if i % 4 == 0 or d is None:
                d = self.snap(d, deg)
            while self.d2m(d) > hi:
                d -= 7
            while self.d2m(d) < lo:
                d += 7
            self.put(bus, self.note(p["inst"], self.d2m(d), ln * step * gate), t + self.human(), p["gain"],
                     0.08, end=s1, send=send)

    def fill(self, t0, kind, g=1.0):
        if kind in (None, "none") or t0 < 0:
            return
        s16 = self.beat / 4
        if kind == "tom":
            for j, lane in enumerate(("t1", "t1", "t2", "t3")):
                self.hit(lane, t0 + j * s16, g * (0.55 + 0.15 * j))
        elif kind == "trap":
            for j in range(8):
                self.hit("h", t0 + j * s16 / 2, g * 0.3 * (0.5 + j / 14))
            self.hit("s", t0 + 3 * s16, g * 0.5)
        else:
            for j in range(4):
                self.hit(kind, t0 + j * s16, g * (0.35 + 0.18 * j) * (0.8 if kind in ("c", "s") else 0.6))

    # -------------------------------------------------------------- render
    def render(self):
        sp = self.sp
        r = self.r
        bar, beat = self.bar, self.beat
        ns = len(self.sections)
        for si, (s0, s1) in enumerate(self.sections):
            last = si == ns - 1
            nb = int(np.ceil((s1 - s0) / bar - 1e-6))
            for k in range(nb):
                tb = s0 + k * bar
                e = self.energy(si, k)
                prog = self.progB if e >= 3 else self.progA
                cut_t = mini_t = None
                if not last and s1 - tb <= bar + 1e-6 and s1 - s0 > beat * 1.5:
                    cut_t = s1 - beat
                elif k % 8 == 7 and tb + bar < s1 - 1e-3 and e >= 2:
                    mini_t = tb + bar - beat
                self.drums_bar(si, k, tb, s1, e, cut_t, mini_t)
                for pi, p in enumerate(sp["parts"]):
                    self.part_bar(pi, p, si, k, tb, s0, s1, e, prog, cut_t)
                if cut_t is not None:
                    self.fill(cut_t, sp.get("fill"))
                if mini_t is not None:
                    self.fill(mini_t, sp.get("fill"), 0.6)

        # intro hit, cue marks, ending
        imp = sp.get("impact", 0.8)
        crash = self.drum("cr")
        self.put("fx", crash, 0.0, 0.22, -0.1, send=0.2)
        self.put("fx", _d_impact(r, 0.6), 0.0, imp * 0.45, send=0.15)
        for c in self.cues:
            self.put("fx", _d_impact(r, 1.0), c, imp * 0.75, send=0.2)
            self.put("fx", crash, c, 0.28, 0.1, send=0.25)
            self.put("fx", _d_whoosh(r, 0.45), c - 0.43, 0.22, -0.2, send=0.3)
            if sp.get("riser") and c > 2.5:
                rl = min(2.5, bar)
                self.put("fx", _d_riser(r, rl), c - rl, 0.12, 0.15, send=0.3)
        te = self.t_end
        tail = max(0.3, self.dur - te)
        if te < self.dur - 0.2:
            deg0 = self.progA[0]
            chord_inst = next((p["inst"] for p in sp["parts"] if p["kind"] == "chords"), "pad")
            chord_inst = sp.get("final_inst", chord_inst if chord_inst not in ("guitar", "organ", "stab", "braam") else "pad_warm")
            for j, m in enumerate(self.voicing(deg0, 62)):
                self.put("final", self.note(chord_inst, m, tail * 0.8), te, 0.11, (j - 1) * 0.3, send=0.4)
            self.put("final", self.note("sub", self.root(deg0, 36), tail * 0.7), te, 0.4)
            if sp.get("end", "hit") == "hit":
                self.put("final", self.drum("k"), te, 0.9)
                self.put("final", crash, te, 0.3, send=0.3)
                self.put("final", _d_impact(r, 0.8), te, imp * 0.6, send=0.2)
            else:
                self.put("final", _i_bell(r, self.d2m(self.snap(self.m2d(79), deg0)), 1.4), te, 0.05, 0.2, send=0.5)

        B = self.b
        N = self.N
        t = np.arange(N) / SR
        # pre-cue "lift": the music thins out (high-pass crossfade) over the bar before each cue
        ramp = np.zeros(N)
        for c in self.cues:
            i0, i1 = int((c - bar) * SR), int(c * SR)
            i0 = max(0, i0)
            if i1 > i0:
                ramp[i0:i1] = np.maximum(ramp[i0:i1], np.linspace(0, 1, i1 - i0) ** 2)
        if ramp.any():
            for name, amt in (("music", 0.8), ("lead", 0.6), ("bass", 0.9)):
                hp = _st(_hp, B[name], 650)
                rr = (ramp * amt)[:, None]
                B[name] = B[name] * (1 - rr) + hp * rr
        # sidechain ducking from the kicks
        depth, rel = sp.get("duck", (0.5, 0.12))
        duck = np.ones(N)
        L = int(min(beat, 0.6) * SR)
        x = np.arange(L) / SR
        shape = 1 - depth * np.clip(x / 0.004, 0, 1) * np.exp(-np.clip(x - 0.004, 0, None) / rel)
        for tk in self.kicks:
            i = int(tk * SR)
            if 0 <= i < N:
                j = min(N, i + L)
                duck[i:j] = np.minimum(duck[i:j], shape[: j - i])
        d2 = duck[:, None]
        # lead delay (ping-pong)
        lead = B["lead"]
        if sp.get("delay"):
            db, fb, mix = sp["delay"]
            dn = int(db * beat * SR)
            mono = _lp(lead.mean(axis=1), 4500)
            wet = np.zeros((N, 2))
            for tap in range(1, 5):
                s = dn * tap
                if s >= N:
                    break
                wet[s:, (tap + 1) % 2] += mono[:-s] * mix * fb ** (tap - 1)
            lead = lead + wet
            B["send"][:, :] += wet * 0.3
        # reverb (decorrelated stereo IRs)
        dec, mixr = sp.get("rev", (1.6, 0.25))
        send = _hp(B["send"].mean(axis=1), 180)
        nir = int(dec * SR)
        wet = np.zeros((N, 2))
        pre = int(0.015 * SR)
        for ch in range(2):
            ir = r.standard_normal(nir) * np.exp(-np.arange(nir) / SR * (6.9 / dec))
            ir = _lp(ir, 6500)
            ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
            wet[pre:, ch] = signal.fftconvolve(send, ir)[: N - pre]
        wet *= mixr * 2.2
        mix = (B["drums"] * 0.9 + B["bass"] * (1 - (1 - d2) * 0.6) + B["music"] * d2 + lead * (1 - (1 - d2) * 0.5)
               + wet * (1 - (1 - d2) * 0.5) + B["fx"] + B["final"])
        mix = _st(_hp, mix, 30)
        if sp.get("lp"):
            mix = _st(_lp, mix, sp["lp"])
        if sp.get("crackle"):
            v = _vinyl(r, N)
            mix += np.stack([v, np.roll(v, 37)], 1) * 0.6 * np.max(np.abs(mix)) * 0.15
        mix = mix[: self.n]
        tt = t[: self.n]
        # ending: everything decays after the final hit, fades to exact silence at the end
        f0 = min(max(self.t_end + 0.35, self.dur - 1.1), self.dur * 0.7)
        if f0 < self.dur:
            g = np.clip((tt - f0) / max(1e-3, self.dur - f0), 0, 1)
            mix *= (np.cos(g * np.pi / 2) ** 2)[:, None]
        fi = min(len(mix), int(0.004 * SR))
        mix[:fi] *= np.linspace(0, 1, fi)[:, None]
        # gentle limiting / glue, then peak normalise
        pk = np.percentile(np.abs(mix), 99.9) + 1e-9
        mix = np.tanh(mix / pk * 0.9)
        mix /= np.max(np.abs(mix)) + 1e-9
        return (mix * 0.89).astype(np.float32)


def style_track(style, duration, cues=(), seed=None):
    """Render one specific style (see STYLES / SERIES_STYLES). Same seed -> identical audio."""
    if style not in STYLES:
        raise ValueError(f"unknown style {style!r}; choose from {sorted(STYLES)}")
    return _Song(style, duration, list(cues or ()), seed).render()


def pick_style(series, seed=None):
    """The style series_track() would use for this series/seed. ISO-date seeds rotate through the styles
    day by day (consecutive days never repeat a style); other seeds pick by hash."""
    series = series if series in SERIES_STYLES else "board"
    styles = SERIES_STYLES[series]
    off = zlib.crc32(series.encode())
    if isinstance(seed, (str, datetime.date)):
        try:
            d = seed if isinstance(seed, datetime.date) else datetime.date.fromisoformat(str(seed)[:10])
            return styles[(d.toordinal() + off) % len(styles)]
        except ValueError:
            pass
    s = _seed_int(seed)
    return styles[(s ^ off) % len(styles)]


def series_track(series, duration, cues=(), seed=None):
    """Seeded original song for a video series ('board', 'hot', 'report', 'preview', 'lab'; unknown -> 'board').
    Returns float32 stereo (n, 2) at SR. seed: int or string (e.g. the date '2026-11-16'); None -> random."""
    if seed is None:
        seed = secrets.randbits(63)
    series = series if series in SERIES_STYLES else "board"
    if isinstance(seed, datetime.date):
        seed = seed.isoformat()
    style = pick_style(series, seed)
    return style_track(style, duration, cues, f"{series}|{seed}")


def write_wav(path, x):
    from scipy.io import wavfile
    wavfile.write(path, SR, (np.clip(x, -1, 1) * 32767).astype(np.int16))


if __name__ == "__main__":
    import sys
    write_wav(sys.argv[1] if len(sys.argv) > 1 else "test.wav", track(20, cues=[4, 12]))
