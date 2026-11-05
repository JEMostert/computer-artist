#!/usr/bin/env python3
"""Original 15 s soundtrack for the "Computer Artist" showreel.

Everything is synthesised here with numpy/scipy: band-limited saws, FM bells,
filtered noise, envelopes, time-varying biquads, a convolution reverb built from
decaying noise and a ping-pong delay. No samples or presets.

128 BPM, 8 bars of 4/4 (1.875 s each), F minor resolving to F major (Picardy).
Outputs reel.wav (48 kHz, stereo, 16-bit, exactly 720000 samples), cues.json
and spectrogram.png, and prints a level/timing report.
"""

import json
import os

import numpy as np
from scipy import signal
from scipy.io import wavfile
from scipy.ndimage import minimum_filter1d, uniform_filter1d

HERE = os.path.dirname(os.path.abspath(__file__))
SR = 48000
N = 720000  # 15.000 s
BPM = 128
BEAT = 60.0 / BPM  # 0.46875 s
BAR = 4 * BEAT  # 1.875 s
SPB = 22500  # samples per beat (exact)
S16 = BEAT / 4
S32 = BEAT / 8
rng = np.random.default_rng(20261105)


def smp(t):
    return int(round(t * SR))


def T(bar, beat=0.0):
    """Time of a 1-indexed bar and 0-indexed (fractional) beat."""
    return (bar - 1) * BAR + beat * BEAT


