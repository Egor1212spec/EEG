#pragma once
#include <QWidget>
#include <QVector>
#include <QPixmap>

// Один устойчивый интервал на временной шкале.
struct Run {
    double start;
    double end;
    bool target;   // true — это целевой (самый длинный) фрагмент
};

// Виджет, который рисует сигнал во времени, подсвечивает устойчивые
// интервалы и позволяет мышью выделить фрагмент (потянуть по горизонтали).
class SignalView : public QWidget {
    Q_OBJECT
public:
    explicit SignalView(QWidget *parent = nullptr);

    void setData(const QVector<double> &t, const QVector<double> &y,
                 const QVector<Run> &runs, double duration);
    void setSelection(double tmin, double tmax);
    void clearData();

signals:
    void selectionChanged(double tmin, double tmax);

protected:
    void paintEvent(QPaintEvent *) override;
    void resizeEvent(QResizeEvent *) override;
    void mousePressEvent(QMouseEvent *) override;
    void mouseMoveEvent(QMouseEvent *) override;
    void mouseReleaseEvent(QMouseEvent *) override;

private:
    QVector<double> t_, y_;
    QVector<Run> runs_;
    double dur_ = 0.0, ymin_ = 0.0, ymax_ = 0.0;
    double selMin_ = -1.0, selMax_ = -1.0;
    bool hasData_ = false;

    bool dragging_ = false;
    double dragStartT_ = 0.0;

    QPixmap cache_;        // отрисованный сигнал + интервалы (кэш фона)
    void buildCache();

    QRect plotRect() const;
    double xToTime(int px) const;
    int timeToX(double tt) const;
    int valToY(double v) const;
};
