#pragma once
#include <QLabel>
#include <QPixmap>

// QLabel, который держит исходную картинку и масштабирует её под свой
// размер, сохраняя пропорции (для PNG из matplotlib).
class ImageView : public QLabel {
    Q_OBJECT
public:
    explicit ImageView(QWidget *parent = nullptr);
    void setImagePath(const QString &path);
    void clearImage();

protected:
    void resizeEvent(QResizeEvent *) override;

private:
    QPixmap orig_;
    void rescale();
};
