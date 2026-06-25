#include "mainwindow.h"
#include "signalview.h"
#include "imageview.h"

#include <QtWidgets>
#include <QProcess>
#include <QJsonDocument>
#include <QJsonObject>
#include <QJsonArray>
#include <QCoreApplication>
#include <QDir>
#include <QFile>
#include <algorithm>

static QString envOr(const char *name, const QString &def) {
    QByteArray v = qgetenv(name);
    return v.isEmpty() ? def : QString::fromLocal8Bit(v);
}

// Где лежит проект (рядом должен быть worker.py):
//   1) EEG_PROJECT_DIR;  2) поиск worker.py вверх от папки с программой;
//   3) захардкоженный путь;  4) текущая папка.
static QString resolveProjectDir() {
    QByteArray env = qgetenv("EEG_PROJECT_DIR");
    if (!env.isEmpty()) return QString::fromLocal8Bit(env);
    QDir d(QCoreApplication::applicationDirPath());
    for (int i = 0; i < 6; ++i) {
        if (QFile::exists(d.filePath("worker.py"))) return d.absolutePath();
        if (!d.cdUp()) break;
    }
    const QString hard = "/home/maths/vscode/EEG";
    if (QFile::exists(hard + "/worker.py")) return hard;
    return QDir::currentPath();
}

// Какой Python запускать:
//   1) EEG_PYTHON;  2) захардкоженный путь, если он существует;
//   3) "python3" из PATH (нужно заранее `conda activate eeg`).
static QString resolvePython() {
    QByteArray env = qgetenv("EEG_PYTHON");
    if (!env.isEmpty()) return QString::fromLocal8Bit(env);
    const QString hard = "/home/maths/miniconda3/envs/eeg/bin/python";
    if (QFile::exists(hard)) return hard;
    return "python3";
}

// Английский ключ коэффициента -> русское название (для таблицы и списка).
// Порядок важен: так метрики идут в выпадающем списке.
static const QList<QPair<QString, QString>> kMetrics = {
    {"modal_freq_Hz",     "Модальная частота, Гц"},
    {"centroid_Hz",       "Центроид, Гц"},
    {"freq_spread_Hz",    "Частотный разброс, Гц"},
    {"fwhm_Hz",           "Ширина пика (FWHM), Гц"},
    {"alpha_index_pct",   "Альфа-индекс, %"},
    {"alpha_power_uV2",   "Мощность альфы, мкВ²"},
    {"alpha_theta_ratio", "Альфа / тета"},
    {"alpha_beta_ratio",  "Альфа / бета"},
    {"peakedness",        "Пиковость"},
    {"q_factor",          "Добротность (острота пика)"},
    {"spectral_entropy",  "Спектральная энтропия"},
    {"delta_power_uV2",   "Мощность дельты, мкВ²"},
    {"theta_power_uV2",   "Мощность теты, мкВ²"},
    {"beta_power_uV2",    "Мощность беты, мкВ²"},
};

static QString metricRu(const QString &key) {
    if (key == "channel") return "Канал";
    for (const auto &m : kMetrics)
        if (m.first == key) return m.second;
    return key;
}

// Подгоняет ширину колонок под содержимое и добавляет небольшой запас,
// чтобы подписи помещались сразу, без ручного растягивания.
static void fitColumns(QTableWidget *t, int pad = 18) {
    t->resizeColumnsToContents();
    for (int c = 0; c < t->columnCount(); ++c)
        t->setColumnWidth(c, t->columnWidth(c) + pad);
}

MainWindow::MainWindow(QWidget *parent) : QMainWindow(parent) {
    // Пути определяются автоматически (env → автопоиск → запасной вариант),
    // поэтому программа переносима между машинами.
    python_  = resolvePython();
    projDir_ = resolveProjectDir();
    worker_  = projDir_ + "/worker.py";
    cacheDir_ = projDir_ + "/.gui_cache";

    setWindowTitle("ЭЭГ-анализатор фрагментов (C++/Qt)");
    resize(1400, 880);
    buildUi();
    startServer();
    statusBar()->showMessage("Готов. Откройте .fif-файл.");
}

MainWindow::~MainWindow() {
    if (proc_ && proc_->state() == QProcess::Running) {
        proc_->write("{\"cmd\":\"quit\"}\n");   // вежливо просим воркер выйти
        proc_->closeWriteChannel();
        if (!proc_->waitForFinished(2000))
            proc_->kill();
    }
}

