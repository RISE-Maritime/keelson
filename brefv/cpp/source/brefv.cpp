
#include <brefv/brefv.hpp>
#include <brefv/tags/tags.hpp>

std::string msg_name_from_tag(const std::string& tag) { return TAG_TYPE_MAP.at(tag); }