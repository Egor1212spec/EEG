#include "signalview.h"

#include <QPainter>
#include <QPainterPath>
#include <QMouseEvent>
#include <algorithm>

SignalView::SignalView(QWidget *parent) : QWidget(parent) {
    setMinimumHeight(180);
    setMouseTracking(true);
}

void SignalView::setData(const QVector<double> &t, const QVector<double> &y,
                         const QVector<Run> &runs, double duration) {
    t_ = t;
    y_ = y;
    runs_ = runs;
    dur_ = duration;
    hasData_ = !t_.isEmpty();
    if (hasData_) {
        ymin_ = *std::min_element(y_.begin(), y_.end());
        ymax_ = *std::max_element(y_.begin(), y_.end());
        if (ymax_ - ymin_ < 1e-9) { ymin_ -= 1; ymax_ += 1; }
    }
    cache_ = QPixmap();   // данные сменились — пересоберём фон
    update();
}

void SignalView::setSelection(double tmin, double tmax) {
    selMin_ = tmin;
    selMax_ = tmax;
    update();
}

void SignalView::clearData() {
    t_.clear(); y_.clear(); runs_.clear();
    hasData_ = false;
    selMin_ = selMax_ = -1.0;
    cache_ = QPixmap();
    update();
}

QRect SignalView::plotRect() const {
    // поля под подписи осей
    return QRect(54, 10, width() - 64, height() - 40);
}

double SignalView::xToTime(int px) const {
    QRect r = plotRect();
    double frac = double(px - r.left()) / std::max(1, r.width());
    frac = std::clamp(frac, 0.0, 1.0);
    return frac * dur_;
}

int SignalView::timeToX(double tt) const {
    QRect r = plotRect();
    double frac = dur_ > 0 ? tt / dur_ : 0.0;
    return r.left() + int(frac * r.width());
}

int SignalView::valToY(double v) const {
    QRect r = plotRect();
    double frac = (v - ymin_) / (ymax_ - ymin_);
    return r.bottom() - int(frac * r.height());
}

void SignalView::buildCache() {
    qreal dpr = devicePixelRatioF();
    cache_ = QPixmap(size() * dpr);
    cache_.setDevicePixelRatio(dpr);
    cache_.fill(Qt::white);

    QPainter p(&cache_);
    QRect r = plotRect();

    if (!hasData_) {
        p.setPen(Qt::gray);
        p.drawText(rect(), Qt::AlignCenter, "Откройте запись (.fif)");
        return;
    }

    // рамка области графика
    p.setPen(QColor(180, 180, 180));
    p.drawRect(r);

    // устойчивые интервалы: зелёные полосы, целевой — оранжевая
    for (const Run &run : runs_) {
        int x1 = timeToX(run.start);
        int x2 = timeToX(run.end);
        QColor c = run.target ? QColor(255, 165, 0, 90) : QColor(60, 179, 113, 60);
        p.fillRect(QRect(x1, r.top(), std::max(1, x2 - x1), r.height()), c);
    }

    // сам сигнал
    p.setPen(QPen(QColor(60, 60, 60), 1));
    QPainterPath path;
    for (int i = 0; i < t_.size(); ++i) {
        int x = timeToX(t_[i]);
        int yv = valToY(y_[i]);
        if (i == 0) path.moveTo(x, yv);
        else path.lineTo(x, yv);
    }
    p.drawPath(path);

    // подписи осей
    p.setPen(Qt::black);
    p.drawText(QRect(0, r.top() - 2, 48, 14), Qt::AlignRight, QString::number(ymax_, 'f', 0));
    p.drawText(QRect(0, r.bottom() - 12, 48, 14), Qt::AlignRight, QString::number(ymin_, 'f', 0));
    p.drawText(2, (r.top() + r.bottom()) / 2, "мкВ");
    p.drawText(r.left(), r.bottom() + 16, "0 с");
    p.drawText(r.right() - 40, r.bottom() + 16, QString::number(dur_, 'f', 0) + " с");
}

void SignalView::paintEvent(QPaintEvent *) {
    if (cache_.isNull() || cache_.size() != size() * devicePixelRatioF())
        buildCache();

    QPainter p(this);
    p.drawPixmap(0, 0, cache_);     // фон из кэша (сигнал + интервалы)

    // поверх — только текущее выделение (дёшево, рисуется каждый кадр)
    if (hasData_ && selMin_ >= 0 && selMax_ > selMin_) {
        QRect r = plotRect();
        int x1 = timeToX(selMin_);
        int x2 = timeToX(selMax_);
        p.fillRect(QRect(x1, r.top(), std::max(1, x2 - x1), r.height()),
                   QColor(30, 110, 220, 70));
        p.setPen(QPen(QColor(30, 110, 220), 1, Qt::DashLine));
        p.drawLine(x1, r.top(), x1, r.bottom());
        p.drawLine(x2, r.top(), x2, r.bottom());
    }
}

void SignalView::resizeEvent(QResizeEvent *) {
    cache_ = QPixmap();   // размер изменился — пересоберём фон
}

void SignalView::mousePressEvent(QMouseEvent *e) {
    if (!hasData_ || e->button() != Qt::LeftButton) return;
    dragging_ = true;
    dragStartT_ = xToTime(int(e->position().x()));
    selMin_ = selMax_ = dragStartT_;
    update();
}

void SignalView::mouseMoveEvent(QMouseEvent *e) {
    if (!dragging_) return;
    double tt = xToTime(int(e->position().x()));
    selMin_ = std::min(dragStartT_, tt);
    selMax_ = std::max(dragStartT_, tt);
    update();
}

void SignalView::mouseReleaseEvent(QMouseEvent *e) {
    if (!dragging_ || e->button() != Qt::LeftButton) return;
    dragging_ = false;
    if (selMax_ - selMin_ >= 0.2)
        emit selectionChanged(selMin_, selMax_);
    update();
}