def mtof(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


def tt(n):
    return np.arange(n) / SR


def panlaw(p):
    a = (np.clip(p, -1, 1) + 1) * np.pi / 4
    return np.cos(a) * np.sqrt(2), np.sin(a) * np.sqrt(2)


def sos(kind, f, order=2):
    return signal.butter(order, f, btype=kind, fs=SR, output='sos')


def sf(x, kind, f, order=2):
    return signal.sosfilt(sos(kind, f, order), x, axis=-1)


def norm(x):
    p = np.max(np.abs(x))
    return x / p if p > 0 else x


def fade_edges(x, a=0.0005, r=0.004):
    x = x.copy()
    na, nr = max(1, int(a * SR)), max(1, int(r * SR))
    x[..., :na] *= np.linspace(0, 1, na)
    x[..., -nr:] *= np.linspace(1, 0, nr)
    return x


# ----------------------------------------------------------------- filters ---
def rbj(kind, f, q):
    f = float(np.clip(f, 20, 0.45 * SR))
    w0 = 2 * np.pi * f / SR
    c, s = np.cos(w0), np.sin(w0)
    al = s / (2 * q)
    if kind == 'low':
        b = [(1 - c) / 2, 1 - c, (1 - c) / 2]
    elif kind == 'high':
        b = [(1 + c) / 2, -(1 + c), (1 + c) / 2]
    else:  # band, 0 dB peak
        b = [al, 0.0, -al]
    a = [1 + al, -2 * c, 1 - al]
    return np.array(b) / a[0], np.array(a) / a[0]


def sweep(x, fc, q=0.707, kind='low', block=64):
    """Time-varying biquad: coefficients updated every `block` samples."""
    if x.ndim == 2:
        return np.vstack([sweep(ch, fc, q, kind, block) for ch in x])
    fc = np.broadcast_to(fc, x.shape)
    y = np.empty_like(x)
    zi = np.zeros(2)
    for s in range(0, len(x), block):
        b, a = rbj(kind, fc[min(s + block // 2, len(x) - 1)], q)
        y[s : s + block], zi = signal.lfilter(b, a, x[s : s + block], zi=zi)
    return y


# ------------------------------------------------------------- oscillators ---
def saw(f, ph0=0.0):
    """PolyBLEP band-limited sawtooth for a per-sample frequency array."""
    inc = f / SR
    p = (ph0 + np.cumsum(inc) - inc) % 1.0
    y = 2 * p - 1
    m = p < inc
    x = p[m] / inc[m]
    y[m] -= x + x - x * x - 1
    m = p > 1 - inc
    x = (p[m] - 1) / inc[m]
    y[m] -= x * x + x + x + 1
    return y


def square(f, ph0=0.0):
    return 0.5 * (saw(f, ph0) - saw(f, (ph0 + 0.5) % 1.0))


def sine_f(f, ph0=0.0):
    return np.sin(2 * np.pi * (ph0 + np.cumsum(f) / SR - f[0] / SR))


def voices(notes, n, spread, width, wave=saw):
    """Detuned unison stack, panned for gentle stereo width. Returns (2, n)."""
    out = np.zeros((2, n))
    for m in notes:
        f0 = mtof(m)
        for cents, pan in ((-spread, -width), (0.0, 0.0), (spread, width)):
            w = wave(np.full(n, f0 * 2 ** (cents / 1200)), rng.random())
            gl, gr = panlaw(pan)
            out[0] += w * gl
            out[1] += w * gr
    return out / np.sqrt(3 * len(notes))


# ------------------------------------------------------------- instruments ---
def kick(length=0.42, f_hi=165, f_lo=47, ptau=0.032, decay=0.20, click=0.30, drive=1.7):
    n = int(length * SR)
    t = tt(n)
    f = f_lo + (f_hi - f_lo) * np.exp(-t / ptau)
    body = sine_f(f)  # starts at phase 0 on the grid
    body *= np.where(t < 0.035, 1.0, np.exp(-(t - 0.035) / decay))
    nz = sf(rng.standard_normal(n), 'high', 2200)
    cl = nz * np.exp(-t / 0.003) * click + np.sin(2 * np.pi * 1800 * t) * np.exp(-t / 0.002) * click
    y = np.tanh(drive * (body + cl)) / np.tanh(drive)
    return fade_edges(y, 0.0, 0.01)


def bass_note(
    m, dur, cut=420, env=1400, ftau=0.05, slide_from=None, sub=0.72, top=0.55, decay=None
):
    n = int(dur * SR)
    t = tt(n)
    f = np.full(n, mtof(m))
    if slide_from is not None:
        f = mtof(m) * 2 ** ((slide_from - m) / 12 * np.exp(-t / 0.018))
    s1 = sine_f(f / 2) if m >= 36 else sine_f(f)
    tone = saw(f, rng.random()) + 0.6 * saw(f * 1.006, rng.random())
    tone = sweep(tone, cut + env * np.exp(-t / ftau), q=1.1)
    amp = np.minimum(1, t / 0.002) * np.exp(-t / (decay or dur * 2.5))
    y = np.tanh(1.4 * (sub * s1 + top * tone)) * amp
    return fade_edges(y, 0.0005, 0.008)


def stab(
    notes,
    dur=0.32,
    decay=0.14,
    hold=0.01,
    base=900,
    bright=5200,
    ftau=0.07,
    q=0.9,
    width=0.65,
    spread=11,
):
    n = int(dur * SR)
    t = tt(n)
    x = voices(notes, n, spread, width)
    x = sweep(x, base + bright * np.exp(-t / ftau), q=q)
    amp = np.minimum(1, t / 0.0015) * np.where(t < hold, 1, np.exp(-(t - hold) / decay))
    x = sf(x * amp, 'high', 160)
    return fade_edges(x, 0.0, 0.01)


def pad(notes, dur, attack=0.4, release=0.4, fc=1400, fc_end=None, width=0.8):
    n = int(dur * SR)
    t = tt(n)
    x = voices(notes, n, 9, width)
    fcs = np.full(n, fc) if fc_end is None else fc * (fc_end / fc) ** (t / t[-1])
    fcs = fcs * (1 + 0.15 * np.sin(2 * np.pi * 0.7 * t))
    x = sweep(x, fcs, q=0.8)
    amp = np.minimum(1, t / attack) * np.minimum(1, (t[-1] - t) / release)
    return sf(x * amp**2, 'high', 180)


def pluck(m, dur=0.22):
    n = int(dur * SR)
    t = tt(n)
    f = np.full(n, mtof(m))
    x = 0.6 * saw(f, rng.random()) + 0.5 * square(f * 1.003, rng.random())
    x = sweep(x, 700 + 4200 * np.exp(-t / 0.035), q=1.4)
    x *= np.minimum(1, t / 0.001) * np.exp(-t / 0.09)
    return fade_edges(x, 0.0, 0.01)


def bell(freq, dur=0.9, ratio=3.5, index=2.2, itau=0.12, decay=0.35):
    t = tt(int(dur * SR))
    mod = index * np.exp(-t / itau) * np.sin(2 * np.pi * freq * ratio * t)
    y = np.sin(2 * np.pi * freq * t + mod) * np.exp(-t / decay) * np.minimum(1, t / 0.001)
    return fade_edges(y, 0.0, 0.02)


def hat(decay=0.03, hp=7200):
    n = int((decay * 7 + 0.01) * SR)
    t = tt(n)
    met = sum(
        np.sign(np.sin(2 * np.pi * f * t + rng.random() * 6.28))
        for f in (205.3, 304.4, 369.6, 522.7, 540.0, 800.0)
    )
    x = 0.65 * rng.standard_normal(n) + 0.18 * met
    x = sf(sf(x, 'high', hp, 4), 'low', 12500, 2)  # no fizz above ~13 kHz
    return norm(x * np.exp(-t / decay) * np.minimum(1, t / 0.0004))


def clap():
    n = int(0.32 * SR)
    t = tt(n)
    x = sf(rng.standard_normal(n), 'bandpass', [950, 3300])
    env = np.zeros(n)
    for off in (0.0, 0.009, 0.018):
        u = t - off
        env += (u >= 0) * np.exp(-np.clip(u, 0, None) / 0.0042)
    u = t - 0.026
    env += 0.75 * (u >= 0) * np.exp(-np.clip(u, 0, None) / 0.065)
    return norm(x * env)


def snare(tone=195):
    n = int(0.25 * SR)
    t = tt(n)
    body = sine_f(tone * (1 + 0.5 * np.exp(-t / 0.008))) * np.exp(-t / 0.05)
    nz = sf(sf(rng.standard_normal(n), 'high', 1400), 'low', 9000) * np.exp(-t / 0.075)
    return fade_edges(norm(0.6 * body + 0.8 * nz), 0.0, 0.01)


def tick(freq, dur=0.02):
    t = tt(int(dur * SR))
    return np.sin(2 * np.pi * freq * t) * np.exp(-t / 0.0028)


def crash(dur=1.6, decay=0.45, lp=9000):
    n = int(dur * SR)
    t = tt(n)
    out = []
    for _ in range(2):
        met = sum(
            np.sign(np.sin(2 * np.pi * f * t + rng.random() * 6.28))
            for f in (431.0, 617.3, 873.1, 1129.0, 1490.7)
        )
        x = rng.standard_normal(n) + 0.15 * met
        x = sf(sf(x, 'high', 2600), 'low', lp)
        out.append(x)
    x = np.array(out) * np.exp(-t / decay) * np.minimum(1, t / 0.001)
    return fade_edges(norm(x), 0.0, 0.05)


def slam(f0=120, f1=48, dur=0.7, decay=0.28):
    """Low tom-ish body for the title slam / impact (mono, low bus)."""
    n = int(dur * SR)
    t = tt(n)
    body = sine_f(f1 + (f0 - f1) * np.exp(-t / 0.06)) * np.exp(-t / decay)
    thump = sf(rng.standard_normal(n), 'low', 900) * np.exp(-t / 0.02) * 0.4
    return fade_edges(np.tanh(1.5 * (body + thump)), 0.0, 0.02)


def noise_riser(dur, f0, f1, q=1.2, curve=2.0, amp_curve=2.0):
    n = int(dur * SR)
    x = np.linspace(0, 1, n)
    fc = f0 * (f1 / f0) ** (x**curve)
    out = np.vstack([sweep(rng.standard_normal(n), fc, q, 'band') for _ in range(2)])
    return out * x**amp_curve


def pitch_riser(m0, m1, dur, width=0.6):
    n = int(dur * SR)
    x = np.linspace(0, 1, n)
    f = mtof(m0) * 2 ** ((m1 - m0) / 12 * x**1.6)
    out = np.zeros((2, n))
    for cents, pan in ((-14, -width), (14, width)):
        gl, gr = panlaw(pan)
        w = saw(f * 2 ** (cents / 1200), rng.random())
        out[0] += w * gl
        out[1] += w * gr
    out = sweep(out, 400 + 5000 * x**2, q=1.0)
    return sf(out * x**2.2, 'high', 200)


# ------------------------------------------------------------- reverb/delay ---
def make_ir(t60, length, predelay, lp, seed):
    r = np.random.default_rng(seed)
    n = int(length * SR)
    t = tt(n)
    chans = []
    for _ in range(2):
        x = r.standard_normal(n) * np.exp(-6.91 * t / t60)
        dark = sf(x, 'low', lp * 0.3)
        w = t / t[-1]
        x = sf(x * (1 - w) + dark * w, 'low', lp)
        x[: int(0.004 * SR)] *= np.linspace(0, 1, int(0.004 * SR))
        chans.append(np.concatenate([np.zeros(int(predelay * SR)), x]))
    ir = np.array(chans)
    return ir / np.sqrt(np.sum(ir**2) / 2)


def reverb(x, ir):
    wet = np.vstack([signal.fftconvolve(x[c], ir[c])[:N] for c in range(2)])
    return sf(wet, 'high', 260)


def pingpong(x, delay_s, fb=0.42, taps=6, lp=5200):
    m = sf(x.mean(0), 'high', 300)
    d = smp(delay_s)
    out = np.zeros((2, N))
    s1 = sos('low', lp, 1)
    y = m
    for k in range(1, taps + 1):
        y = signal.sosfilt(s1, y)
        sh = k * d
        if sh >= N:
            break
        out[(k - 1) % 2, sh:] += fb ** (k - 1) * y[: N - sh]
    return out


# ---------------------------------------------------------------- the mix ---
BUSES = {k: np.zeros((2, N)) for k in ('low', 'bass', 'drums', 'music', 'room', 'hall', 'delay')}
KICKS = []
STARTS = []  # sample index of every scheduled (grid-anchored) event


def put(sig, t, bus, gain=1.0, pan=0.0, room=0.0, hall=0.0, delay=0.0, anchored=True):
    if sig.ndim == 1:
        gl, gr = panlaw(pan)
        st = np.vstack([sig * gl, sig * gr])
    else:
        st = sig
    st = st * gain
    i = smp(t)
    if anchored:
        STARTS.append(i)
    if i < 0:
        st, i = st[:, -i:], 0
    n = min(st.shape[1], N - i)
    if n <= 0:
        return
    BUSES[bus][:, i : i + n] += st[:, :n]
    for name, g in (('room', room), ('hall', hall), ('delay', delay)):
        if g:
            BUSES[name][:, i : i + n] += g * st[:, :n]


def put_end(sig, t_end, bus, gain=1.0, **kw):
    """Place a sound so that it ends exactly at t_end (reverse swells, risers)."""
    n = sig.shape[-1]
    put(sig, t_end - n / SR, bus, gain, anchored=False, **kw)


def add_kick(t, gain=1.0, **kw):
    KICKS.append(t)
    put(kick(**kw), t, 'low', gain)


def sc_curve(times, depth, hold=0.018, rel=0.17):
    g = np.ones(N)
    n = int((hold + rel) * SR)
    t = tt(n)
    shape = np.where(t < hold, 1.0, 0.5 * (1 + np.cos(np.pi * np.clip((t - hold) / rel, 0, 1))))
    for tk in times:
        i = smp(tk)
        m = min(n, N - i)
        g[i : i + m] = np.minimum(g[i : i + m], 1 - depth * shape[:m])
    return uniform_filter1d(g, 48)


def reverse_swell(notes, dur, bright=3000):
    src = stab(notes, dur=0.25, decay=0.08, base=700, bright=bright)
    src = np.pad(src, ((0, 0), (0, int(dur * SR))))
    ir = make_ir(1.6, 1.6, 0.0, 7000, seed=7)
    wet = np.vstack([signal.fftconvolve(src[c], ir[c])[: int(dur * SR)] for c in range(2)])
    x = wet[:, ::-1] + 0.3 * src[:, : int(dur * SR)][:, ::-1]
    x = norm(x)
    x[:, -int(0.002 * SR) :] *= np.linspace(1, 0.3, int(0.002 * SR))
    return x


# chords (rootless voicings over the bass root); top line G F G Bb G Ab-Bb -> A
CH = {
    'Fm9': [56, 60, 63, 67],  # Ab C Eb G
    'Fm9+': [56, 60, 63, 67, 72],
    'Dbmaj9': [56, 60, 63, 65],  # Ab C Eb F
    'Abmaj9': [58, 60, 63, 67],  # Bb C Eb G
    'Eb69': [55, 58, 63, 65, 70],  # G Bb Eb F Bb
    'Db+': [56, 60, 63, 65, 68],
    'Eb+': [58, 63, 65, 67, 70],
    'Fadd9': [57, 60, 65, 67, 69],  # A C F G A  (Picardy resolution)
}
ROOT = {'F': 41, 'Db': 37, 'Ab': 44, 'Eb': 39}
CUES = []


def cue(t, label, note=''):
    CUES.append({'time': round(t, 6), 'label': label, 'note': note})


def bass_line(bar, root, steps, cut=420, env=1400, gain=1.0):
    """steps: (16th position, semitone offset, length in 16ths, slide-from offset or None)."""
    for pos, off, ln, sl in steps:
        m = root + off
        put(
            bass_note(
                m, ln * S16 * 0.92, cut=cut, env=env, slide_from=None if sl is None else root + sl
            ),
            T(bar, pos / 4),
            'bass',
            gain,
        )


def hats(bar, pattern, gain=0.16, decay=0.028, open_pos=(), open_gain=0.15, upto=16):
    """pattern: 16 velocities; open hats at 16th positions in open_pos."""
    for i, v in enumerate(pattern[:upto]):
        if v and i not in open_pos:
            put(hat(decay), T(bar, i / 4), 'drums', gain * v, pan=0.28 if i % 2 else -0.22)
    for i in open_pos:
        if i < upto:
            put(hat(0.11, hp=6500), T(bar, i / 4), 'drums', open_gain, pan=0.15, room=0.15)


def claps(bar, beats=(1, 3), gain=0.42):
    for b in beats:
        put(clap(), T(bar, b), 'drums', gain, room=0.35)


def arp(bar, chord, order, gain=0.15, upto=16, octave=12):
    tones = sorted(n + octave for n in CH[chord])
    for i in range(upto):
        m = tones[order[i % len(order)] % len(tones)]
        put(
            pluck(m),
            T(bar, i / 4),
            'music',
            gain * (1.0 if i % 4 == 0 else 0.75),
            pan=0.3 * np.sin(i * 1.3),
            delay=0.32,
            room=0.12,
        )


ROLL = [1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15]


def build():
    # ---------------------------------------------------- bar 1: intro ----
    cue(T(1), 'nib', 'soft nib ping on beat 1')
    put(
        bell(mtof(89), dur=1.2, ratio=2.0, index=1.1, itau=0.08, decay=0.32),
        T(1),
        'music',
        0.26,
        hall=0.35,
        delay=0.4,
    )
    put(bell(mtof(77), dur=0.8, ratio=1.0, index=0.3, decay=0.25), T(1), 'music', 0.10)
    for b in range(4):  # sub pulses
        t = tt(int(0.36 * SR))
        p = np.sin(2 * np.pi * mtof(29) * t) + 0.25 * np.sin(2 * np.pi * mtof(41) * t)
        p *= np.minimum(1, t / 0.004) * np.exp(-t / 0.11)
        put(fade_edges(p, 0, 0.01), T(1, b), 'low', 0.30 + 0.08 * b)
    put(noise_riser(BAR, 250, 7000, q=1.6), T(1), 'music', 0.09, hall=0.15)
    put(
        pad(CH['Fm9'], BAR, attack=1.0, release=0.02, fc=300, fc_end=2400),
        T(1),
        'music',
        0.20,
        hall=0.2,
    )
    ticks = [0.5, 1.0, 1.5] + [2 + i / 4 for i in range(4)] + [3 + i / 8 for i in range(8)]
    for k, b in enumerate(ticks):
        put(
            tick(2200 * 2 ** (k / 12)),
            T(1, b),
            'drums',
            0.10 + 0.022 * k,
            pan=0.35 if k % 2 else -0.35,
            room=0.2,
        )
    cue(T(1, 2), 'ticks', 'tick build: 16ths then 32nds into the drop')
    put_end(reverse_swell(CH['Fm9+'], BEAT), T(2), 'music', 0.30)
    cue(T(1, 3), 'swell', 'reverse swell into the drop')

    # ---------------------------------------------------- bar 2: drop -----
    cue(T(2), 'drop', 'title slam: kick + slam + Fm9 stab + crash')
    for b in range(4):
        add_kick(T(2, b), 1.0)
    put(slam(), T(2), 'low', 0.55)
    put(
        stab(CH['Fm9+'], dur=0.9, decay=0.32, hold=0.04, bright=6500),
        T(2),
        'music',
        0.62,
        hall=0.45,
        delay=0.2,
    )
    put(crash(1.8, 0.5), T(2), 'drums', 0.20, hall=0.2)
    put(stab(CH['Fm9'], dur=0.2, decay=0.07, bright=3500), T(2, 2.5), 'music', 0.32, room=0.3)
    cue(T(2, 2.5), 'stab', 'offbeat stab')
    bass_line(2, ROOT['F'], [(p, 0, 1.8, None) for p in (2, 6, 10, 14)])
    claps(2)
    hats(2, [0, 0, 1, 0] * 4)
    put_end(reverse_swell(CH['Dbmaj9'], BEAT * 0.75, 2500), T(3), 'music', 0.22)
    cue(T(2, 3.25), 'fill', 'reverse swell into bar 3')

    # ---------------------------------------------------- bar 3: Db -------
    for b in range(4):
        add_kick(T(3, b))
    bass_line(3, ROOT['Db'], [(p, 0, 1, None) for p in ROLL])
    claps(3)
    hats(3, [0.5, 0.35, 1, 0.35] * 4)
    for p in (0, 3, 6, 10):
        put(
            stab(CH['Dbmaj9'], dur=0.18, decay=0.06, bright=3800),
            T(3, p / 4),
            'music',
            0.34,
            room=0.3,
        )
        cue(T(3, p / 4), 'stab', 'syncopated Dbmaj9 stab')
    for k in range(4):  # snare fill
        put(snare(), T(3, 3 + k / 4), 'drums', 0.16 + 0.07 * k, pan=-0.1 + 0.07 * k, room=0.3)
    cue(T(3, 3), 'fill', 'snare 16ths into bar 4')

    # ---------------------------------------------------- bar 4: Ab, arp --
    cue(T(4), 'arp', 'arp enters')
    for b in range(4):
        add_kick(T(4, b))
    bass_line(4, ROOT['Ab'], [(p, 12 if p % 4 == 3 else 0, 1, None) for p in ROLL])
    claps(4)
    hats(4, [0.4, 0.3, 0, 0.3] * 4, open_pos=(2, 6, 10, 14))
    for p in (0, 10):
        put(
            stab(CH['Abmaj9'], dur=0.22, decay=0.08, bright=3600),
            T(4, p / 4),
            'music',
            0.30,
            room=0.3,
        )
    arp(4, 'Abmaj9', [0, 1, 2, 3])
    for k in range(8):  # tick roll
        put(
            tick(3000 * 2 ** (k / 8)),
            T(4, 3 + k / 8),
            'drums',
            0.10 + 0.03 * k,
            pan=0.3 if k % 2 else -0.3,
        )
    put_end(reverse_swell(CH['Eb69'], BEAT, 3200), T(5), 'music', 0.22)
    cue(T(4, 3), 'fill', 'tick roll + reverse swell into bar 5')

    # ---------------------------------------------------- bar 5: Eb, bass var
    for b in range(3):  # beat 4 = stutter
        add_kick(T(5, b))
    bass_line(
        5,
        ROOT['Eb'],
        [
            (2, 0, 1, None),
            (3, 12, 1, None),
            (5, 0, 1, None),
            (6, 0, 1, None),
            (7, 12, 1, 0),
            (9, 0, 1, None),
            (10, 7, 1, None),
            (11, 12, 1, 7),
        ],
        cut=520,
        env=1800,
    )
    claps(5, beats=(1,))
    hats(5, [0.4, 0, 1, 0.5, 0.4, 0.5, 1, 0] * 2, upto=12)
    for p in (3, 11):  # rim clicks
        put(
            tick(1700, 0.03) + 0.4 * tick(3400, 0.03),
            T(5, p / 4),
            'drums',
            0.22,
            pan=0.4,
            room=0.25,
        )
    put(stab(CH['Eb69'], dur=0.22, decay=0.08, bright=3600), T(5), 'music', 0.32, room=0.3)
    arp(5, 'Eb69', [0, 2, 1, 3, 2, 4, 3, 1], upto=12)
    # stutter: retriggered slice of an Eb stab + clap, pitch stepping up
    src = stab(CH['Eb+'], dur=0.12, decay=0.05, bright=5000)
    c = clap()[: src.shape[1]]
    src = norm(src + 0.5 * np.vstack([c, c]))
    hits = [(0, 0), (1, 0), (2, 0), (3, 2), (4, 3), (5, 5), (6, 7), (6.5, 8), (7, 10), (7.5, 12)]
    for k, (pos, st) in enumerate(hits):
        nxt = hits[k + 1][0] if k + 1 < len(hits) else 8
        ln = int((nxt - pos) * S32 * SR * 0.8)
        r = 2 ** (st / 12)
        idx = np.arange(0, ln) * r
        sl = np.vstack(
            [np.interp(idx, np.arange(src.shape[1]), src[ch], right=0) for ch in range(2)]
        )
        put(
            fade_edges(sl, 0.0005, 0.003),
            T(5, 3 + pos / 8),
            'music',
            1.0 + 0.05 * k,
            room=0.15,
            delay=0.12,
        )
    cue(T(5, 3), 'stutter', 'glitch stutter fill (32nd/64th retriggers) into bar 6')

    # ---------------------------------------------------- bar 6: Fm full --
    for b in range(4):
        add_kick(T(6, b))
    put(crash(1.2, 0.35), T(6), 'drums', 0.13, hall=0.15)
    put(pad(CH['Fm9+'], BAR, attack=0.15, release=0.1, fc=1800), T(6), 'music', 0.22, hall=0.2)
    put(stab(CH['Fm9+'], dur=0.4, decay=0.14, bright=5200), T(6), 'music', 0.44, hall=0.3)
    put(stab(CH['Fm9'], dur=0.18, decay=0.06, bright=3500), T(6, 1.75), 'music', 0.28, room=0.3)
    bass_line(
        6,
        ROOT['F'],
        [
            (1, 0, 1, None),
            (2, 12, 1, None),
            (3, 0, 1, None),
            (5, 0, 1, None),
            (6, 12, 1, None),
            (7, 0, 1, None),
            (9, 0, 1, None),
            (10, 12, 1, None),
            (11, 0, 1, None),
            (13, 0, 1, None),
            (14, 12, 1, None),
            (15, 10, 1, None),
        ],
        cut=480,
        env=1700,
    )
    claps(6)
    put(clap(), T(6, 3.75), 'drums', 0.2, room=0.35)
    hats(6, [0.45, 0.3, 0.6, 0.3] * 4, open_pos=(2, 6, 10))
    arp(6, 'Fm9+', [0, 1, 2, 3, 4, 3, 2, 1], gain=0.13)
    for k in range(4):
        put(snare(), T(6, 3 + k / 4), 'drums', 0.18 + 0.08 * k, room=0.3)
    put(noise_riser(BEAT, 1500, 9000, q=1.4), T(6, 3), 'music', 0.10)
    cue(T(6, 3), 'fill', 'snare 16ths + noise lift into bar 7')

    # ---------------------------------------------------- bar 7: cuts -----
    cut_beats = [0, 1, 2, 3, 3.5]
    for k, b in enumerate(cut_beats):
        chord = 'Db+' if b < 2 else 'Eb+'
        if b == int(b):
            add_kick(T(7, b), 0.95)
        put(
            stab(
                CH[chord],
                dur=0.28 if k < 4 else 0.12,
                decay=0.09 if k < 4 else 0.05,
                bright=3800 + 900 * k,
            ),
            T(7, b),
            'music',
            0.40 + 0.04 * k,
            room=0.25,
            delay=0.1,
        )
        put(clap(), T(7, b), 'drums', 0.16 + 0.03 * k, room=0.3)
        cue(T(7, b), 'cut', f'image cut {k + 1}/5: {chord[:2]} stab')
    for b in range(4):
        root = ROOT['Db'] if b < 2 else ROOT['Eb']
        bass_line(
            7,
            root,
            [(4 * b + p, 0, 1, None) for p in (1, 2, 3)],
            cut=420 + 160 * b,
            env=1500 + 300 * b,
        )
    hats(7, [0.4, 0.5, 0.7, 0.5] * 4, gain=0.14, upto=14)
    riser_end = T(7, 3.75)  # last 16th is a gap
    put(noise_riser(riser_end - T(7), 400, 9500, q=1.3, curve=1.5), T(7), 'music', 0.16)
    put(pitch_riser(53, 77, riser_end - T(7)), T(7), 'music', 0.16)
    rolls = [2 + i / 4 for i in range(4)] + [3 + i / 8 for i in range(6)]
    for k, b in enumerate(rolls):
        put(snare(), T(7, b), 'drums', 0.10 + 0.035 * k, pan=0.15 * np.sin(k), room=0.25)
    cue(T(7), 'riser', 'tension riser across bar 7')
    cue(riser_end, 'gap', 'one-16th dropout before the impact')

    # ---------------------------------------------------- bar 8: finale ---
    cue(T(8), 'impact', 'impact + F major add9 resolution')
    add_kick(T(8), 1.0, length=0.7, decay=0.32, f_hi=180, f_lo=44)
    n = int(1.6 * SR)
    t = tt(n)
    boom = sine_f(mtof(29) * (1 + 1.2 * np.exp(-t / 0.05))) * np.exp(-t / 0.42)
    put(fade_edges(np.tanh(1.3 * boom), 0, 0.05), T(8), 'low', 0.55)
    put(slam(140, 45, 0.9, 0.3), T(8), 'low', 0.35)
    put(crash(1.9, 0.6), T(8), 'drums', 0.24, hall=0.3)
    put(
        stab(CH['Fadd9'], dur=1.6, decay=0.45, hold=0.05, bright=6000, ftau=0.12),
        T(8),
        'music',
        0.55,
        hall=0.5,
    )
    put(
        pad(CH['Fadd9'] + [72], 1.85, attack=0.03, release=1.4, fc=2200, fc_end=900),
        T(8),
        'music',
        0.30,
        hall=0.4,
    )
    put(bass_note(ROOT['F'], 1.4, cut=300, env=900, ftau=0.1, decay=0.32), T(8), 'bass', 0.8)
    t_logo = T(8, 1)
    cue(t_logo, 'logo', 'logo hit: F major bell chord + nib ping')
    for m, g, p in ((89, 0.20, -0.3), (93, 0.16, 0.3), (96, 0.14, 0.0), (101, 0.07, 0.2)):
        put(
            bell(mtof(m), dur=1.3, ratio=3.0, index=1.6, itau=0.06, decay=0.4),
            t_logo,
            'music',
            g,
            pan=p,
            hall=0.4,
            delay=0.3,
        )
    put(tick(4200, 0.03), t_logo, 'drums', 0.25, room=0.3)
    put(slam(90, 50, 0.4, 0.12), t_logo, 'low', 0.25)


def master():
    sc = sc_curve(KICKS, depth=0.92)
    sc_music = sc_curve(KICKS, depth=0.32, rel=0.2)
    bass = BUSES['bass'].mean(0) * sc
    low = 0.6 * (BUSES['low'].mean(0) + bass)  # mono low end
    music = BUSES['music'] * sc_music
    room = reverb(BUSES['room'], make_ir(0.55, 0.7, 0.008, 8000, seed=1)) * 0.55
    hall = reverb(BUSES['hall'], make_ir(1.7, 1.9, 0.02, 7000, seed=2)) * 0.5
    dly = pingpong(BUSES['delay'], 0.75 * BEAT, fb=0.42, taps=6) * 0.5
    mix = (
        np.vstack([low, low])
        + 1.4 * BUSES['drums']
        + 1.2 * music
        + 1.2 * (room + hall + dly) * sc_music
    )
    mix = sf(mix, 'high', 26)
    # keep the low end mono: high-pass the side channel
    mid, side = (mix[0] + mix[1]) / 2, (mix[0] - mix[1]) / 2
    side = sf(side, 'high', 150, 4)
    mix = np.vstack([mid + side, mid - side])
    # gentle lookahead peak limiter (about 3 dB on the loudest transients)
    mix /= np.max(np.abs(mix))
    thr = 10 ** (-4.5 / 20)
    a = np.max(np.abs(mix), axis=0)
    gt = np.minimum(1, thr / np.maximum(a, 1e-9))
    L = int(0.0015 * SR)
    g = minimum_filter1d(gt, 2 * L + 1)
    rel = np.exp(-1 / (0.06 * SR))
    out = np.empty_like(g)
    prev = 1.0
    for i in range(len(g)):
        prev = min(g[i], 1 - (1 - prev) * rel)
        out[i] = prev
    g = uniform_filter1d(out, L)
    g = np.minimum(g, minimum_filter1d(gt, 1))  # guarantee no overs
    mix *= g
    # finale tail fades to silence by 15.000 s
    t = tt(N)
    fade = np.ones(N)
    m = t >= 14.2
    fade[m] = 0.5 * (1 + np.cos(np.pi * (t[m] - 14.2) / (15.0 - 14.2)))
    mix *= fade**1.5
    mix = mix / np.max(np.abs(mix)) * 10 ** (-1 / 20)
    dither = (rng.random((2, N)) - rng.random((2, N))) / 32768
    pcm = np.round(np.clip(mix + dither, -1, 1) * 32767).astype(np.int16)
    return mix, pcm


def report(mix, pcm):
    def db(x):
        return 20 * np.log10(max(x, 1e-12))

    print(f'samples={pcm.shape[1]} ({pcm.shape[1] / SR:.6f} s), channels={pcm.shape[0]}')
    print(
        f'peak={db(np.max(np.abs(pcm)) / 32768):.2f} dBFS, '
        f'int16 max |x|={np.max(np.abs(pcm.astype(np.int32)))}, '
        f'clipped samples={int(np.sum(np.abs(pcm.astype(np.int32)) >= 32767))}'
    )
    print('bar  start    RMS dBFS  peak dBFS')
    for b in range(8):
        seg = mix[:, b * 4 * SPB : (b + 1) * 4 * SPB]
        print(
            f'{b + 1:>3}  {b * BAR:6.3f}   {db(np.sqrt(np.mean(seg**2))):7.2f}  '
            f'{db(np.max(np.abs(seg))):8.2f}'
        )
    print(
        f'whole RMS {db(np.sqrt(np.mean(mix**2))):.2f} dBFS; last 50 ms RMS '
        f'{db(np.sqrt(np.mean(mix[:, -2400:] ** 2))):.1f} dBFS'
    )
    # low-end mono check
    mid, side = (mix[0] + mix[1]) / 2, (mix[0] - mix[1]) / 2
    lm, ls = sf(mid, 'low', 120, 4), sf(side, 'low', 120, 4)
    print(
        f'side/mid energy below 120 Hz: {db(np.sqrt(np.mean(ls**2)) / np.sqrt(np.mean(lm**2))):.1f} dB'
    )
    # band balance
    for lo, hi in ((20, 120), (120, 500), (500, 2000), (2000, 6000), (6000, 12000), (12000, 23000)):
        x = sf(mid, 'bandpass', [lo, hi], 4)
        print(f'  band {lo:>5}-{hi:<5} Hz RMS {db(np.sqrt(np.mean(x**2))):6.1f} dBFS')
    # kick onsets: cross-correlate the < 150 Hz band with a kick template (lag search ±10 ms)
    lowband = sf(mid, 'low', 150, 2)
    tmpl = sf(sf(kick(), 'high', 26), 'low', 150, 2)[:4000]  # same filtering as the mix
    devs = []
    for tk in KICKS:
        i = smp(tk)
        seg = lowband[i - 480 : i + 480 + len(tmpl) - 1]
        devs.append((np.argmax(np.correlate(seg, tmpl, 'valid')) - 480) / SR * 1000)
    devs = np.array(devs)
    print(
        f'kick onsets vs beat grid (xcorr): {int(np.sum(np.abs(devs) < 0.05))}/{len(devs)} at 0.00 ms; '
        f'others: {[(round(k, 4), round(v, 3)) for k, v in zip(KICKS, devs) if abs(v) >= 0.05]}'
    )
    # every scheduled event start vs the 64th-note grid (1406.25 samples)
    st = np.array(STARTS, float)
    g64 = SPB / 16
    sdev = np.abs(st - np.round(st / g64) * g64)
    on16 = np.sum(np.abs(st - np.round(st / (SPB / 4)) * (SPB / 4)) < 0.5)
    print(
        f'scheduled events: {len(st)}; max distance from 64th grid {sdev.max():.2f} samples; '
        f'{on16} exactly on 16ths, {len(st) - on16} on 32nd/64th (stutter, rolls)'
    )
    # audio transients: >= 15 dB rise within 1 ms above 5 kHz, onset = first sample at 2x the
    # level 1 ms earlier; compared with the 64th grid and with the scheduled event starts
    hp = sf(mid, 'high', 5000, 4)
    env = np.sqrt(np.maximum(uniform_filter1d(hp**2, 24), 0))
    le = 20 * np.log10(env + 1e-7)
    d = np.zeros_like(le)
    d[48:] = le[48:] - le[:-48]
    pk, _ = signal.find_peaks(np.where(le > -55, d, 0), height=15, distance=int(0.02 * SR))
    on = np.array([p - 48 + np.argmax(env[p - 48 : p + 1] > 2 * env[p - 48]) for p in pk])
    g = SPB / 16
    dev = (on - np.round(on / g) * g) / SR * 1000
    ok = np.abs(dev) <= 1.0
    print(
        f'audio transients: {len(on)}; within ±1 ms of the grid: {int(ok.sum())} '
        f'(median {np.median(dev[ok]):+.2f} ms)'
    )
    starts = np.sort(np.array(STARTS))
    rest = []
    for o in on[~ok]:
        prev = starts[starts <= o]
        rest.append((o - prev[-1]) / SR * 1000 if len(prev) else np.inf)
    rest = np.array(rest)
    print(
        f'  the other {len(rest)} are {int(np.sum(rest < 30))} secondary bursts within 30 ms of a '
        f'grid-placed event (clap re-hits, hat noise); unexplained: {int(np.sum(rest >= 30))}'
    )


def spectrogram(mix):
    import matplotlib

    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    mid = mix.mean(0)
    f, t, S = signal.spectrogram(mid, SR, nperseg=2048, noverlap=2048 - 256, window='hann')
    Sdb = 10 * np.log10(S + 1e-14)
    Sdb -= Sdb.max()
    fig, ax = plt.subplots(
        2, 1, figsize=(14, 8), gridspec_kw={'height_ratios': [3, 1]}, sharex=True
    )
    keep = f >= 25
    im = ax[0].pcolormesh(t, f[keep], Sdb[keep], vmin=-90, vmax=0, cmap='magma', shading='auto')
    ax[0].set_yscale('log')
    ax[0].set_ylim(25, 22000)
    ax[0].set_ylabel('Hz')
    for b in range(9):
        ax[0].axvline(b * BAR, color='w', lw=0.6, alpha=0.6)
        ax[1].axvline(b * BAR, color='k', lw=0.6, alpha=0.4)
    fig.colorbar(im, ax=list(ax), pad=0.01, label='dB (top)')
    env = np.sqrt(np.maximum(uniform_filter1d(mid**2, 480), 0))
    ax[1].plot(np.arange(N)[::48] / SR, 20 * np.log10(env[::48] + 1e-9), lw=0.6)
    ax[1].set_ylim(-70, 0)
    ax[1].set_ylabel('RMS dBFS (10 ms)')
    ax[1].set_xlabel('s')
    ax[0].set_title('Computer Artist reel — 128 BPM, F minor → F major (bar lines in white)')
    fig.savefig(os.path.join(HERE, 'spectrogram.png'), dpi=110)


def main():
    build()
    mix, pcm = master()
    wavfile.write(os.path.join(HERE, 'reel.wav'), SR, pcm.T.copy())
    sections = [
        (1, 'intro', 'Fm9 (pad)'),
        (2, 'drop', 'Fm9'),
        (3, 'groove A', 'Dbmaj9'),
        (4, 'groove B: arp', 'Abmaj9'),
        (5, 'groove C: bass variation + stutter', 'Eb6/9'),
        (6, 'groove D: full', 'Fm9'),
        (7, 'cuts + riser', 'Dbmaj9 -> Eb'),
        (8, 'finale', 'F major add9'),
    ]
    CUES.sort(key=lambda c: c['time'])
    for c in CUES:
        c['frame'] = round(c['time'] * 60, 3)
        c['bar'] = int(c['time'] // BAR) + 1
        c['beat'] = round((c['time'] % BAR) / BEAT + 1, 4)
    cues = {
        'bpm': BPM,
        'fps': 60,
        'sample_rate': SR,
        'duration': N / SR,
        'beat_seconds': BEAT,
        'bar_seconds': BAR,
        'key': 'F minor, resolving to F major',
        'beats': [round(i * BEAT, 6) for i in range(32)],
        'bars': [round(i * BAR, 6) for i in range(8)],
        'sections': [
            {'bar': b, 'start': (b - 1) * BAR, 'end': b * BAR, 'name': nm, 'chord': ch}
            for b, nm, ch in sections
        ],
        'accents': CUES,
    }
    with open(os.path.join(HERE, 'cues.json'), 'w') as fh:
        json.dump(cues, fh, indent=2)
    report(mix, pcm)
    try:
        spectrogram(mix)
    except ImportError:
        print('matplotlib missing: skipped spectrogram.png')
    print('wrote reel.wav and cues.json')


if __name__ == '__main__':
    main()
