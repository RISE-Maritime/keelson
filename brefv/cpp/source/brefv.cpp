
#include <envelope.pb.h>
#include <google/protobuf/util/time_util.h>

#include <brefv/brefv.hpp>

namespace brefv {
  using namespace google::protobuf;

  void set_current_time(google::protobuf::Timestamp* timestamp) {
    auto now = util::TimeUtil::GetCurrentTime();
    timestamp->CopyFrom(now);
  }

  std::string enclose(const std::string& payload) {
    auto env = brefv::Envelope::default_instance();

    set_current_time(env.mutable_enclosed_at());
    env.set_payload(payload);

    return env.SerializeAsString();
  };
  std::tuple<google::protobuf::Timestamp, google::protobuf::Timestamp, std::string> unwrap(
      const std::string& message) {
    auto env = brefv::Envelope::default_instance();
    env.ParseFromString(message);
    return {util::TimeUtil::GetCurrentTime(), env.enclosed_at(), env.payload()};
  };
}  // namespace brefv