"""
Поиск устойчивых частот в ЭЭГ по методике В.Б. Войнова.

Логика:
  1. Полосовая фильтрация 6-15 Гц (шире классической альфы).
  2. Спектрограмма (скользящее окно) -> трек доминирующей частоты во времени.
  3. Поиск интервалов >= 1-3 с, где доминирующая частота УСТОЙЧИВА
     (держится в коридоре +/- FREQ_TOL Гц).
  4. Для каждого устойчивого фрагмента: частота, мощность и
     региональная представленность (мощность по всем каналам -> топокарта).

Результат: графики (PNG) + сводка + CSV.
"""

import numpy as np
import pandas as pd
import mne
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from scipy.signal import spectrogram, welch

mne.set_log_level("ERROR")

# ============================ ПАРАМЕТРЫ ============================
F_NAME        = "NP042309_CE.fif"
DETECT_CH     = ["O1", "O2", "P3", "P4"]  # каналы для поиска устойчивой частоты
BAND          = (6.0, 15.0)               # рабочая полоса (по В.Б.: шире альфы)
MIN_DURATION  = 1.0      # минимальная длительность устойчивого фрагмента, с (В.Б.: 1-3 с)
FREQ_TOL      = 0.5      # коридор устойчивости частоты, +/- Гц
WIN_SEC       = 2.0      # окно спектрограммы, с (даёт разрешение ~0.5 Гц)
STEP_SEC      = 0.25     # шаг скользящего окна, с
REGION_HALF   = 1.0      # полуширина полосы вокруг устойчивой частоты для оценки мощности, Гц
# ==================================================================


def dominant_freq_track(sig, sfreq):
    """Спектрограмма -> доминирующая частота в полосе BAND для каждого временного окна."""
    nperseg = int(WIN_SEC * sfreq)
    noverlap = nperseg - int(STEP_SEC * sfreq)
    f, t, Sxx = spectrogram(sig, fs=sfreq, nperseg=nperseg,
                            noverlap=noverlap, scaling="density")
    bmask = (f >= BAND[0]) & (f <= BAND[1])
    fb, Sb = f[bmask], Sxx[bmask, :]
    dom_idx = np.argmax(Sb, axis=0)
    dom_freq = fb[dom_idx]
    dom_power = Sb[dom_idx, np.arange(Sb.shape[1])]
    return t, dom_freq, dom_power, f, Sxx


def find_stable_runs(t, dom_freq, gate, min_dur, tol):
    """Интервалы, где доминирующая частота держится в коридоре +/- tol дольше min_dur."""
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
            runs.append({"start": t[i], "end": t[j], "freq": float(np.mean(acc))})
        i = j + 1
    return runs


def regional_power(raw, picks_eeg, s, e, f0, half, sfreq):
    """Мощность в полосе [f0-half, f0+half] по каждому каналу EEG на отрезке [s, e]."""
    frag = raw.copy().crop(tmin=s, tmax=e).get_data(picks=picks_eeg)
    vals = np.zeros(len(picks_eeg))
    for k in range(frag.shape[0]):
        f, p = welch(frag[k], fs=sfreq, nperseg=min(frag.shape[1], int(2 * sfreq)))
        m = (f >= f0 - half) & (f <= f0 + half)
        vals[k] = np.trapezoid(p[m], f[m]) if m.any() else 0.0
    return vals


