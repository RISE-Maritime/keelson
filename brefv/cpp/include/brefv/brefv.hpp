#pragma once
#include <string>
#include <tuple>

namespace brefv {
  void set_current_time(google::protobuf::Timestamp* timestamp);
  std::string enclose(const std::string& payload);
  std::tuple<google::protobuf::Timestamp, google::protobuf::Timestamp, std::string> unwrap(
      const std::string& message);
};  // namespace brefv
