"""
Поиск интервалов альфа-ритма (8-13 Гц) в ЭЭГ-записи .fif.

Метод:
  1. Полосовой фильтр 8-13 Гц.
  2. Огибающая мгновенной амплитуды через преобразование Гильберта.
  3. Сглаживание и порог по перцентилю -> интервалы повышенной альфа-активности.

Результат: графики (PNG) + текстовая сводка + CSV с интервалами.
"""

import numpy as np
import pandas as pd
import mne
import matplotlib
matplotlib.use("Agg")  # без интерактивного окна, сохраняем в файлы
import matplotlib.pyplot as plt
from scipy.signal import hilbert, welch

mne.set_log_level("ERROR")

# ============================ ПАРАМЕТРЫ ============================
F_NAME       = "NP042309_CE.fif"
CHANNELS     = ["O1", "O2", "P3", "P4"]  # затылочно-теменные: там живёт альфа
ALPHA_BAND   = (8.0, 13.0)
PERCENTILE   = 75       # порог: верхние 25% значений огибающей считаем "альфа активна"
MIN_DURATION = 0.5      # минимальная длительность интервала, с
MERGE_GAP    = 0.3      # склеивать интервалы, если разрыв между ними меньше, с
SMOOTH_SEC   = 0.2      # окно сглаживания огибающей, с
# ==================================================================


def moving_average(x, win):
    if win < 1:
        return x
    kernel = np.ones(win) / win
    return np.convolve(x, kernel, mode="same")


def find_intervals(mask, times, sfreq, min_dur, merge_gap):
    """По булевой маске -> список (start, end) с учётом склейки и мин. длительности."""
    segs = []
    i = 0
    n = len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            segs.append([times[i], times[j - 1]])
            i = j
        else:
            i += 1

    # склейка близких интервалов
    merged = []
    for s, e in segs:
        if merged and s - merged[-1][1] <= merge_gap:
            merged[-1][1] = e
        else:
            merged.append([s, e])

    # фильтр по минимальной длительности
    return [(s, e) for s, e in merged if e - s >= min_dur]