// --------------------------------------------------------------- интерфейс
void MainWindow::buildUi() {
    QWidget *central = new QWidget;
    setCentralWidget(central);
    QHBoxLayout *root = new QHBoxLayout(central);

    // ---- левая панель ----
    QVBoxLayout *left = new QVBoxLayout;
    root->addLayout(left, 0);

    QPushButton *btnOpen = new QPushButton("Открыть .fif…");
    connect(btnOpen, &QPushButton::clicked, this, &MainWindow::onOpen);
    left->addWidget(btnOpen);

    fileLbl_ = new QLabel("файл не загружен");
    fileLbl_->setWordWrap(true);
    left->addWidget(fileLbl_);

    left->addWidget(new QLabel("<b>Каналы для расчёта</b>"));
    chList_ = new QListWidget;
    chList_->setMinimumWidth(190);
    // Никакого выделения/фокуса строки — клик в любом месте просто
    // включает/выключает канал.
    chList_->setSelectionMode(QAbstractItemView::NoSelection);
    chList_->setFocusPolicy(Qt::NoFocus);
    connect(chList_, &QListWidget::itemClicked, this,
            [](QListWidgetItem *it) {
                it->setCheckState(it->checkState() == Qt::Checked
                                      ? Qt::Unchecked : Qt::Checked);
            });
    left->addWidget(chList_, 1);

    QHBoxLayout *chBtns = new QHBoxLayout;
    QPushButton *bAll = new QPushButton("Все");
    QPushButton *bNone = new QPushButton("Снять");
    QPushButton *bOcc = new QPushButton("Затылочные");
    connect(bAll, &QPushButton::clicked, this, [this] { setAllChannels(true); });
    connect(bNone, &QPushButton::clicked, this, [this] { setAllChannels(false); });
    connect(bOcc, &QPushButton::clicked, this, &MainWindow::selectOccipital);
    chBtns->addWidget(bAll);
    chBtns->addWidget(bNone);
    chBtns->addWidget(bOcc);
    left->addLayout(chBtns);

    left->addWidget(new QLabel("<b>Метрика для карты головы</b>"));
    metricBox_ = new QComboBox;
    for (const auto &m : kMetrics)
        metricBox_->addItem(m.second, m.first);    // подпись рус., данные — англ. ключ
    metricBox_->setCurrentIndex(metricBox_->findData("alpha_power_uV2"));
    connect(metricBox_, &QComboBox::currentTextChanged,
            this, &MainWindow::onMetricChanged);
    left->addWidget(metricBox_);

    // --- ручной ввод времени фрагмента ---
    left->addWidget(new QLabel("<b>Время фрагмента вручную</b>"));
    QHBoxLayout *timeRow = new QHBoxLayout;
    tminSpin_ = new QDoubleSpinBox;
    tmaxSpin_ = new QDoubleSpinBox;
    for (QDoubleSpinBox *sp : {tminSpin_, tmaxSpin_}) {
        sp->setDecimals(2);
        sp->setRange(0.0, 1e6);
        sp->setSuffix(" с");
        sp->setSingleStep(0.5);
    }
    timeRow->addWidget(new QLabel("с"));
    timeRow->addWidget(tminSpin_);
    timeRow->addWidget(new QLabel("по"));
    timeRow->addWidget(tmaxSpin_);
    left->addLayout(timeRow);
    QPushButton *btnApplyTime = new QPushButton("Задать фрагмент по времени");
    connect(btnApplyTime, &QPushButton::clicked, this, &MainWindow::onApplyManualTime);
    left->addWidget(btnApplyTime);

    selLbl_ = new QLabel("фрагмент не выделен");
    selLbl_->setWordWrap(true);
    left->addWidget(selLbl_);

    btnCalc_ = new QPushButton("Рассчитать по фрагменту");
    btnCalc_->setEnabled(false);
    connect(btnCalc_, &QPushButton::clicked, this, &MainWindow::onCalc);
    left->addWidget(btnCalc_);

    btnTarget_ = new QPushButton("Взять целевой фрагмент");
    btnTarget_->setEnabled(false);
    connect(btnTarget_, &QPushButton::clicked, this, &MainWindow::onUseTarget);
    left->addWidget(btnTarget_);

    btnCsv_ = new QPushButton("Экспорт CSV");
    btnCsv_->setEnabled(false);
    connect(btnCsv_, &QPushButton::clicked, this, &MainWindow::onExport);
    left->addWidget(btnCsv_);

    left->addStretch(1);

    // ---- правая часть ----
    QVBoxLayout *right = new QVBoxLayout;
    root->addLayout(right, 1);

    sigView_ = new SignalView;
    connect(sigView_, &SignalView::selectionChanged,
            this, &MainWindow::onSelection);
    right->addWidget(sigView_, 2);

    tabs_ = new QTabWidget;
    right->addWidget(tabs_, 3);

    // вкладка коэффициентов
    QWidget *tabTbl = new QWidget;
    QVBoxLayout *vt = new QVBoxLayout(tabTbl);
    table_ = new QTableWidget;
    vt->addWidget(table_, 1);
    summaryLbl_ = new QLabel("—");
    summaryLbl_->setWordWrap(true);
    vt->addWidget(summaryLbl_);
    tabs_->addTab(tabTbl, "Коэффициенты");

    // вкладка со всеми устойчивыми фрагментами
    QWidget *tabRuns = new QWidget;
    QVBoxLayout *vr = new QVBoxLayout(tabRuns);
    vr->addWidget(new QLabel("Все устойчивые фрагменты "
                             "(двойной клик — перейти к фрагменту и посчитать):"));
    runsTable_ = new QTableWidget;
    runsTable_->setColumnCount(6);
    runsTable_->setHorizontalHeaderLabels(
        {"№", "Начало, с", "Конец, с", "Длит., с", "Частота, Гц", "Целевой"});
    runsTable_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    runsTable_->setSelectionBehavior(QAbstractItemView::SelectRows);
    connect(runsTable_, &QTableWidget::cellDoubleClicked, this,
            [this](int row, int) {
                if (row < 0 || row >= runsInfo_.size()) return;
                const RunInfo &ri = runsInfo_[row];
                updateSelectionUi(ri.start, ri.end);
                onCalc();
            });
    vr->addWidget(runsTable_);
    tabs_->addTab(tabRuns, "Устойчивые фрагменты");

    auto addImageTab = [this](const QString &title) {
        QScrollArea *area = new QScrollArea;
        area->setWidgetResizable(true);
        ImageView *iv = new ImageView;
        area->setWidget(iv);
        tabs_->addTab(area, title);
        return iv;
    };
    imgSpec_ = addImageTab("Спектр (PSD)");
    imgSpectro_ = addImageTab("Спектрограмма");
    imgTopo_ = addImageTab("Голова (карта)");
}

