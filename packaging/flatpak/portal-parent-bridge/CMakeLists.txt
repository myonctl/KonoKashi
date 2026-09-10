cmake_minimum_required(VERSION 3.20)
project(konokashi_portal_parent LANGUAGES CXX)

find_package(Qt6 6.11 REQUIRED COMPONENTS Gui)
find_package(Qt6GuiPrivate 6.11 REQUIRED)

add_library(konokashi_portal_parent SHARED portal_parent.cpp)
target_compile_features(konokashi_portal_parent PRIVATE cxx_std_17)
target_link_libraries(konokashi_portal_parent PRIVATE Qt6::GuiPrivate)
set_target_properties(
    konokashi_portal_parent
    PROPERTIES
        CXX_VISIBILITY_PRESET hidden
        VISIBILITY_INLINES_HIDDEN YES
)

install(TARGETS konokashi_portal_parent LIBRARY DESTINATION lib)
