from mcap_protobuf.decoder import DecoderFactory
from mcap.reader import make_reader
import struct
import numpy as np
from itertools import islice
import rerun as rr
import os
from PIL import Image #Pillow
from io import BytesIO


# MiS - Martin Sanfridson, RISE, October 2024

class readMCAPiterator:

    def __init__(self, filename, topics):
        self.filename = filename
        self.topics = topics
        self.file = open(self.filename, "rb")
        self.reader = make_reader(self.file, decoder_factories=[DecoderFactory()])
        #register schema handlers, please add new ones here
        schema_handlers_schemabased = {
            "foxglove.CompressedImage": self.handle_webcam_image, #webcam image OR axis-1 image
            "foxglove.PointCloud": self.handle_aptiv_point_cloud, #e.g. os2 lidar AND aptiv radar AND boat radar
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
        type_mapping1 = {
            8: 'd',  # double = 8 bytes = np.float64
            7: 'f',  # float = 4 bytes = np.float32
            0: 'n/a',  # add more if needed
        }
        type_mapping2 ={
            8: np.float64,
            7: np.float32,
            0: np.float32,
        }
        field_names = [field.name for field in proto_msg.fields]
        field_types = [field.type for field in proto_msg.fields]
        format_string = '<' + ''.join(type_mapping1[typ] for typ in field_types)
        segment_size = struct.calcsize(format_string)
        assert segment_size == proto_msg.point_stride
        #dtype = np.dtype([(name, np.float64) for name in field_names])
        dtype = np.dtype([(name, type_mapping2[typ]) for name,typ in zip(field_names,field_types)])
        structured_array = np.frombuffer(proto_msg.data, dtype=dtype)
        largest_dtype = np.float64 if 8 in field_types else np.float32
        cloud = structured_array.view(largest_dtype).reshape(-1, len(field_names)) #what type to use, need largest datatype here?
        #cloud = cloud.astype(np.float32), #cast to float32 to save memory, unsure if it helps, also: tuple or list?
        metadata = self.handle_metadata(proto_msg,channel,message)
        return channel.topic, cloud, field_names, metadata

    def handle_webcam_image(self,proto_msg,channel,message):
        #TODO: call IOBytes and Image.open here instead?
        field_names = []
        img = proto_msg.data
        metadata = self.handle_metadata(proto_msg,channel,message)
        return channel.topic, img, field_names, metadata

    def handle_none(self,proto_msg,channel,message):
        # if no handler is found
        data, field_names, metadata = [], [], []
        return channel.topic, data, field_names, metadata



def plot_with_rerun(filename,topics,start,stop):
    """
    Plot data from MCAP file using rerun
    :param filename: path to MCAP file
    :param topics: list of topics to plot
    :param start: start index
    :param stop: stop index
    :return: None
    """
    mcap_iter = islice(readMCAPiterator(filename, topics),start,stop)
    log_name = os.path.basename(filename) #could be tailored
    rr.init(log_name,spawn=True)
    for topic, data, col_label, metadata in mcap_iter:
        if len(data) > 0:
            if '1201' in topic:
                data_conv = np.vstack((-data[:,0],data[:,1])).T
                rr.log("radar/r1201",rr.Points2D(data_conv,radii=1.0))
            elif 'aptiv' in topic:
                #do some processing here
                mask = np.logical_and(data[:,col_label.index('az_conf')] < 1,data[:,col_label.index('el_conf')] < 1) 
                pc0 = data[mask,0:3]
                rr.log("radar/pc_aptiv",rr.Points3D(pc0,radii=3.0))
                rr.log("radar/final_size",rr.Scalar(pc0.shape[0]))
            elif 'usb' or 'axis-1' in topic:	
                rr.log("camera/webcam",rr.Image(Image.open(BytesIO(data))))
            elif 'os2' in topic:
                rr.log("lidar/pc_os2",rr.Points3D(data[:,0:3],radii=1.0))
            #TODO: add time from MCAP metadata