// --------------------------------------------------------------- worker
bool MainWindow::startServer() {
    if (proc_ && proc_->state() == QProcess::Running) return true;

    if (!QFile::exists(worker_)) {
        showError("Не найден worker.py:\n" + worker_ +
                  "\n\nУкажите папку проекта переменной окружения EEG_PROJECT_DIR "
                  "(там должны лежать worker.py и target_fragment_analysis.py).");
        return false;
    }

    delete proc_;
    proc_ = new QProcess(this);
    proc_->setWorkingDirectory(projDir_);
    // -u — небуферизованный вывод, чтобы "DONE" приходил сразу
    proc_->start(python_, {"-u", worker_, "serve"});
    if (!proc_->waitForStarted(8000)) {
        showError(
            "Не удалось запустить Python-воркер:\n" + python_ +
            "\n\nЧто проверить:\n"
            "• это интерпретатор Python с установленными mne, scipy, pandas, "
            "matplotlib;\n"
            "• либо задайте путь к нему: переменная EEG_PYTHON=/путь/к/python;\n"
            "• либо перед запуском выполните: conda activate eeg\n"
            "  (тогда подойдёт python3 из PATH).");
        return false;
    }
    return true;
}

// Отправляет одну команду постоянному воркеру и ждёт ответа "DONE".
// Запись .fif при этом не перечитывается — отсюда отсутствие лагов.
bool MainWindow::sendCommand(const QJsonObject &cmd) {
    if (!startServer()) return false;
    QDir().mkpath(cacheDir_);
    QFile::remove(cacheDir_ + "/result.json");

    QByteArray line = QJsonDocument(cmd).toJson(QJsonDocument::Compact) + "\n";
    QApplication::setOverrideCursor(Qt::WaitCursor);
    proc_->write(line);
    proc_->waitForBytesWritten(3000);

    QByteArray acc;
    bool done = false;
    while (proc_->state() == QProcess::Running) {
        if (!proc_->waitForReadyRead(120000)) break;
        acc += proc_->readAllStandardOutput();
        if (acc.contains("DONE")) { done = true; break; }
    }
    QApplication::restoreOverrideCursor();

    if (!done) {
        showError("Воркер не ответил:\n" +
                  QString::fromUtf8(proc_->readAllStandardError()));
        return false;
    }
    return true;
}

