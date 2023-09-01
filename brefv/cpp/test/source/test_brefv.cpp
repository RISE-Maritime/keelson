#include <brefv/messages/envelope.pb.h>
#include <doctest/doctest.h>
#include <google/protobuf/descriptor.h>

#include <brefv/brefv.hpp>
#include <string>

brefv::Envelope env;

TEST_CASE("Trial") {
    auto desc
      = google::protobuf::DescriptorPool::generated_pool()->FindMessageTypeByName("brefv.Envelope");

  assert(desc != NULL);
}
