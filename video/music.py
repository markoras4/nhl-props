"""Original, royalty-free backing track for the preview videos.

Everything is synthesised from scratch with numpy/scipy (no samples, no
copyrighted material), so the track can be used on TikTok, Instagram and
YouTube without claims.

    track(duration, cues, bpm=120) -> float32 stereo array (n, 2) at SR

`cues` is a list of times (seconds) where a new section starts (each game);
they get an impact and a whoosh leading into them (no rising build-up at the start).
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


def write_wav(path, x):
    from scipy.io import wavfile
    wavfile.write(path, SR, (np.clip(x, -1, 1) * 32767).astype(np.int16))


if __name__ == "__main__":
    import sys
    write_wav(sys.argv[1] if len(sys.argv) > 1 else "test.wav", track(20, cues=[4, 12]))
