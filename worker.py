"""
Расчётный воркер для C++/Qt интерфейса (eeg_gui_cpp).

Два режима работы:

  1) Разовый вызов (для отладки):
       worker.py load    --file F.fif --out DIR
       worker.py analyze --file F.fif --tmin A --tmax B
                         --channels c1,c2,.. --metric M --out DIR

  2) Сервер (так его запускает GUI — чтобы не перечитывать .fif каждый раз):
       worker.py serve
     читает из stdin по одной JSON-команде в строке, держит запись ЭЭГ
     в памяти, на каждую команду пишет result.json и печатает строку "DONE".

Общение с GUI — через файлы: result.json (+ PNG-картинки для analyze).
Вся "математика" переиспользуется из target_fragment_analysis.py.
"""

import os
import sys
import json
import argparse
import traceback

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mne
from scipy.signal import spectrogram

from target_fragment_analysis import (
    channel_psd,
    alpha_coefficients,
    detect_target_fragment,
    BAND,
    ANALYSIS_BAND,
    ALPHA_BAND,
    DETECT_CH,
)

mne.set_log_level("ERROR")

METRIC_KEYS = [
    "modal_freq_Hz", "centroid_Hz", "freq_spread_Hz", "fwhm_Hz",
    "alpha_index_pct", "alpha_power_uV2", "alpha_theta_ratio",
    "alpha_beta_ratio", "peakedness", "q_factor", "spectral_entropy",
    "delta_power_uV2", "theta_power_uV2", "beta_power_uV2",
]
METRIC_CMAP = {
    "modal_freq_Hz": "viridis", "centroid_Hz": "viridis",
    "freq_spread_Hz": "magma", "spectral_entropy": "magma",
}

# Русские подписи коэффициентов (для топокарты; в GUI те же).
METRIC_RU = {
    "modal_freq_Hz": "Модальная частота, Гц",
    "centroid_Hz": "Центроид, Гц",
    "freq_spread_Hz": "Частотный разброс, Гц",
    "fwhm_Hz": "Ширина пика (FWHM), Гц",
    "alpha_index_pct": "Альфа-индекс, %",
    "alpha_power_uV2": "Мощность альфы, мкВ²",
    "alpha_theta_ratio": "Альфа / тета",
    "alpha_beta_ratio": "Альфа / бета",
    "peakedness": "Пиковость",
    "q_factor": "Добротность (острота пика)",
    "spectral_entropy": "Спектральная энтропия",
    "delta_power_uV2": "Мощность дельты, мкВ²",
    "theta_power_uV2": "Мощность теты, мкВ²",
    "beta_power_uV2": "Мощность беты, мкВ²",
}


def write_result(out_dir, obj):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)


def load_raw(path):
    raw = mne.io.read_raw_fif(path, preload=True, verbose="ERROR")
    raw.filter(0.5, 30.0, verbose="ERROR")
    raw.set_eeg_reference("average", verbose="ERROR")
    return raw