bool MainWindow::readResult(QJsonObject &out) {
    QFile f(cacheDir_ + "/result.json");
    if (!f.open(QIODevice::ReadOnly)) {
        showError("Нет result.json от воркера.");
        return false;
    }
    QJsonParseError err;
    QJsonDocument doc = QJsonDocument::fromJson(f.readAll(), &err);
    if (err.error != QJsonParseError::NoError || !doc.isObject()) {
        showError("Не удалось разобрать result.json.");
        return false;
    }
    out = doc.object();
    if (!out.value("ok").toBool(false)) {
        showError("Ошибка воркера:\n" + out.value("error").toString());
        return false;
    }
    return true;
}

// --------------------------------------------------------------- загрузка
void MainWindow::onOpen() {
    QString path = QFileDialog::getOpenFileName(
        this, "Выберите запись ЭЭГ", projDir_,
        "FIF (*.fif);;Все файлы (*)");
    if (!path.isEmpty()) loadFile(path);
}

void MainWindow::autoLoadDefault() {
    QString def = projDir_ + "/NP042309_CE.fif";
    if (QFile::exists(def)) loadFile(def);
}

void MainWindow::loadFile(const QString &path) {
    statusBar()->showMessage("Загрузка…");
    QJsonObject cmd{{"cmd", "load"}, {"file", path}, {"out", cacheDir_}};
    if (!sendCommand(cmd)) return;
    QJsonObject r;
    if (!readResult(r)) return;

    file_ = path;
    duration_ = r.value("duration").toDouble();
    double sfreq = r.value("sfreq").toDouble();
    tminSpin_->setRange(0.0, duration_);
    tmaxSpin_->setRange(0.0, duration_);

    // список каналов
    chList_->clear();
    QJsonArray chans = r.value("channels").toArray();
    for (const auto &c : chans) {
        QListWidgetItem *it = new QListWidgetItem(c.toString());
        // только Enabled: галочка видна (из-за setCheckState), но сам
        // индикатор клик не перехватывает — всё делаем в itemClicked.
        it->setFlags(Qt::ItemIsEnabled);
        it->setCheckState(Qt::Checked);
        chList_->addItem(it);
    }
    fileLbl_->setText(QString("<b>%1</b><br>каналов ЭЭГ: %2, sfreq: %3 Гц, "
                              "длит.: %4 с")
                          .arg(QFileInfo(path).fileName())
                          .arg(chans.size())
                          .arg(sfreq, 0, 'g', 4)
                          .arg(duration_, 0, 'f', 1));

    // сигнал + интервалы для отрисовки
    QJsonObject disp = r.value("display").toObject();
    QJsonArray ta = disp.value("t").toArray();
    QJsonArray ya = disp.value("y").toArray();
    QVector<double> t, y;
    t.reserve(ta.size());
    y.reserve(ya.size());
    for (int i = 0; i < ta.size(); ++i) {
        t.push_back(ta[i].toDouble());
        y.push_back(ya[i].toDouble());
    }
    QVector<Run> runs;
    QJsonValue tv = r.value("target");
    targetStart_ = targetEnd_ = -1;
    if (tv.isObject()) {
        targetStart_ = tv.toObject().value("start").toDouble();
        targetEnd_ = tv.toObject().value("end").toDouble();
    }
    runsInfo_.clear();
    for (const auto &rv : r.value("runs").toArray()) {
        QJsonObject ro = rv.toObject();
        double s = ro.value("start").toDouble();
        double e = ro.value("end").toDouble();
        bool isT = (qAbs(s - targetStart_) < 1e-6 && qAbs(e - targetEnd_) < 1e-6);
        runs.push_back({s, e, isT});
        runsInfo_.push_back({ro.value("index").toInt(), s, e,
                             ro.value("duration").toDouble(),
                             ro.value("freq").toDouble(), isT});
    }
    sigView_->setData(t, y, runs, duration_);

    // заполняем таблицу «Устойчивые фрагменты» (по убыванию длительности)
    std::sort(runsInfo_.begin(), runsInfo_.end(),
              [](const RunInfo &a, const RunInfo &b) {
                  return a.duration > b.duration;
              });
    runsTable_->setRowCount(runsInfo_.size());
    for (int i = 0; i < runsInfo_.size(); ++i) {
        const RunInfo &ri = runsInfo_[i];
        auto cell = [](const QString &s) {
            auto *it = new QTableWidgetItem(s);
            return it;
        };
        runsTable_->setItem(i, 0, cell(QString::number(ri.index)));
        runsTable_->setItem(i, 1, cell(QString::number(ri.start, 'f', 2)));
        runsTable_->setItem(i, 2, cell(QString::number(ri.end, 'f', 2)));
        runsTable_->setItem(i, 3, cell(QString::number(ri.duration, 'f', 2)));
        runsTable_->setItem(i, 4, cell(QString::number(ri.freq, 'f', 2)));
        runsTable_->setItem(i, 5, cell(ri.target ? "★ да" : ""));
        if (ri.target)
            for (int c = 0; c < 6; ++c) {
                runsTable_->item(i, c)->setBackground(QColor(255, 214, 153));
                runsTable_->item(i, c)->setForeground(Qt::black);  // читаемо на любой теме
            }
    }
    fitColumns(runsTable_);

    btnCalc_->setEnabled(true);
    btnTarget_->setEnabled(targetStart_ >= 0);
    statusBar()->showMessage("Запись загружена. Выделите фрагмент мышью.");

    if (targetStart_ >= 0) onUseTarget();   // сразу считаем целевой
}

