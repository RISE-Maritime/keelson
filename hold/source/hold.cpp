#include "CLI/CLI.hpp"

auto main(int argc, char **argv) -> int {
  CLI::App app{"Hold"};

  // General options for this service
  uint16_t cid = 111;
  app.add_option("-c,--cid", cid, "OpenDaVINCI session id");
  uint16_t id = 1;
  app.add_option("-i,--id", id, "Identification id of this microservice");
  bool verbose = false;
  app.add_flag("--verbose", verbose, "Print to cout");
}