# --------------------------------------------------------- расчётные команды
def do_load(raw, out_dir):
    sfreq = float(raw.info["sfreq"])
    picks = mne.pick_types(raw.info, eeg=True)
    names = [raw.ch_names[i] for i in picks]

    try:
        target, runs, _ = detect_target_fragment(raw, sfreq)
    except SystemExit:
        target, runs = None, []

    avail = [c for c in DETECT_CH if c in raw.ch_names]
    band = raw.copy().filter(*BAND, verbose="ERROR")
    sig = band.get_data(picks=avail).mean(axis=0)
    t = raw.times
    step = max(1, len(t) // 4000)               # прореживаем для отрисовки

    write_result(out_dir, {
        "ok": True,
        "channels": names,
        "sfreq": sfreq,
        "duration": float(t[-1]),
        "display": {"t": t[::step].tolist(),
                    "y": (sig[::step] * 1e6).tolist()},
        "runs": [{"start": r["start"], "end": r["end"],
                  "index": r["index"], "duration": r["duration"],
                  "freq": r["freq"]} for r in runs],
        "target": (None if target is None else
                   {"start": target["start"], "end": target["end"],
                    "index": target["index"], "duration": target["duration"],
                    "freq": target["freq"]}),
    })


def do_analyze(raw, tmin, tmax, channels_csv, metric, out_dir):
    sfreq = float(raw.info["sfreq"])
    sel = [c for c in channels_csv.split(",") if c]
    picks = [raw.ch_names.index(c) for c in sel]

    frag = raw.copy().crop(tmin=tmin, tmax=tmax)
    data = frag.get_data(picks=picks)

    rows, psd_by_ch = [], {}
    for k, ch in enumerate(sel):
        f, p = channel_psd(data[k], sfreq)
        psd_by_ch[ch] = (f, p)
        rows.append({"channel": ch, **alpha_coefficients(f, p)})
    table = pd.DataFrame(rows)

    os.makedirs(out_dir, exist_ok=True)
    csv_path = os.path.abspath(os.path.join(out_dir, "coefficients.csv"))
    table.to_csv(csv_path, index=False, encoding="utf-8")

    spec_png = os.path.abspath(os.path.join(out_dir, "spectrum.png"))
    spectro_png = os.path.abspath(os.path.join(out_dir, "spectrogram.png"))
    topo_png = os.path.abspath(os.path.join(out_dir, "topomap.png"))

    _render_spectrum(psd_by_ch, spec_png)
    _render_spectrogram(data.mean(axis=0), sfreq, tmin, spectro_png)
    _render_topomap(table, raw, picks, metric, topo_png)

    cols = ["channel"] + METRIC_KEYS
    out_rows = []
    for _, r in table.iterrows():
        row = [r["channel"]]
        for key in METRIC_KEYS:
            v = r[key]
            row.append(None if (isinstance(v, float) and np.isnan(v)) else
                       (float(v) if not isinstance(v, str) else v))
        out_rows.append(row)

    mf = table["modal_freq_Hz"]
    top = table.loc[table["alpha_power_uV2"].idxmax(), "channel"]
    write_result(out_dir, {
        "ok": True,
        "table": {"columns": cols, "rows": out_rows},
        "summary": {
            "modal_mean": float(mf.mean()), "modal_std": float(mf.std()),
            "spread_mean": float(table["freq_spread_Hz"].mean()),
            "alpha_index_mean": float(table["alpha_index_pct"].mean()),
            "top_channel": str(top),
        },
        "images": {"spectrum": spec_png, "spectrogram": spectro_png,
                   "topomap": topo_png},
        "csv": csv_path,
    })


def _render_spectrum(psd_by_ch, path):
    fig, ax = plt.subplots(figsize=(8, 5))
    for ch, (f, p) in psd_by_ch.items():
        ax.semilogy(f, p * 1e12, lw=1.0, label=ch)
    ax.axvspan(*ALPHA_BAND, color="orange", alpha=0.18, label="альфа 8–13 Гц")
    ax.set_xlim(*ANALYSIS_BAND)
    ax.set_xlabel("Частота, Гц")
    ax.set_ylabel("PSD, мкВ²/Гц (лог)")
    ax.set_title("Спектр выбранных отведений")
    if len(psd_by_ch) <= 12:
        ax.legend(fontsize=7, ncol=2)
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _render_spectrogram(sig, sfreq, tmin, path):
    fig, ax = plt.subplots(figsize=(8, 5))
    nper = max(32, min(len(sig), int(sfreq * 1.0)))
    nov = int(nper * 0.9)
    f, t, Sxx = spectrogram(sig, fs=sfreq, nperseg=nper, noverlap=nov,
                            scaling="density")
    m = (f >= ANALYSIS_BAND[0]) & (f <= ANALYSIS_BAND[1])
    S = 10 * np.log10(Sxx[m] + 1e-20)
    mesh = ax.pcolormesh(t + tmin, f[m], S, shading="auto", cmap="viridis")
    ax.set_ylim(*ANALYSIS_BAND)
    ax.set_xlabel("Время, с")
    ax.set_ylabel("Частота, Гц")
    ax.set_title("Спектрограмма (среднее по выбранным каналам)")
    fig.colorbar(mesh, ax=ax, label="дБ")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _render_topomap(table, raw, picks, metric, path):
    fig, ax = plt.subplots(figsize=(6, 5.5))
    sel = list(table["channel"])
    if len(sel) < 4:
        ax.text(0.5, 0.5, "Для карты головы выберите\nне менее 4 каналов",
                ha="center", va="center")
        ax.axis("off")
    else:
        info = mne.pick_info(raw.info, picks)
        vals = table[metric].to_numpy(dtype=float)
        if np.isnan(vals).any():
            fill = np.nanmean(vals)
            vals = np.nan_to_num(vals, nan=fill if np.isfinite(fill) else 0.0)
        try:
            im, _ = mne.viz.plot_topomap(
                vals, info, axes=ax, show=False,
                cmap=METRIC_CMAP.get(metric, "Reds"), contours=4)
            fig.colorbar(im, ax=ax, shrink=0.7)
            ax.set_title(METRIC_RU.get(metric, metric))
        except Exception as ex:
            ax.clear()
            ax.text(0.5, 0.5, f"Карта недоступна:\n{ex}",
                    ha="center", va="center")
            ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# --------------------------------------------------------------- режим сервера
def serve():
    """Держим одну запись в памяти и обслуживаем команды из stdin."""
    cache = {}

    def get_raw(path):
        if path not in cache:
            cache.clear()                 # храним только одну запись
            cache[path] = load_raw(path)
        return cache[path]

    while True:
        line = sys.stdin.readline()
        if not line:                      # stdin закрыт — GUI завершился
            break
        line = line.strip()
        if not line:
            continue
        out_dir = "."
        try:
            cmd = json.loads(line)
            out_dir = cmd.get("out", ".")
            kind = cmd.get("cmd")
            if kind == "quit":
                break
            elif kind == "load":
                do_load(get_raw(cmd["file"]), out_dir)
            elif kind == "analyze":
                do_analyze(get_raw(cmd["file"]), cmd["tmin"], cmd["tmax"],
                           cmd["channels"], cmd.get("metric", "alpha_power_uV2"),
                           out_dir)
            else:
                write_result(out_dir, {"ok": False, "error": f"неизвестная команда: {kind}"})
        except Exception as ex:
            write_result(out_dir, {"ok": False, "error": str(ex),
                                   "trace": traceback.format_exc()})
        sys.stdout.write("DONE\n")
        sys.stdout.flush()


# --------------------------------------------------------------- разовый вызов
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve")

    pl = sub.add_parser("load")
    pl.add_argument("--file", required=True)
    pl.add_argument("--out", required=True)

    pa = sub.add_parser("analyze")
    pa.add_argument("--file", required=True)
    pa.add_argument("--tmin", type=float, required=True)
    pa.add_argument("--tmax", type=float, required=True)
    pa.add_argument("--channels", required=True)
    pa.add_argument("--metric", default="alpha_power_uV2")
    pa.add_argument("--out", required=True)

    args = ap.parse_args()
    if args.cmd == "serve":
        serve()
    elif args.cmd == "load":
        do_load(load_raw(args.file), args.out)
    else:
        do_analyze(load_raw(args.file), args.tmin, args.tmax,
                   args.channels, args.metric, args.out)


if __name__ == "__main__":
    main()
