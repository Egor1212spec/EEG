

import os
import numpy as np
import pandas as pd
import mne
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import spectrogram, welch

mne.set_log_level("ERROR")

F_NAME        = "NP042309_CE.fif"
FIG_DIR       = "figures"   
CSV_DIR       = "results"   


DETECT_CH     = ["O1", "O2", "P3", "P4"]

BAND          = (6.0, 15.0)

# Условия, по которым участок считается "устойчивым":
MIN_DURATION  = 1.0      
FREQ_TOL      = 0.5      # +/- Гц
WIN_SEC       = 2.0      # ширина окна, в котором меряем частоту, с
STEP_SEC      = 0.25     # на сколько сдвигаем окно на каждом шаге, с

# Параметры спектрального анализа:
ANALYSIS_BAND = (1.0, 30.0)   # полоса для спектра
ALPHA_BAND    = (8.0, 13.0)   # льфа-ритмом
PSD_NFFT      = 2048          # длина FFT

MAIN_CH       = ["O1", "O2", "P3", "P4", "Pz"]  


BANDS = {"delta": (0.5, 4), "theta": (4, 8), "alpha": (8, 13), "beta": (13, 30)}




def dominant_freq_track(sig, sfreq):
    
    nperseg = int(WIN_SEC * sfreq)
    noverlap = nperseg - int(STEP_SEC * sfreq)
    f, t, Sxx = spectrogram(sig, fs=sfreq, nperseg=nperseg,
                            noverlap=noverlap, scaling="density")
    
    bmask = (f >= BAND[0]) & (f <= BAND[1])
    fb, Sb = f[bmask], Sxx[bmask, :]
    dom_idx = np.argmax(Sb, axis=0)

    return t, fb[dom_idx], Sb[dom_idx, np.arange(Sb.shape[1])]


def find_stable_runs(t, dom_freq, gate, min_dur, tol):


    runs = []
    n = len(dom_freq)
    i = 0
    while i < n:
        
        if not gate[i]:
            i += 1
            continue
        j = i
        acc = [dom_freq[i]]
        
        while j + 1 < n and gate[j + 1] and abs(dom_freq[j + 1] - np.mean(acc)) <= tol:
            j += 1
            acc.append(dom_freq[j])
        dur = t[j] - t[i]
        
        if dur >= min_dur:
            runs.append({"start": float(t[i]), "end": float(t[j]),
                         "freq": float(np.mean(acc))})
        i = j + 1
    return runs


def detect_target_fragment(raw, sfreq):
    avail = [ch for ch in DETECT_CH if ch in raw.ch_names]
    if not avail:
        raise SystemExit(f"Нет ни одного из каналов {DETECT_CH}")
    
    band_raw = raw.copy().filter(*BAND)
    sig = band_raw.get_data(picks=avail).mean(axis=0)
    t, dom_freq, dom_power = dominant_freq_track(sig, sfreq)
    
    gate = dom_power > np.median(dom_power)
    runs = find_stable_runs(t, dom_freq, gate, MIN_DURATION, FREQ_TOL)
    if not runs:
        raise SystemExit("Устойчивых фрагментов не найдено — смягчите параметры.")
    
    for k, r in enumerate(runs, 1):
        r["index"] = k
        r["duration"] = r["end"] - r["start"]
    
    target = max(runs, key=lambda r: r["duration"])
    return target, runs, avail



def channel_psd(sig, sfreq):
    
    nperseg = min(len(sig), int(2 * sfreq))
    nfft = max(PSD_NFFT, nperseg)
    f, p = welch(sig, fs=sfreq, nperseg=nperseg, nfft=nfft, scaling="density")
    
    m = (f >= ANALYSIS_BAND[0]) & (f <= ANALYSIS_BAND[1])
    
    return f[m], p[m]


def band_power(f, p, lo, hi):
    # Мощность в заданной полосе — это площадь под кривой спектра на этом участке.
    m = (f >= lo) & (f <= hi)
    return float(np.trapezoid(p[m], f[m])) if m.any() else 0.0


