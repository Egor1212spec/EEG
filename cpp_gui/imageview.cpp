#include "imageview.h"
#include <QResizeEvent>

ImageView::ImageView(QWidget *parent) : QLabel(parent) {
    setAlignment(Qt::AlignCenter);
    setMinimumSize(200, 150);
    setText("—");
}

void ImageView::setImagePath(const QString &path) {
    orig_.load(path);
    rescale();
}

void ImageView::clearImage() {
    orig_ = QPixmap();
    setText("—");
}

void ImageView::resizeEvent(QResizeEvent *e) {
    QLabel::resizeEvent(e);
    rescale();
}

void ImageView::rescale() {
    if (orig_.isNull()) return;
    setPixmap(orig_.scaled(size(), Qt::KeepAspectRatio,
                           Qt::SmoothTransformation));
}
