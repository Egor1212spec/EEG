#pragma once
#include <QMainWindow>
#include <QString>
#include <QStringList>
#include <QVector>

class QListWidget;
class QComboBox;
class QLabel;
class QTableWidget;
class QPushButton;
class QTabWidget;
class QDoubleSpinBox;
class SignalView;
class ImageView;

class QProcess;

// Один устойчивый интервал (для таблицы «Устойчивые фрагменты»).
struct RunInfo {
    int index;
    double start, end, duration, freq;
    bool target;
};

class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(QWidget *parent = nullptr);
    ~MainWindow() override;
    void autoLoadDefault();          // подхватить NP042309_CE.fif, если он есть

private slots:
    void onOpen();
    void onCalc();
    void onExport();
    void onUseTarget();
    void onSelection(double tmin, double tmax);
    void onApplyManualTime();
    void onMetricChanged();
    void setAllChannels(bool checked);
    void selectOccipital();

private:
    // конфигурация (можно переопределить переменными окружения)
    QString python_, worker_, projDir_, cacheDir_;

    // состояние
    QString file_;
    double selMin_ = -1, selMax_ = -1, duration_ = 0;
    double targetStart_ = -1, targetEnd_ = -1;
    QString lastCsv_;
    bool haveResult_ = false;
    QVector<RunInfo> runsInfo_;       // все устойчивые фрагменты

    // виджеты
    SignalView *sigView_;
    QListWidget *chList_;
    QComboBox *metricBox_;
    QDoubleSpinBox *tminSpin_, *tmaxSpin_;
    QLabel *fileLbl_, *selLbl_, *summaryLbl_;
    QTableWidget *table_;
    QTableWidget *runsTable_;
    ImageView *imgSpec_, *imgSpectro_, *imgTopo_;
    QPushButton *btnCalc_, *btnTarget_, *btnCsv_;
    QTabWidget *tabs_;

    QProcess *proc_ = nullptr;        // постоянный процесс-воркер (держит .fif в памяти)

    void buildUi();
    bool startServer();
    bool sendCommand(const class QJsonObject &cmd);
    bool readResult(class QJsonObject &out);
    void loadFile(const QString &path);
    void updateSelectionUi(double tmin, double tmax);  // синхронизирует график, поля, подпись
    QStringList selectedChannels() const;
    void showError(const QString &msg);
};
