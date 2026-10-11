cmake_minimum_required(VERSION 3.24)
project(ComputerArtistKWinPlugin VERSION 0.1 LANGUAGES C CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_AUTOMOC ON)
find_package(ECM REQUIRED NO_MODULE)
list(APPEND CMAKE_MODULE_PATH ${ECM_MODULE_PATH})
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets Quick DBus Network)
find_package(KWin 6.7 REQUIRED CONFIG)
find_package(PkgConfig REQUIRED)
pkg_check_modules(WaylandClient REQUIRED IMPORTED_TARGET wayland-client)
pkg_check_modules(XkbCommon REQUIRED IMPORTED_TARGET xkbcommon)
pkg_get_variable(WaylandProtocolsDir wayland-protocols pkgdatadir)
find_program(WaylandScanner wayland-scanner REQUIRED)
set(DataControlXml "${WaylandProtocolsDir}/staging/ext-data-control/ext-data-control-v1.xml")
add_custom_command(OUTPUT data-control-client.h data-control-protocol.c
    COMMAND ${WaylandScanner} client-header ${DataControlXml} data-control-client.h
    COMMAND ${WaylandScanner} private-code ${DataControlXml} data-control-protocol.c
    DEPENDS ${DataControlXml})
# Identify the exact plugin source so ca doctor can detect a stale loaded build.
# Hash "name:sha256" lines of every source in name order; the CLI does the same.
file(GLOB PluginSources CONFIGURE_DEPENDS RELATIVE ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/*.cpp ${CMAKE_CURRENT_SOURCE_DIR}/*.h
    ${CMAKE_CURRENT_SOURCE_DIR}/metadata.json ${CMAKE_CURRENT_SOURCE_DIR}/CMakeLists.txt)
list(SORT PluginSources)
set(PluginSourceLines "")
foreach(Source IN LISTS PluginSources)
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/${Source})
    file(SHA256 ${CMAKE_CURRENT_SOURCE_DIR}/${Source} SourceHash)
    string(APPEND PluginSourceLines "${Source}:${SourceHash}\n")
endforeach()
string(SHA256 PluginSourceHash "${PluginSourceLines}")
add_library(computerartist MODULE main.cpp capture.cpp clipboard.cpp keyboard.cpp data-control-client.h data-control-protocol.c)
target_include_directories(computerartist PRIVATE ${CMAKE_CURRENT_BINARY_DIR})
target_compile_definitions(computerartist PRIVATE CA_PLUGIN_SOURCE_SHA256="${PluginSourceHash}")
target_link_libraries(computerartist PRIVATE KWin::kwin Qt6::Network PkgConfig::WaylandClient PkgConfig::XkbCommon)
set_target_properties(computerartist PROPERTIES PREFIX "" LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/kwin/plugins")
install(TARGETS computerartist DESTINATION lib/qt6/plugins/kwin/plugins)
