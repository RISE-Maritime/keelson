cmake_minimum_required(VERSION 3.14...3.22)

# ---- Project ----

# Note: update this to your new project's name and version
project(
  Hold
  VERSION 1.0
  LANGUAGES CXX
)

# ---- Include guards ----

if(PROJECT_SOURCE_DIR STREQUAL PROJECT_BINARY_DIR)
  message(
    FATAL_ERROR
      "In-source builds not allowed. Please make a new directory (called a build directory) and run CMake from there."
  )
endif()


# ---- Add dependencies via CPM ----
# see https://github.com/TheLartians/CPM.cmake for more info

include(../cmake/CPM.cmake)

CPMAddPackage("gh:CLIUtils/CLI11@1.9.1")

CPMAddPackage(NAME Brefv SOURCE_DIR ${CMAKE_CURRENT_LIST_DIR}/../brefv/cpp)

# ---- Compile protobuf definitions ----

set(Protobuf_USE_STATIC_LIBS ON)
find_package(Protobuf REQUIRED)

set(PROTO_PATH ${CMAKE_CURRENT_SOURCE_DIR}/../brefv/messages)
file(GLOB PROTOBUF_DEFINITION_FILES ${PROTO_PATH}/*.proto)

# For executables:
protobuf_generate_cpp(PROTO_SRCS PROTO_HDRS DESCRIPTORS PROTO_DESCS ${PROTOBUF_DEFINITION_FILES})

# ---- Create library ----

# Note: for header-only libraries change all PUBLIC flags to INTERFACE and create an interface
# target: add_library(${PROJECT_NAME} INTERFACE)
add_executable(${PROJECT_NAME} ${CMAKE_CURRENT_SOURCE_DIR}/source/hold.cpp)
set_target_properties(${PROJECT_NAME} PROPERTIES CXX_STANDARD 17)

# being a cross-platform target, we enforce standards conformance on MSVC
target_compile_options(${PROJECT_NAME} PUBLIC "$<$<COMPILE_LANG_AND_ID:CXX,MSVC>:/permissive->")

# Link dependencies
target_link_libraries(${PROJECT_NAME} ${Protobuf_LIBRARIES} CLI11)

target_include_directories(
  ${PROJECT_NAME} PRIVATE ${Protobuf_INCLUDE_DIRS}
)

# Install executable
install(TARGETS ${PROJECT_NAME} DESTINATION bin COMPONENT ${PROJECT_NAME})


# Tests
CPMAddPackage("gh:onqtam/doctest#2.4.0")

enable_testing()
add_executable(${PROJECT_NAME}-tester ${CMAKE_CURRENT_SOURCE_DIR}/tests/test_hold.cpp)
target_include_directories(${PROJECT_NAME}-tester PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/source)
target_link_libraries(${PROJECT_NAME}-tester doctest)
add_test(NAME ${PROJECT_NAME}-tester COMMAND ${PROJECT_NAME}-tester)