def alpha_coefficients(f, p):
    
    am = (f >= ALPHA_BAND[0]) & (f <= ALPHA_BAND[1])
    fa, pa = f[am], p[am]

    
    modal_freq = float(fa[np.argmax(pa)])              
    psum = pa.sum()
    centroid = float(np.sum(fa * pa) / psum)           
    spread = float(np.sqrt(np.sum((fa - centroid) ** 2 * pa) / psum))  

  
    p_total = band_power(f, p, ANALYSIS_BAND[0], ANALYSIS_BAND[1])
    p_alpha = band_power(f, p, *ALPHA_BAND)
    p_theta = band_power(f, p, *BANDS["theta"])
    p_beta = band_power(f, p, *BANDS["beta"])
    p_delta = band_power(f, p, *BANDS["delta"])

    
    alpha_index = 100.0 * p_alpha / p_total if p_total > 0 else 0.0     
    alpha_theta = p_alpha / p_theta if p_theta > 0 else np.nan          
    alpha_beta = p_alpha / p_beta if p_beta > 0 else np.nan             
    peak_power = float(pa.max())
    peakedness = peak_power / pa.mean() if pa.mean() > 0 else np.nan   

    
    half = peak_power / 2.0
    above = fa[pa >= half]
    fwhm = float(above.max() - above.min()) if above.size >= 2 else 0.0
    q_factor = modal_freq / fwhm if fwhm > 0 else np.nan               # острота (добротность) пика

    
    pp = pa / psum
    pp = pp[pp > 0]
    entropy = float(-np.sum(pp * np.log(pp)) / np.log(len(pp))) if len(pp) > 1 else 0.0

    return {
        "modal_freq_Hz": round(modal_freq, 2),
        "centroid_Hz": round(centroid, 2),
        "freq_spread_Hz": round(spread, 3),
        "fwhm_Hz": round(fwhm, 2),
        "alpha_index_pct": round(alpha_index, 1),
        "alpha_power_uV2": round(p_alpha * 1e12, 2),
        "alpha_theta_ratio": round(alpha_theta, 2) if np.isfinite(alpha_theta) else np.nan,
        "alpha_beta_ratio": round(alpha_beta, 2) if np.isfinite(alpha_beta) else np.nan,
        "peakedness": round(peakedness, 2) if np.isfinite(peakedness) else np.nan,
        "q_factor": round(q_factor, 2) if np.isfinite(q_factor) else np.nan,
        "spectral_entropy": round(entropy, 3),
        "delta_power_uV2": round(p_delta * 1e12, 2),
        "theta_power_uV2": round(p_theta * 1e12, 2),
        "beta_power_uV2": round(p_beta * 1e12, 2),
    }


