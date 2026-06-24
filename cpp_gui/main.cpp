#include <QApplication>
#include <QTimer>
#include "mainwindow.h"

int main(int argc, char **argv) {
    QApplication app(argc, argv);

    bool selftest = false;
    for (int i = 1; i < argc; ++i)
        if (QString(argv[i]) == "--selftest") selftest = true;

    MainWindow w;
    w.show();
    w.autoLoadDefault();

    if (selftest)   // режим самопроверки: загрузил, посчитал — и выходим
        QTimer::singleShot(0, &app, &QApplication::quit);

    return app.exec();
}
