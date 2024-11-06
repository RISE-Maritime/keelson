from mcap_protobuf.decoder import DecoderFactory
from mcap.reader import make_reader
import struct
import numpy as np

#MiS - Martin Sanfridson, RISE, October 2024

class readMCAPiterator:

    def __init__(self, filename, topics):
        self.filename = filename
        self.topics = topics
        self.file = open(self.filename, "rb")
        self.reader = make_reader(self.file, decoder_factories=[DecoderFactory()])
        #register schema handlers, please add new ones here
        schema_handlers_schemabased = {
            "foxglove.CompressedImage": self.handle_webcam_image, #webcam image
            "foxglove.PointCloud": self.handle_aptiv_point_cloud, #e.g. os2 lidar AND aptiv radar
            "keelson.primitives.TimestampedBytes": self.handle_none, #e.g. raw aptiv radar data
            "keelson.compound.ImuReading": self.handle_none, #e.g. imu data from os2 is not implemented yet
        }
        self.schema_handlers = schema_handlers_schemabased
        self.iterator = self.reader.iter_decoded_messages()

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            try:
                schema, channel, message, proto_msg = next(self.iterator)
                handler = self.schema_handlers.get(schema.name, self.handle_none)
                #print(f"{channel.topic} ({schema.name})")
                if channel.topic in self.topics:
                    return handler(proto_msg, channel, message)
            except StopIteration:
                self.file.close()
                raise

    def handle_metadata(self,proto_msg,channel,message):
        #TODO: not fully implemented yet
        point_dict = {}
        point_dict["log_time"] = message.log_time
        point_dict["publish_time"] = message.publish_time
        point_dict["sequence"] = message.sequence
        point_dict["timestamp_s"] = proto_msg.timestamp.seconds
        point_dict["timestamp_ns"] = proto_msg.timestamp.nanos
        #proto_msg.pose
        return point_dict


    def handle_aptiv_point_cloud(self,proto_msg,channel,message):
        #TODO: at the moment very limited to float64, need to become more general
        type_mapping = {
            8: 'd',  # double = 8 bytes = np.float64
            4: 'f',  # float = 4 bytes = np.float32
            0: 'n/a',  # add more if needed
        }
        field_names = [field.name for field in proto_msg.fields]
        format_string = '<' + ''.join(type_mapping[field.type] for field in proto_msg.fields)
        segment_size = struct.calcsize(format_string)
        assert segment_size == proto_msg.point_stride
        dtype = np.dtype([(name, np.float64) for name in field_names])
        structured_array = np.frombuffer(proto_msg.data, dtype=dtype)
        cloud = structured_array.view(np.float64).reshape(-1, len(field_names))
        #cast to float32 to save memory
        cloud = cloud.astype(np.float32)
        metadata = self.handle_metadata(proto_msg,channel,message)
        return channel.topic, cloud, field_names, metadata

    def handle_webcam_image(self,proto_msg,channel,message):
        #TODO: call IOBytes and Image.open here instead
        field_names = []
        img = proto_msg.data
        metadata = self.handle_metadata(proto_msg,channel,message)
        return channel.topic, img, field_names, metadata

    def handle_none(self,proto_msg,channel,message):
        # if no handler is found
        data, field_names, metadata = [], [], []
        return channel.topic, data, field_names, metadata