def main():
    # ---------- Загрузка ----------
    raw = mne.io.read_raw_fif(F_NAME, preload=True)
    raw.filter(0.5, 30.0)
    raw.set_eeg_reference("average")
    sfreq = raw.info["sfreq"]

    avail = [ch for ch in DETECT_CH if ch in raw.ch_names]
    if not avail:
        raise SystemExit(f"Нет ни одного из каналов {DETECT_CH}")

    # ---------- Полоса 6-15 Гц, усреднённый сигнал для детекции ----------
    band_raw = raw.copy().filter(*BAND)
    sig = band_raw.get_data(picks=avail).mean(axis=0)

    # ---------- Трек доминирующей частоты ----------
    t, dom_freq, dom_power, f_full, Sxx_full = dominant_freq_track(sig, sfreq)

    # порог по мощности: учитываем только окна с реальным пиком (выше медианы)
    gate = dom_power > np.median(dom_power)

    runs = find_stable_runs(t, dom_freq, gate, MIN_DURATION, FREQ_TOL)

    # ---------- Сводная таблица + региональность ----------
    picks_eeg = mne.pick_types(raw.info, eeg=True)
    eeg_names = [raw.ch_names[i] for i in picks_eeg]
    bands = {"theta": (4, 8), "alpha": (8, 13), "beta": (13, 30)}

    def band_name(fr):
        for b, (lo, hi) in bands.items():
            if lo <= fr < hi:
                return b
        return "n/a"

    rows = []
    region_accum = np.zeros(len(picks_eeg))
    total_dur = 0.0
    for k, r in enumerate(runs, 1):
        s, e, fr = r["start"], r["end"], r["freq"]
        reg = regional_power(raw, picks_eeg, s, e, fr, REGION_HALF, sfreq)
        region_accum += reg * (e - s)
        total_dur += (e - s)
        top_ch = eeg_names[int(np.argmax(reg))]
        rows.append({
            "interval": k,
            "start_s": round(s, 2),
            "end_s": round(e, 2),
            "duration_s": round(e - s, 2),
            "stable_freq_Hz": round(fr, 2),
            "band": band_name(fr),
            "power_uV2": round(float(reg.sum()) * 1e12, 2),
            "max_channel": top_ch,
        })
    table = pd.DataFrame(rows)
    region_mean = region_accum / total_dur if total_dur > 0 else region_accum

    # ===================== ГРАФИКИ =====================
    # 1) Спектрограмма + трек частоты + устойчивые интервалы
    # fig, ax = plt.subplots(figsize=(15, 5))
    # fmask = (f_full >= 1) & (f_full <= 30)
    # ax.pcolormesh(t, f_full[fmask], 10 * np.log10(Sxx_full[fmask] + 1e-20),
    #               shading="gouraud", cmap="viridis")
    # ax.plot(t, dom_freq, color="white", lw=0.6, alpha=0.5, label="доминирующая частота (6-15 Гц)")
    # ax.axhspan(BAND[0], BAND[1], color="white", alpha=0.08)
    # for r in runs:
    #     ax.plot([r["start"], r["end"]], [r["freq"], r["freq"]],
    #             color="red", lw=3, solid_capstyle="butt")
    # ax.set_ylim(1, 30)
    # ax.set_xlabel("Время, с")
    # ax.set_ylabel("Частота, Гц")
    # ax.set_title("Спектрограмма и устойчивые частотные фрагменты (красным)")
    # ax.legend(loc="upper right")
    # fig.tight_layout()
    # fig.savefig("stable_spectrogram.png", dpi=120)
    # plt.close(fig)

    # # 2) Топокарта средней региональной мощности устойчивых фрагментов
    # try:
    #     info = mne.pick_info(raw.info, picks_eeg)
    #     fig, ax = plt.subplots(figsize=(6, 5))
    #     mne.viz.plot_topomap(region_mean, info, axes=ax, show=False,
    #                          cmap="Reds", contours=4)
    #     ax.set_title("Региональная представленность\n(средняя мощность устойчивых частот)")
    #     fig.tight_layout()
    #     fig.savefig("stable_topomap.png", dpi=120)
    #     plt.close(fig)
    #     topo_ok = True
    # except Exception as ex:
    #     # запасной вариант: столбики по каналам
    #     topo_ok = False
    #     order = np.argsort(region_mean)[::-1]
    #     fig, ax = plt.subplots(figsize=(12, 4))
    #     ax.bar([eeg_names[i] for i in order], region_mean[order] * 1e12, color="firebrick")
    #     ax.set_ylabel("Мощность, мкВ²")
    #     ax.set_title(f"Региональная мощность по каналам (топокарта недоступна: {ex})")
    #     plt.xticks(rotation=90)
    #     fig.tight_layout()
    #     fig.savefig("stable_topomap.png", dpi=120)
    #     plt.close(fig)
        # ===================== ГРАФИКИ (улучшенная визуализация) =====================
    # 1) Спектрограмма + трек частоты + устойчивые интервалы
    fig, ax = plt.subplots(figsize=(15, 5))
    fmask = (f_full >= 1) & (f_full <= 30)
    
    # Логарифмическая мощность в децибелах
    power_db = 10 * np.log10(Sxx_full[fmask] + 1e-20)
    
    # Автоматический контраст: используем 5-й и 95-й процентили
    vmin = np.percentile(power_db, 5)
    vmax = np.percentile(power_db, 95)
    
    # Цветовая карта plasma с хорошим контрастом
    mesh = ax.pcolormesh(t, f_full[fmask], power_db,
                         shading="gouraud", cmap="plasma",
                         vmin=vmin, vmax=vmax)
    plt.colorbar(mesh, ax=ax, label="Мощность (дБ)")
    
    # Яркая линия доминирующей частоты с чёрной обводкой для лучшей читаемости
    import matplotlib.patheffects as pe
    ax.plot(t, dom_freq, color="white", lw=1.5, alpha=1.0,
            path_effects=[pe.Stroke(linewidth=3, foreground='black'), pe.Normal()],
            label="Доминирующая частота (6-15 Гц)")
    
    ax.axhspan(BAND[0], BAND[1], color="white", alpha=0.08)
    for r in runs:
        ax.plot([r["start"], r["end"]], [r["freq"], r["freq"]],
                color="red", lw=3, solid_capstyle="butt")
    
    ax.set_ylim(1, 30)
    ax.set_xlabel("Время, с")
    ax.set_ylabel("Частота, Гц")
    ax.set_title("Спектрограмма и устойчивые частотные фрагменты (красным)")
    ax.legend(loc="upper right")
    fig.tight_layout()
    fig.savefig("stable_spectrogram.png", dpi=120)
    plt.close(fig)

    # ===================== СВОДКА =====================
    rec_len = raw.times[-1]
    print("=" * 64)
    print("СВОДКА: устойчивые частоты (полоса 6-15 Гц)")
    print("=" * 64)
    print(f"Файл:                 {F_NAME}")
    print(f"Каналы детекции:      {', '.join(avail)}")
    print(f"Полоса:               {BAND[0]}-{BAND[1]} Гц")
    print(f"Критерий:             частота стабильна +/-{FREQ_TOL} Гц >= {MIN_DURATION} с")
    print(f"Длительность записи:  {rec_len:.1f} с")
    print(f"Найдено фрагментов:   {len(runs)}")
    print(f"Суммарно:             {total_dur:.1f} с ({100 * total_dur / rec_len:.1f}% записи)")
    print()
    if len(table):
        print(table.to_string(index=False))
    else:
        print("Ничего не найдено — попробуй смягчить FREQ_TOL или MIN_DURATION.")
    print("Спектрограмма: stable_spectrogram.png")

    table.to_csv("stable_intervals.csv", index=False, encoding="utf-8")
    print("Таблица: stable_intervals.csv")


if __name__ == "__main__":
    main()
