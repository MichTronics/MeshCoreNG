#include <gtest/gtest.h>

#include "Packet.h"

using namespace mesh;

TEST(PacketParser, RejectsMissingHeaderAndPathLength) {
    Packet packet;
    EXPECT_FALSE(packet.readFrom(nullptr, 0));
    const uint8_t header_only[] = {ROUTE_TYPE_FLOOD};
    EXPECT_FALSE(packet.readFrom(header_only, sizeof(header_only)));
}

TEST(PacketParser, RejectsTruncatedTransportHeader) {
    const uint8_t truncated[] = {ROUTE_TYPE_TRANSPORT_FLOOD, 0x01, 0x02, 0x03};
    Packet packet;
    EXPECT_FALSE(packet.readFrom(truncated, sizeof(truncated)));
}

TEST(PacketParser, RejectsTruncatedPathAndReservedEncoding) {
    const uint8_t truncated_path[] = {ROUTE_TYPE_FLOOD, 0x05, 0xAA};
    const uint8_t reserved_path[] = {ROUTE_TYPE_FLOOD, 0xC0, 0x00};
    Packet packet;
    EXPECT_FALSE(packet.readFrom(truncated_path, sizeof(truncated_path)));
    EXPECT_FALSE(packet.readFrom(reserved_path, sizeof(reserved_path)));
}

TEST(PacketParser, AcceptsMinimalPayloadFrame) {
    const uint8_t valid[] = {ROUTE_TYPE_FLOOD, 0x00, 0x7F};
    Packet packet;
    ASSERT_TRUE(packet.readFrom(valid, sizeof(valid)));
    EXPECT_EQ(0, packet.getPathHashCount());
    EXPECT_EQ(1, packet.payload_len);
    EXPECT_EQ(0x7F, packet.payload[0]);
}

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