// --------------------------------------------------------------- выделение
// Единая точка обновления выделения: график, поля времени и подпись.
void MainWindow::updateSelectionUi(double tmin, double tmax) {
    selMin_ = tmin;
    selMax_ = tmax;
    sigView_->setSelection(tmin, tmax);
    // обновляем поля, не вызывая повторных сигналов
    tminSpin_->blockSignals(true);
    tmaxSpin_->blockSignals(true);
    tminSpin_->setValue(tmin);
    tmaxSpin_->setValue(tmax);
    tminSpin_->blockSignals(false);
    tmaxSpin_->blockSignals(false);
    selLbl_->setText(QString("<b>Фрагмент:</b> %1–%2 с (длит. %3 с)")
                         .arg(tmin, 0, 'f', 2).arg(tmax, 0, 'f', 2)
                         .arg(tmax - tmin, 0, 'f', 2));
}

void MainWindow::onSelection(double tmin, double tmax) {
    updateSelectionUi(tmin, tmax);
    statusBar()->showMessage("Фрагмент выделен. Нажмите «Рассчитать».");
}

void MainWindow::onUseTarget() {
    if (targetStart_ < 0) return;
    updateSelectionUi(targetStart_, targetEnd_);
    onCalc();
}

void MainWindow::onApplyManualTime() {
    if (file_.isEmpty()) return;
    double a = tminSpin_->value();
    double b = tmaxSpin_->value();
    if (b - a < 0.2) {
        QMessageBox::information(this, "Некорректное время",
                                "Конец должен быть больше начала минимум на 0.2 с.");
        return;
    }
    if (duration_ > 0 && b > duration_) {
        QMessageBox::information(this, "Некорректное время",
                                QString("Конец больше длительности записи (%1 с).")
                                    .arg(duration_, 0, 'f', 1));
        return;
    }
    updateSelectionUi(a, b);
    onCalc();
}

// --------------------------------------------------------------- каналы
void MainWindow::setAllChannels(bool checked) {
    for (int i = 0; i < chList_->count(); ++i)
        chList_->item(i)->setCheckState(checked ? Qt::Checked : Qt::Unchecked);
}

void MainWindow::selectOccipital() {
    static const QStringList want = {"O1", "O2", "P3", "P4", "Pz", "Oz",
                                     "P7", "P8"};
    for (int i = 0; i < chList_->count(); ++i) {
        QListWidgetItem *it = chList_->item(i);
        it->setCheckState(want.contains(it->text()) ? Qt::Checked : Qt::Unchecked);
    }
}

