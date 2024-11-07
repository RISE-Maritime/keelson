import rerun as rr
import os
import numpy as np
from itertools import islice
from mcap_file_reader import readMCAPiterator
from PIL import Image #Pillow
from io import BytesIO

#MiS - Martin Sanfridson, RISE, October 2024

#rerun viewer needs to be installed


def plot_with_rerun(filename,topics,start,stop):
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


#input examples
#filename = r"C:\Users\martinsa\RISE\EPA - RISE - Dokument\RISE\3. Data\2024-09-06_Fiskebäck\Incident_kayak_in_front_of_arriving_ferry\0902_lidar_aptiv_webcam.mcap"
filename = r"C:\Users\martinsa\RISE\EPA - RISE - Dokument\RISE\3. Data\2024-09-06_Fiskebäck\Incident_kayak_in_front_of_arriving_ferry\0902_radar_cam.mcap"
topics = ["rise/v0/landkrabba-two/pubsub/point_cloud/radar/aptiv/0",
		  "rise/v0/landkrabba-two/pubsub/compressed_image/usb",
		  "rise/v0/landkrabba/pubsub/compressed_image/axis-1",
		  "rise/v0/landkrabba-two/pubsub/point_cloud/lidar/os2/0",
		  "rise/v0/landkrabba/pubsub/point_cloud/1201"] #1202 och 1201 does not work yet
start = 17000 #6000, 17000
stop = 38000 #13000, 38000

#TODO: start, stop in ticks should be calculated from time to synchronize with other files

plot_with_rerun(filename,topics,start,stop)