"""
ЭЭГ-анализатор (Qt) — интерактивный разбор записи по фрагментам.

Что умеет:
  * открыть .fif запись ЭЭГ;
  * показать сигнал во времени и подсветить цветом устойчивые интервалы
    (зелёным — все устойчивые участки, оранжевым — самый длинный, "целевой");
  * выделить мышью любой фрагмент прямо на сигнале (потянуть по горизонтали);
  * выбрать набор каналов (отведений), по которым вести расчёт;
  * посчитать по выделенному фрагменту и выбранным каналам:
        - таблицу коэффициентов,
        - спектр (PSD),
        - спектрограмму,
        - карту распределения по голове (топокарту);
  * выгрузить коэффициенты в CSV.

Сам расчёт коэффициентов берётся из уже написанного target_fragment_analysis.py.
"""

import os
import sys

import numpy as np
import pandas as pd
import mne
from scipy.signal import spectrogram

# Расчётная "начинка" — переиспользуем то, что уже написано и проверено.
from target_fragment_analysis import (
    channel_psd,
    alpha_coefficients,
    detect_target_fragment,
    BAND,
    ANALYSIS_BAND,
    ALPHA_BAND,
    DETECT_CH,
    CSV_DIR,
)

from PyQt6 import QtCore, QtWidgets
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavToolbar,
)
from matplotlib.figure import Figure
from matplotlib.widgets import SpanSelector

# Порядок и оформление метрик, которые можно вывести на топокарту.
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


