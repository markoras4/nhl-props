"""Original, royalty-free backing track for the preview videos.

Everything is synthesised from scratch with numpy/scipy (no samples, no
copyrighted material), so the track can be used on TikTok, Instagram and
YouTube without claims.

    track(duration, cues, bpm=120) -> float32 stereo array (n, 2) at SR

`cues` is a list of times (seconds) where a new section starts (each game);
they get an impact, a whoosh and a short riser leading into them.
"""
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

    # intro filter sweep: open the low-pass across the intro
    if intro > 0.5:
        i = int(intro * SR)
        seg = music[:i]
        lo = _lp(seg, 500)
        w = np.linspace(0, 1, i) ** 2
        music[:i] = lo * (1 - w) + seg * w

    for c in cues:
        _add(fx, impact() * 0.8, c)
        _add(fx, whoosh(0.45), max(0, c - 0.42))
    if intro > 1.2:
        _add(fx, riser(min(2.0, intro - 0.2)), intro - min(2.0, intro - 0.2))

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


def write_wav(path, x):
    from scipy.io import wavfile
    wavfile.write(path, SR, (np.clip(x, -1, 1) * 32767).astype(np.int16))


if __name__ == "__main__":
    import sys
    write_wav(sys.argv[1] if len(sys.argv) > 1 else "test.wav", track(20, cues=[4, 12]))
