cmake_minimum_required(VERSION 3.14...3.22)

# ---- Project ----

# Note: update this to your new project's name and version
project(
  Brefv
  VERSION 1.0
  LANGUAGES CXX
)

set(PROJECT_INCLUDE_DIR ${CMAKE_CURRENT_SOURCE_DIR}/include/brefv)

# ---- Include guards ----

if(PROJECT_SOURCE_DIR STREQUAL PROJECT_BINARY_DIR)
  message(
    FATAL_ERROR
      "In-source builds not allowed. Please make a new directory (called a build directory) and run CMake from there."
  )
endif()

# ---- Add dependencies via CPM ----
# see https://github.com/TheLartians/CPM.cmake for more info

include(../../cmake/CPM.cmake)

# PackageProject.cmake will be used to make our target installable
CPMAddPackage("gh:TheLartians/PackageProject.cmake@1.8.0")

# ---- Compile protobuf definitions ----

find_package(Protobuf REQUIRED)
file(GLOB PROTOBUF_DEFINITION_FILES "../protos/*.proto")

set(MESSAGE_DIR ${PROJECT_INCLUDE_DIR}/messages)

# Compile message specifications
add_custom_command(
  OUTPUT ${MESSAGE_DIR}
  COMMAND ${CMAKE_COMMAND} -E make_directory ${MESSAGE_DIR}
  COMMAND protoc --proto_path ${CMAKE_CURRENT_SOURCE_DIR}/../protos --cpp_out ${MESSAGE_DIR} ${PROTOBUF_DEFINITION_FILES}
  DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/../protos
)
add_custom_target(
  generate_messages
  DEPENDS ${MESSAGE_DIR}
)

# ---- Generate tag_to_type map ----

set(TAGS_DIR ${PROJECT_INCLUDE_DIR}/tags)

# Compile message specifications
add_custom_command(
  OUTPUT ${TAGS_DIR}
  COMMAND ${CMAKE_COMMAND} -E make_directory ${TAGS_DIR}
  COMMAND jinja2 ${CMAKE_CURRENT_SOURCE_DIR}/tag_type_map_to_header.j2 ${CMAKE_CURRENT_SOURCE_DIR}/../tag_type_map.json > ${TAGS_DIR}/tags.hpp
  DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/tag_type_map_to_header.j2
)
add_custom_target(
  generate_type_map
  DEPENDS ${TAGS_DIR}
)

# ---- Add source files ----

# Note: globbing sources is considered bad practice as CMake's generators may not detect new files
# automatically. Keep that in mind when changing files, or explicitly mention them here.
file(GLOB_RECURSE headers CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/include/*.h")
file(GLOB_RECURSE sources CONFIGURE_DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/source/*.cpp")
file(GLOB_RECURSE protobuf_sources CONFIGURE_DEPENDS "${MESSAGE_DIR}/*.cc")

# ---- Create library ----

# Note: for header-only libraries change all PUBLIC flags to INTERFACE and create an interface
# target: add_library(${PROJECT_NAME} INTERFACE)
add_library(${PROJECT_NAME} ${headers} ${sources} ${protobuf_sources})
set_target_properties(${PROJECT_NAME} PROPERTIES CXX_STANDARD 17)

# being a cross-platform target, we enforce standards conformance on MSVC
target_compile_options(${PROJECT_NAME} PUBLIC "$<$<COMPILE_LANG_AND_ID:CXX,MSVC>:/permissive->")

# Link dependencies
target_link_libraries(${PROJECT_NAME} PUBLIC ${Protobuf_LIBRARIES})
add_dependencies(${PROJECT_NAME} generate_messages generate_type_map)

target_include_directories(
  ${PROJECT_NAME} PUBLIC $<BUILD_INTERFACE:${PROJECT_SOURCE_DIR}/include>
                         $<INSTALL_INTERFACE:include/${PROJECT_NAME}-${PROJECT_VERSION}>
)

# ---- Create an installable target ----
# this allows users to install and find the library via `find_package()`.

# the location where the project's version header will be placed should match the project's regular
# header paths
string(TOLOWER ${PROJECT_NAME}/version.h VERSION_HEADER_LOCATION)

packageProject(
  NAME ${PROJECT_NAME}
  VERSION ${PROJECT_VERSION}
  NAMESPACE ${PROJECT_NAME}
  BINARY_DIR ${PROJECT_BINARY_DIR}
  INCLUDE_DIR ${PROJECT_SOURCE_DIR}/include
  INCLUDE_DESTINATION include/${PROJECT_NAME}-${PROJECT_VERSION}
  VERSION_HEADER "${VERSION_HEADER_LOCATION}"
  COMPATIBILITY SameMajorVersion
  # DEPENDENCIES "fmt 9.1.0"
)