class EEGAnalyzer(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ЭЭГ-анализатор фрагментов")
        self.resize(1400, 880)

        # ---- состояние ----
        self.raw = None            # загруженная запись MNE
        self.sfreq = None          # частота дискретизации
        self.eeg_names = []        # имена ЭЭГ-каналов
        self.runs = []             # все устойчивые интервалы
        self.target = None         # самый длинный (целевой) интервал
        self.disp_t = None         # ось времени для отображаемого сигнала
        self.disp_sig = None       # сам отображаемый сигнал
        self.sel_tmin = None       # границы выделенного фрагмента
        self.sel_tmax = None
        self.sel_patch = None      # подсветка выделения на графике
        self.last_table = None     # последняя посчитанная таблица (для CSV)

        self._build_ui()

    # ============================================================== UI
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)

        # ---------- левая панель управления ----------
        left = QtWidgets.QVBoxLayout()
        root.addLayout(left, 0)

        self.btn_open = QtWidgets.QPushButton("Открыть .fif…")
        self.btn_open.clicked.connect(self.on_open)
        left.addWidget(self.btn_open)

        self.lbl_file = QtWidgets.QLabel("файл не загружен")
        self.lbl_file.setWordWrap(True)
        left.addWidget(self.lbl_file)

        left.addWidget(self._hline())
        left.addWidget(QtWidgets.QLabel("<b>Каналы для расчёта</b>"))

        self.ch_list = QtWidgets.QListWidget()
        self.ch_list.setMinimumWidth(180)
        left.addWidget(self.ch_list, 1)

        row = QtWidgets.QHBoxLayout()
        b_all = QtWidgets.QPushButton("Все")
        b_none = QtWidgets.QPushButton("Снять")
        b_occ = QtWidgets.QPushButton("Затылочные")
        b_all.clicked.connect(lambda: self._set_all_channels(True))
        b_none.clicked.connect(lambda: self._set_all_channels(False))
        b_occ.clicked.connect(self._select_occipital)
        for b in (b_all, b_none, b_occ):
            row.addWidget(b)
        left.addLayout(row)

        left.addWidget(self._hline())
        left.addWidget(QtWidgets.QLabel("<b>Метрика для карты головы</b>"))
        self.metric_box = QtWidgets.QComboBox()
        self.metric_box.addItems(METRIC_KEYS)
        self.metric_box.setCurrentText("alpha_power_uV2")
        self.metric_box.currentTextChanged.connect(self._refresh_topomap)
        left.addWidget(self.metric_box)

        left.addWidget(self._hline())
        self.lbl_sel = QtWidgets.QLabel("фрагмент не выделен")
        self.lbl_sel.setWordWrap(True)
        left.addWidget(self.lbl_sel)

        self.btn_calc = QtWidgets.QPushButton("Рассчитать по фрагменту")
        self.btn_calc.clicked.connect(self.on_calc)
        self.btn_calc.setEnabled(False)
        left.addWidget(self.btn_calc)

        self.btn_target = QtWidgets.QPushButton("Взять целевой фрагмент")
        self.btn_target.clicked.connect(self._use_target_fragment)
        self.btn_target.setEnabled(False)
        left.addWidget(self.btn_target)

        self.btn_csv = QtWidgets.QPushButton("Экспорт CSV")
        self.btn_csv.clicked.connect(self.on_export)
        self.btn_csv.setEnabled(False)
        left.addWidget(self.btn_csv)

        left.addStretch(1)

        # ---------- правая часть: график-выделение + вкладки ----------
        right = QtWidgets.QVBoxLayout()
        root.addLayout(right, 1)

        # Верх: сигнал, по которому выделяем фрагмент.
        self.fig_sig = Figure(figsize=(9, 3))
        self.canvas_sig = FigureCanvas(self.fig_sig)
        self.ax_sig = self.fig_sig.add_subplot(111)
        self.ax_sig.set_title("Откройте запись (.fif)")
        right.addWidget(self.canvas_sig, 2)

        # Низ: результаты по вкладкам.
        self.tabs = QtWidgets.QTabWidget()
        right.addWidget(self.tabs, 3)

        # вкладка "Коэффициенты"
        tab_tbl = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(tab_tbl)
        self.table = QtWidgets.QTableWidget()
        v.addWidget(self.table, 1)
        self.lbl_summary = QtWidgets.QLabel("—")
        self.lbl_summary.setWordWrap(True)
        v.addWidget(self.lbl_summary)
        self.tabs.addTab(tab_tbl, "Коэффициенты")

        # вкладка "Спектр"
        self.canvas_psd, self.fig_psd = self._make_canvas_tab("Спектр (PSD)")
        # вкладка "Спектрограмма"
        self.canvas_spec, self.fig_spec = self._make_canvas_tab("Спектрограмма")
        # вкладка "Голова"
        self.canvas_topo, self.fig_topo = self._make_canvas_tab("Голова (карта)")

        self.statusBar().showMessage("Готов. Откройте .fif-файл.")

    def _make_canvas_tab(self, title):
        tab = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(tab)
        fig = Figure(figsize=(7, 4.5))
        canvas = FigureCanvas(fig)
        v.addWidget(NavToolbar(canvas, tab))
        v.addWidget(canvas, 1)
        self.tabs.addTab(tab, title)
        return canvas, fig

    @staticmethod
    def _hline():
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        return line

    # ============================================================ загрузка
    def on_open(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Выберите запись ЭЭГ", "", "FIF (*.fif);;Все файлы (*)")
        if not path:
            return
        self.load_file(path)

    def load_file(self, path):
        try:
            self.statusBar().showMessage("Загрузка…")
            QtWidgets.QApplication.processEvents()
            raw = mne.io.read_raw_fif(path, preload=True, verbose="ERROR")
            raw.filter(0.5, 30.0, verbose="ERROR")
            raw.set_eeg_reference("average", verbose="ERROR")
        except Exception as ex:
            QtWidgets.QMessageBox.critical(self, "Ошибка", f"Не удалось открыть файл:\n{ex}")
            return

        self.raw = raw
        self.sfreq = raw.info["sfreq"]
        picks = mne.pick_types(raw.info, eeg=True)
        self.eeg_names = [raw.ch_names[i] for i in picks]
        self.lbl_file.setText(f"<b>{os.path.basename(path)}</b><br>"
                              f"каналов ЭЭГ: {len(self.eeg_names)}, "
                              f"sfreq: {self.sfreq:g} Гц, "
                              f"длит.: {raw.times[-1]:.1f} с")

        # список каналов с галочками
        self.ch_list.clear()
        for name in self.eeg_names:
            it = QtWidgets.QListWidgetItem(name)
            it.setFlags(it.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.CheckState.Checked)
            self.ch_list.addItem(it)

        # детекция устойчивых интервалов (та же логика, что в скрипте)
        try:
            self.target, self.runs, _ = detect_target_fragment(raw, self.sfreq)
        except SystemExit as ex:
            self.target, self.runs = None, []
            self.statusBar().showMessage(str(ex))

        # сигнал для отображения = усреднённые каналы детекции в полосе BAND
        avail = [c for c in DETECT_CH if c in raw.ch_names]
        band = raw.copy().filter(*BAND, verbose="ERROR")
        self.disp_sig = band.get_data(picks=avail).mean(axis=0)
        self.disp_t = raw.times

        self._draw_signal()
        self._setup_span()

        self.btn_calc.setEnabled(True)
        self.btn_target.setEnabled(self.target is not None)
        # сразу предлагаем целевой фрагмент
        if self.target is not None:
            self._use_target_fragment()
        self.statusBar().showMessage("Запись загружена. Выделите фрагмент мышью на верхнем графике.")

    # ============================================================ график сигнала
    def _draw_signal(self):
        self.ax_sig.clear()
        # прореживаем для скорости отрисовки
        step = max(1, len(self.disp_t) // 20000)
        self.ax_sig.plot(self.disp_t[::step], self.disp_sig[::step] * 1e6,
                         lw=0.5, color="0.25")
        # подсветка всех устойчивых интервалов
        for r in self.runs:
            self.ax_sig.axvspan(r["start"], r["end"], color="tab:green", alpha=0.18)
        # целевой — ярче
        if self.target is not None:
            self.ax_sig.axvspan(self.target["start"], self.target["end"],
                                color="tab:orange", alpha=0.35,
                                label="целевой (самый длинный)")
        self.ax_sig.set_xlabel("Время, с")
        self.ax_sig.set_ylabel("мкВ")
        self.ax_sig.set_title("Сигнал (зелёным — устойчивые интервалы). "
                              "Потяните мышью, чтобы выделить фрагмент.")
        self.ax_sig.margins(x=0)
        self.sel_patch = None
        self.fig_sig.tight_layout()
        self.canvas_sig.draw_idle()

    def _setup_span(self):
        self.span = SpanSelector(
            self.ax_sig, self._on_span_select, "horizontal",
            useblit=True, interactive=True, drag_from_anywhere=True,
            props=dict(alpha=0.25, facecolor="tab:blue"))

    def _on_span_select(self, xmin, xmax):
        if xmax - xmin < 0.2:        # слишком короткий выбор игнорируем
            return
        self.sel_tmin = max(0.0, float(xmin))
        self.sel_tmax = min(float(self.disp_t[-1]), float(xmax))
        self.lbl_sel.setText(
            f"<b>Фрагмент:</b> {self.sel_tmin:.2f}–{self.sel_tmax:.2f} с "
            f"(длит. {self.sel_tmax - self.sel_tmin:.2f} с)")
        self.statusBar().showMessage("Фрагмент выделен. Нажмите «Рассчитать».")

    def _use_target_fragment(self):
        if self.target is None:
            return
        self.sel_tmin = self.target["start"]
        self.sel_tmax = self.target["end"]
        self.span.extents = (self.sel_tmin, self.sel_tmax)
        self.lbl_sel.setText(
            f"<b>Целевой фрагмент:</b> {self.sel_tmin:.2f}–{self.sel_tmax:.2f} с "
            f"(длит. {self.sel_tmax - self.sel_tmin:.2f} с)")
        self.canvas_sig.draw_idle()

    # ============================================================ каналы
    def _set_all_channels(self, checked):
        state = QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked
        for i in range(self.ch_list.count()):
            self.ch_list.item(i).setCheckState(state)

    def _select_occipital(self):
        want = set(DETECT_CH) | {"Pz", "Oz", "O1", "O2", "P3", "P4", "P7", "P8"}
        for i in range(self.ch_list.count()):
            it = self.ch_list.item(i)
            it.setCheckState(QtCore.Qt.CheckState.Checked if it.text() in want
                             else QtCore.Qt.CheckState.Unchecked)

    def _selected_channels(self):
        out = []
        for i in range(self.ch_list.count()):
            it = self.ch_list.item(i)
            if it.checkState() == QtCore.Qt.CheckState.Checked:
                out.append(it.text())
        return out

    # ============================================================ расчёт
    def on_calc(self):
        if self.raw is None:
            return
        if self.sel_tmin is None:
            QtWidgets.QMessageBox.information(self, "Нет фрагмента",
                                             "Сначала выделите фрагмент на графике.")
            return
        sel = self._selected_channels()
        if not sel:
            QtWidgets.QMessageBox.information(self, "Нет каналов",
                                             "Отметьте хотя бы один канал.")
            return
        try:
            self.statusBar().showMessage("Расчёт…")
            QtWidgets.QApplication.processEvents()
            self._compute(sel)
        except Exception as ex:
            QtWidgets.QMessageBox.critical(self, "Ошибка расчёта", str(ex))
            self.statusBar().showMessage("Ошибка расчёта.")

    def _compute(self, sel):
        picks = [self.raw.ch_names.index(c) for c in sel]
        frag = self.raw.copy().crop(tmin=self.sel_tmin, tmax=self.sel_tmax)
        data = frag.get_data(picks=picks)

        rows, psd_by_ch = [], {}
        for k, ch in enumerate(sel):
            f, p = channel_psd(data[k], self.sfreq)
            psd_by_ch[ch] = (f, p)
            rows.append({"channel": ch, **alpha_coefficients(f, p)})
        table = pd.DataFrame(rows)
        self.last_table = table
        self.psd_by_ch = psd_by_ch

        self._fill_table(table)
        self._plot_psd(psd_by_ch)
        self._plot_spectrogram(data.mean(axis=0))
        self._refresh_topomap()

        self.btn_csv.setEnabled(True)
        self.statusBar().showMessage(
            f"Готово: {len(sel)} каналов, фрагмент "
            f"{self.sel_tmin:.2f}–{self.sel_tmax:.2f} с.")

    def _fill_table(self, table):
        cols = ["channel"] + METRIC_KEYS
        self.table.setColumnCount(len(cols))
        self.table.setRowCount(len(table))
        self.table.setHorizontalHeaderLabels(cols)
        for r in range(len(table)):
            for c, col in enumerate(cols):
                val = table.iloc[r][col]
                txt = val if col == "channel" else (
                    "—" if pd.isna(val) else f"{val:g}")
                self.table.setItem(r, c, QtWidgets.QTableWidgetItem(str(txt)))
        self.table.resizeColumnsToContents()

        mf = table["modal_freq_Hz"]
        top = table.loc[table["alpha_power_uV2"].idxmax(), "channel"]
        self.lbl_summary.setText(
            f"<b>Усреднённо по выбранным отведениям:</b> "
            f"модальная частота {mf.mean():.2f} ± {mf.std():.2f} Гц; "
            f"частотный разброс {table['freq_spread_Hz'].mean():.3f} Гц; "
            f"альфа-индекс {table['alpha_index_pct'].mean():.1f} %; "
            f"макс. альфа-мощность — {top}.")

    def _plot_psd(self, psd_by_ch):
        self.fig_psd.clear()
        ax = self.fig_psd.add_subplot(111)
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
        self.fig_psd.tight_layout()
        self.canvas_psd.draw_idle()

    def _plot_spectrogram(self, sig):
        self.fig_spec.clear()
        ax = self.fig_spec.add_subplot(111)
        nper = max(32, min(len(sig), int(self.sfreq * 1.0)))
        nov = int(nper * 0.9)
        f, t, Sxx = spectrogram(sig, fs=self.sfreq, nperseg=nper,
                                noverlap=nov, scaling="density")
        m = (f >= ANALYSIS_BAND[0]) & (f <= ANALYSIS_BAND[1])
        S = 10 * np.log10(Sxx[m] + 1e-20)
        mesh = ax.pcolormesh(t + self.sel_tmin, f[m], S, shading="auto", cmap="viridis")
        ax.axhspan(*ALPHA_BAND, color="white", alpha=0.0)
        ax.set_ylim(*ANALYSIS_BAND)
        ax.set_xlabel("Время, с")
        ax.set_ylabel("Частота, Гц")
        ax.set_title("Спектрограмма (среднее по выбранным каналам)")
        self.fig_spec.colorbar(mesh, ax=ax, label="дБ")
        self.fig_spec.tight_layout()
        self.canvas_spec.draw_idle()

    def _refresh_topomap(self):
        if self.last_table is None:
            return
        metric = self.metric_box.currentText()
        table = self.last_table
        sel = list(table["channel"])
        self.fig_topo.clear()
        ax = self.fig_topo.add_subplot(111)
        if len(sel) < 4:
            ax.text(0.5, 0.5, "Для карты головы выберите\nне менее 4 каналов",
                    ha="center", va="center")
            ax.axis("off")
            self.canvas_topo.draw_idle()
            return
        picks = [self.raw.ch_names.index(c) for c in sel]
        info = mne.pick_info(self.raw.info, picks)
        vals = table[metric].to_numpy(dtype=float)
        if np.isnan(vals).any():
            fill = np.nanmean(vals)
            vals = np.nan_to_num(vals, nan=fill if np.isfinite(fill) else 0.0)
        try:
            im, _ = mne.viz.plot_topomap(
                vals, info, axes=ax, show=False,
                cmap=METRIC_CMAP.get(metric, "Reds"), contours=4)
            self.fig_topo.colorbar(im, ax=ax, shrink=0.7)
            ax.set_title(metric)
        except Exception as ex:
            ax.clear()
            ax.text(0.5, 0.5, f"Карта недоступна:\n{ex}", ha="center", va="center")
            ax.axis("off")
        self.fig_topo.tight_layout()
        self.canvas_topo.draw_idle()

    # ============================================================ экспорт
    def on_export(self):
        if self.last_table is None:
            return
        os.makedirs(CSV_DIR, exist_ok=True)
        default = os.path.join(
            CSV_DIR, f"coef_{self.sel_tmin:.1f}-{self.sel_tmax:.1f}s.csv")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Сохранить CSV", default, "CSV (*.csv)")
        if not path:
            return
        self.last_table.to_csv(path, index=False, encoding="utf-8")
        self.statusBar().showMessage(f"Сохранено: {path}")


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = EEGAnalyzer()
    win.show()
    # автозагрузка файла по умолчанию, если он рядом
    default = "NP042309_CE.fif"
    if os.path.exists(default):
        win.load_file(default)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