# ----------------- визуализация -----------------
def plot_topomaps(values_by_metric, info, target, fname):
    metrics = list(values_by_metric.keys())
    ncol = 3
    nrow = int(np.ceil(len(metrics) / ncol))
    fig, axes = plt.subplots(nrow, ncol, figsize=(4.2 * ncol, 3.8 * nrow))
    axes = np.atleast_1d(axes).ravel()
    cmaps = {"modal_freq_Hz": "viridis", "centroid_Hz": "viridis",
             "freq_spread_Hz": "magma", "spectral_entropy": "magma"}
    for ax, m in zip(axes, metrics):
        vals = np.asarray(values_by_metric[m], dtype=float)
        cmap = cmaps.get(m, "Reds")
        im, _ = mne.viz.plot_topomap(vals, info, axes=ax, show=False,
                                     cmap=cmap, contours=4)
        ax.set_title(m, fontsize=10)
        cb = fig.colorbar(im, ax=ax, shrink=0.7)
        cb.ax.tick_params(labelsize=7)
    for ax in axes[len(metrics):]:
        ax.axis("off")
    fig.suptitle(
        f"Распределение характеристик альфа-ритма по отведениям\n"
        f"целевой фрагмент {target['start']:.2f}-{target['end']:.2f} с "
        f"(длит. {target['duration']:.2f} с)",
        fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(fname, dpi=120)
    plt.close(fig)


def plot_spectra(psd_by_ch, main_ch, target, fname):
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for ch in main_ch:
        if ch not in psd_by_ch:
            continue
        f, p = psd_by_ch[ch]
        ax.semilogy(f, p * 1e12, lw=1.2, label=ch)
    ax.axvspan(*ALPHA_BAND, color="orange", alpha=0.18, label="альфа 8-13 Гц")
    ax.set_xlim(*ANALYSIS_BAND)
    ax.set_xlabel("Частота, Гц")
    ax.set_ylabel("PSD, мкВ²/Гц (лог)")
    ax.set_title(f"Спектры основных отведений\nцелевой фрагмент "
                 f"{target['start']:.2f}-{target['end']:.2f} с")
    ax.legend()
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(fname, dpi=120)
    plt.close(fig)


def main():
    os.makedirs(FIG_DIR, exist_ok=True)
    os.makedirs(CSV_DIR, exist_ok=True)

    raw = mne.io.read_raw_fif(F_NAME, preload=True)
    raw.filter(0.5, 30.0)
    raw.set_eeg_reference("average")
    sfreq = raw.info["sfreq"] # частота дискритизации 

    
    target, runs, avail = detect_target_fragment(raw, sfreq)

    
    picks_eeg = mne.pick_types(raw.info, eeg=True)
    eeg_names = [raw.ch_names[i] for i in picks_eeg]
    frag = raw.copy().crop(tmin=target["start"], tmax=target["end"])
    data = frag.get_data(picks=picks_eeg)
    
    rows = []
    psd_by_ch = {}
    for k, ch in enumerate(eeg_names):
        f, p = channel_psd(data[k], sfreq)
        psd_by_ch[ch] = (f, p)
        coef = alpha_coefficients(f, p)
        rows.append({"channel": ch, **coef})
    table = pd.DataFrame(rows)

    
    topo_metrics = ["modal_freq_Hz", "centroid_Hz", "freq_spread_Hz",
                    "alpha_index_pct", "alpha_power_uV2", "peakedness",
                    "q_factor", "spectral_entropy", "alpha_theta_ratio"]
    info = mne.pick_info(raw.info, picks_eeg)
    values_by_metric = {}
    for m in topo_metrics:
        v = table[m].to_numpy(dtype=float)
        
        if np.isnan(v).any():
            v = np.nan_to_num(v, nan=np.nanmean(v) if np.isfinite(np.nanmean(v)) else 0.0)
        values_by_metric[m] = v
    
    topo_path = os.path.join(FIG_DIR, "target_topomaps.png")
    try:
        plot_topomaps(values_by_metric, info, target, topo_path)
        topo_ok = True
    except Exception as ex:
        topo_ok = False
        print(f"[!] Топокарты недоступны ({ex}) — пропущено.")

    
    main_avail = [ch for ch in MAIN_CH if ch in psd_by_ch]
    spectra_path = os.path.join(FIG_DIR, "target_spectra.png")
    plot_spectra(psd_by_ch, main_avail, target, spectra_path)

    
    print("=" * 70)
    print("ЦЕЛЕВОЙ ФРАГМЕНТ: спектральный анализ альфа-ритма по отведениям")
    print("=" * 70)
    print(f"Файл:                 {F_NAME}")
    print(f"Всего устойчивых фр.:  {len(runs)}")
    print(f"Целевой (самый длинный): #{target['index']}  "
          f"{target['start']:.2f}-{target['end']:.2f} с  "
          f"(длит. {target['duration']:.2f} с, детект.частота {target['freq']:.2f} Гц)")
    print(f"Диапазон анализа:     {ANALYSIS_BAND[0]}-{ANALYSIS_BAND[1]} Гц")
    print(f"Полоса альфа:         {ALPHA_BAND[0]}-{ALPHA_BAND[1]} Гц")
    print(f"Каналов EEG:          {len(eeg_names)}")
    print()

    
    main_tbl = table[table["channel"].isin(main_avail)] if main_avail else table
    print("Коэффициенты по основным отведениям:")
    cols = ["channel", "modal_freq_Hz", "freq_spread_Hz", "alpha_index_pct",
            "alpha_power_uV2", "peakedness", "q_factor", "spectral_entropy"]
    print(main_tbl[cols].to_string(index=False))
    print()
    print("Усреднённо по всем отведениям:")
    print(f"  модальная частота:  {table['modal_freq_Hz'].mean():.2f} +/- "
          f"{table['modal_freq_Hz'].std():.2f} Гц")
    print(f"  частотный разброс:  {table['freq_spread_Hz'].mean():.3f} Гц")
    print(f"  альфа-индекс:       {table['alpha_index_pct'].mean():.1f} %")
    top = table.loc[table['alpha_power_uV2'].idxmax(), 'channel']
    print(f"  макс. альфа-мощность в отведении: {top}")
    print()

    
    csv_path = os.path.join(CSV_DIR, "target_coefficients.csv")
    table.to_csv(csv_path, index=False, encoding="utf-8")
    print("Сохранено:")
    print(f"  {csv_path}  — коэффициенты по всем отведениям")
    if topo_ok:
        print(f"  {topo_path}      — карты распределения по отведениям")
    print(f"  {spectra_path}       — спектры основных отведений")


if __name__ == "__main__":
    main()