QStringList MainWindow::selectedChannels() const {
    QStringList out;
    for (int i = 0; i < chList_->count(); ++i)
        if (chList_->item(i)->checkState() == Qt::Checked)
            out << chList_->item(i)->text();
    return out;
}

// --------------------------------------------------------------- расчёт
void MainWindow::onCalc() {
    if (file_.isEmpty()) return;
    if (selMin_ < 0) {
        QMessageBox::information(this, "Нет фрагмента",
                                "Сначала выделите фрагмент на графике.");
        return;
    }
    QStringList sel = selectedChannels();
    if (sel.isEmpty()) {
        QMessageBox::information(this, "Нет каналов",
                                "Отметьте хотя бы один канал.");
        return;
    }

    statusBar()->showMessage("Расчёт…");
    QJsonObject cmd{
        {"cmd", "analyze"}, {"file", file_},
        {"tmin", selMin_}, {"tmax", selMax_},
        {"channels", sel.join(",")},
        {"metric", metricBox_->currentData().toString()},
        {"out", cacheDir_}};
    if (!sendCommand(cmd)) return;

    QJsonObject r;
    if (!readResult(r)) return;

    // таблица
    QJsonObject tbl = r.value("table").toObject();
    QJsonArray cols = tbl.value("columns").toArray();
    QJsonArray rows = tbl.value("rows").toArray();
    table_->setColumnCount(cols.size());
    table_->setRowCount(rows.size());
    QStringList headers;
    for (const auto &c : cols) headers << metricRu(c.toString());
    table_->setHorizontalHeaderLabels(headers);
    for (int i = 0; i < rows.size(); ++i) {
        QJsonArray row = rows[i].toArray();
        for (int j = 0; j < row.size(); ++j) {
            QString txt;
            QJsonValue v = row[j];
            if (v.isString()) txt = v.toString();
            else if (v.isNull()) txt = "—";
            else txt = QString::number(v.toDouble(), 'g', 4);
            table_->setItem(i, j, new QTableWidgetItem(txt));
        }
    }
    fitColumns(table_);

    QJsonObject s = r.value("summary").toObject();
    summaryLbl_->setText(
        QString("<b>Усреднённо по выбранным отведениям:</b> "
                "модальная частота %1 ± %2 Гц; частотный разброс %3 Гц; "
                "альфа-индекс %4 %; макс. альфа-мощность — %5.")
            .arg(s.value("modal_mean").toDouble(), 0, 'f', 2)
            .arg(s.value("modal_std").toDouble(), 0, 'f', 2)
            .arg(s.value("spread_mean").toDouble(), 0, 'f', 3)
            .arg(s.value("alpha_index_mean").toDouble(), 0, 'f', 1)
            .arg(s.value("top_channel").toString()));

    // картинки
    QJsonObject im = r.value("images").toObject();
    imgSpec_->setImagePath(im.value("spectrum").toString());
    imgSpectro_->setImagePath(im.value("spectrogram").toString());
    imgTopo_->setImagePath(im.value("topomap").toString());

    lastCsv_ = r.value("csv").toString();
    haveResult_ = true;
    btnCsv_->setEnabled(true);
    statusBar()->showMessage(
        QString("Готово: %1 каналов, фрагмент %2–%3 с.")
            .arg(sel.size())
            .arg(selMin_, 0, 'f', 2).arg(selMax_, 0, 'f', 2));
}

void MainWindow::onMetricChanged() {
    if (haveResult_) onCalc();   // перерисовать карту под новую метрику
}

// --------------------------------------------------------------- экспорт
void MainWindow::onExport() {
    if (lastCsv_.isEmpty()) return;
    QString def = projDir_ + QString("/results/coef_%1-%2s.csv")
                                 .arg(selMin_, 0, 'f', 1)
                                 .arg(selMax_, 0, 'f', 1);
    QString path = QFileDialog::getSaveFileName(this, "Сохранить CSV", def,
                                                "CSV (*.csv)");
    if (path.isEmpty()) return;
    QFile::remove(path);
    if (QFile::copy(lastCsv_, path))
        statusBar()->showMessage("Сохранено: " + path);
    else
        showError("Не удалось сохранить CSV.");
}

void MainWindow::showError(const QString &msg) {
    QMessageBox::critical(this, "Ошибка", msg);
    statusBar()->showMessage("Ошибка.");
}
