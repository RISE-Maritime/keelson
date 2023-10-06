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

set(Protobuf_USE_STATIC_LIBS ON)
find_package(Protobuf REQUIRED)

# Dummy output which is never actually produced. Anything that depends on
# this will always be rebuilt.
add_custom_command(
    OUTPUT always_rebuild
    COMMAND cmake -E echo Triggering regeneration...
    )

# ---- Compile envelope definition ----

protobuf_generate_cpp(ENVELOPE_SRC ENVELOPE_HDR ${CMAKE_CURRENT_SOURCE_DIR}/../envelope.proto)

# ---- Compile payload definitions ----

### -- Protobuf --

set(PROTO_PATH ${CMAKE_CURRENT_SOURCE_DIR}/../payloads)
file(GLOB_RECURSE PROTOBUF_DEFINITION_FILES CONFIGURE_DEPENDS "${PROTO_PATH}/*.proto")

set(PAYLOADS_DIR ${PROJECT_INCLUDE_DIR}/payloads)
# set(PAYLOADS_DESC_SET ${PROJECT_BINARY_DIR}/payloads_proto.desc)
add_custom_command(
  OUTPUT ${PAYLOADS_DIR}
  COMMAND ${CMAKE_COMMAND} -E make_directory ${PAYLOADS_DIR}
  COMMAND protoc --proto_path ${PROTO_PATH} --cpp_out ${PAYLOADS_DIR} ${PROTOBUF_DEFINITION_FILES}
  DEPENDS ${PROTO_PATH}
)

add_custom_target(
  generate_messages
  DEPENDS ${PAYLOADS_DIR} always_rebuild
)

file(GLOB_RECURSE PROTO_HDRS CONFIGURE_DEPENDS "${PAYLOADS_DIR}/*.h")
file(GLOB_RECURSE PROTO_SRCS CONFIGURE_DEPENDS "${PAYLOADS_DIR}/*.cc")

# ---- Create library ----

# Note: for header-only libraries change all PUBLIC flags to INTERFACE and create an interface
# target: add_library(${PROJECT_NAME} INTERFACE)
add_library(${PROJECT_NAME} STATIC ${CMAKE_CURRENT_SOURCE_DIR}/source/brefv.cpp ${ENVELOPE_SRC} ${PROTO_SRCS})
set_target_properties(${PROJECT_NAME} PROPERTIES CXX_STANDARD 17)

# being a cross-platform target, we enforce standards conformance on MSVC
target_compile_options(${PROJECT_NAME} PUBLIC "$<$<COMPILE_LANG_AND_ID:CXX,MSVC>:/permissive->")

# Link dependencies
target_link_libraries(${PROJECT_NAME} ${Protobuf_LIBRARIES})
add_dependencies(${PROJECT_NAME} generate_messages)

target_include_directories(
  ${PROJECT_NAME} PUBLIC $<BUILD_INTERFACE:${PROJECT_SOURCE_DIR}/include>
                         $<BUILD_INTERFACE:${PROJECT_BINARY_DIR}>
                         $<BUILD_INTERFACE:${Protobuf_INCLUDE_DIRS}>
                         $<INSTALL_INTERFACE:include/${PROJECT_NAME}-${PROJECT_VERSION}>
                         ${PAYLOADS_DIR}
                         
)

# ---- Create an installable target ----
# this allows users to install and find the library via `find_package()`.

# the location where the project's version header will be placed should match the project's regular
# header paths
string(TOLOWER ${PROJECT_NAME}/version.h VERSION_HEADER_LOCATION)

# install(FILES ${PAYLOADS_DESC_SET} DESTINATION include)

packageProject(
  NAME ${PROJECT_NAME}
  VERSION ${PROJECT_VERSION}
  NAMESPACE ${PROJECT_NAME}
  BINARY_DIR ${PROJECT_BINARY_DIR}
  INCLUDE_DIR ${PROJECT_SOURCE_DIR}/include
  INCLUDE_DESTINATION include/${PROJECT_NAME}-${PROJECT_VERSION}
  VERSION_HEADER "${VERSION_HEADER_LOCATION}"
  COMPATIBILITY SameMajorVersion
)