def main():
    # ---------- Загрузка и предобработка ----------
    raw = mne.io.read_raw_fif(F_NAME, preload=True)
    raw.filter(0.5, 30.0)
    raw.set_eeg_reference("average")
    sfreq = raw.info["sfreq"]
    times = raw.times

    avail = [ch for ch in CHANNELS if ch in raw.ch_names]
    if not avail:
        raise SystemExit(f"Нет ни одного из каналов {CHANNELS} в файле")

    # ---------- Огибающая альфы ----------
    alpha_raw = raw.copy().filter(*ALPHA_BAND)
    smooth_win = int(SMOOTH_SEC * sfreq)

    envelopes = {}
    for ch in avail:
        sig = alpha_raw.get_data(picks=ch)[0]
        env = np.abs(hilbert(sig))
        envelopes[ch] = moving_average(env, smooth_win)

    # усреднённая огибающая по выбранным каналам
    env_mean = np.mean(np.vstack(list(envelopes.values())), axis=0)

    # ---------- Порог и интервалы ----------
    thr = np.percentile(env_mean, PERCENTILE)
    mask = env_mean > thr
    intervals = find_intervals(mask, times, sfreq, MIN_DURATION, MERGE_GAP)

    # ---------- Сводная таблица по интервалам ----------
    broadband = raw.get_data(picks=avail)  # для оценки амплитуды
    rows = []
    for k, (s, e) in enumerate(intervals, 1):
        idx = (times >= s) & (times <= e)
        # доминирующая частота внутри альфа-полосы на этом отрезке
        frag = raw.copy().crop(tmin=s, tmax=e).get_data(picks=avail).mean(axis=0)
        f, p = welch(frag, fs=sfreq, nperseg=min(len(frag), int(2 * sfreq)))
        amask = (f >= ALPHA_BAND[0]) & (f <= ALPHA_BAND[1])
        peak_f = f[amask][np.argmax(p[amask])] if amask.any() else np.nan
        rows.append({
            "interval": k,
            "start_s": round(s, 2),
            "end_s": round(e, 2),
            "duration_s": round(e - s, 2),
            "mean_alpha_uV": round(env_mean[idx].mean() * 1e6, 2),
            "peak_freq_Hz": round(float(peak_f), 2),
        })
    table = pd.DataFrame(rows)

    # ---------- Относительная мощность по диапазонам (вся запись) ----------
    bands = {"delta": (0.5, 4), "theta": (4, 8), "alpha": (8, 13), "beta": (13, 30)}
    band_rows = []
    for ch in avail:
        x = raw.get_data(picks=ch)[0]
        f, p = welch(x, fs=sfreq, nperseg=int(4 * sfreq))
        tot = np.trapezoid(p[(f >= 0.5) & (f <= 30)], f[(f >= 0.5) & (f <= 30)])
        rel = {}
        for b, (lo, hi) in bands.items():
            m = (f >= lo) & (f <= hi)
            rel[b] = round(100 * np.trapezoid(p[m], f[m]) / tot, 1)
        band_rows.append({"channel": ch, **rel})
    band_table = pd.DataFrame(band_rows)

    # ===================== ГРАФИКИ =====================
    # 1) Огибающая альфы с порогом и подсвеченными интервалами
    fig, ax = plt.subplots(figsize=(15, 4))
    ax.plot(times, env_mean * 1e6, color="tab:blue", lw=0.8, label="Альфа-огибающая (среднее по каналам)")
    ax.axhline(thr * 1e6, color="red", ls="--", lw=1, label=f"Порог (p{PERCENTILE})")
    for s, e in intervals:
        ax.axvspan(s, e, color="orange", alpha=0.3)
    ax.set_xlabel("Время, с")
    ax.set_ylabel("Амплитуда альфы, мкВ")
    ax.set_title("Огибающая альфа-ритма (8-13 Гц) и обнаруженные интервалы")
    ax.legend(loc="upper right")
    ax.margins(x=0)
    fig.tight_layout()
    fig.savefig("alpha_envelope.png", dpi=120)
    plt.close(fig)

    # 2) Широкополосный сигнал O1/O2 с подсветкой интервалов
    occ = [ch for ch in ["O1", "O2"] if ch in avail] or avail[:2]
    fig, axes = plt.subplots(len(occ), 1, figsize=(15, 2.4 * len(occ)), sharex=True)
    if len(occ) == 1:
        axes = [axes]
    for ax, ch in zip(axes, occ):
        ax.plot(times, raw.get_data(picks=ch)[0] * 1e6, color="black", lw=0.4)
        for s, e in intervals:
            ax.axvspan(s, e, color="orange", alpha=0.3)
        ax.set_ylabel(f"{ch}, мкВ")
        ax.margins(x=0)
    axes[0].set_title("Сигнал ЭЭГ с подсвеченными альфа-интервалами")
    axes[-1].set_xlabel("Время, с")
    fig.tight_layout()
    fig.savefig("alpha_signal.png", dpi=120)
    plt.close(fig)

    # 3) PSD всей записи с выделенной альфа-полосой
    fig, ax = plt.subplots(figsize=(9, 5))
    for ch in avail:
        x = raw.get_data(picks=ch)[0]
        f, p = welch(x, fs=sfreq, nperseg=int(4 * sfreq))
        sel = (f >= 1) & (f <= 30)
        ax.semilogy(f[sel], p[sel], lw=1, label=ch)
    ax.axvspan(*ALPHA_BAND, color="orange", alpha=0.2, label="альфа 8-13 Гц")
    ax.set_xlabel("Частота, Гц")
    ax.set_ylabel("PSD (В²/Гц, лог)")
    ax.set_title("Спектр мощности по каналам")
    ax.legend()
    fig.tight_layout()
    fig.savefig("alpha_psd.png", dpi=120)
    plt.close(fig)

    # ===================== СВОДКА =====================
    total_alpha = sum(e - s for s, e in intervals)
    rec_len = times[-1]
    print("=" * 64)
    print("СВОДКА: поиск альфа-ритма (8-13 Гц)")
    print("=" * 64)
    print(f"Файл:                 {F_NAME}")
    print(f"Каналы:               {', '.join(avail)}")
    print(f"Длительность записи:  {rec_len:.1f} с")
    print(f"Найдено интервалов:   {len(intervals)}")
    print(f"Суммарно альфы:       {total_alpha:.1f} с ({100 * total_alpha / rec_len:.1f}% записи)")
    print()
    print("Относительная мощность по диапазонам (вся запись):")
    print(band_table.to_string(index=False))
    print()
    print("Интервалы альфа-активности:")
    if len(table):
        print(table.to_string(index=False))
    else:
        print("  (ничего не найдено — попробуй снизить PERCENTILE)")
    print()
    print("Сохранены графики: alpha_envelope.png, alpha_signal.png, alpha_psd.png")

    table.to_csv("alpha_intervals.csv", index=False, encoding="utf-8")
    print("Сохранена таблица: alpha_intervals.csv")


if __name__ == "__main__":
    main()
