cmake_minimum_required(VERSION 3.16)
project(eeg_gui_cpp LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Widgets)

add_executable(eeg_gui_cpp
    main.cpp
    mainwindow.cpp
    signalview.cpp
    imageview.cpp
    mainwindow.h
    signalview.h
    imageview.h
)

target_link_libraries(eeg_gui_cpp PRIVATE Qt6::Widgets)
