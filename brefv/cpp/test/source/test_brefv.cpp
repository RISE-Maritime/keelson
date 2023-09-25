#include <brefv/messages/scalars.pb.h>
#include <doctest/doctest.h>
#include <google/protobuf/util/time_util.h>

#include <brefv/brefv.hpp>
#include <cassert>
#include <iostream>
#include <string>

TEST_CASE("Dummy payload") {
  std::string test = "test";

  auto message = brefv::enclose(test);

  auto [received_at, enclosed_at, content] = brefv::unwrap(message);

  REQUIRE_EQ(test, content);
  REQUIRE_GE(received_at, enclosed_at);
}

TEST_CASE("Actual payload") {
  brefv::scalars::TimestampedFloat data;
  brefv::set_current_time(data.mutable_timestamp());
  data.set_value(3.14);

  auto message = brefv::enclose(data.SerializeAsString());

  auto [received_at, enclosed_at, payload] = brefv::unwrap(message);

  brefv::scalars::TimestampedFloat content;
  content.ParseFromString(payload);

  REQUIRE_EQ(data.value(), content.value());
  REQUIRE_EQ(data.timestamp(), content.timestamp());
  REQUIRE_GE(enclosed_at, content.timestamp());
  REQUIRE_GE(received_at, enclosed_at);
}
