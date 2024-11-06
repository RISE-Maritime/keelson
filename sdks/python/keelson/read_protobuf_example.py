import rerun as rr
import numpy as np
from itertools import islice
from mcap_file_reader import readMCAPiterator
from PIL import Image #Pillow
from io import BytesIO

#MiS - Martin Sanfridson, RISE, October 2024

#rerun viewer needs to be installed


def plot_with_rerun(filename,topics,start,stop):
	mcap_iter = islice(readMCAPiterator(filename, topics),start,stop)
	rr.init("Incident_kayak_in_front_of_arriving_ferry",spawn=True)
	for topic, data, col_label, metadata in mcap_iter:
		if len(data) > 0:
			if topic == topics[0]:
				#do some processing here
				mask = np.logical_and(data[:,col_label.index('az_conf')] < 1,data[:,col_label.index('el_conf')] < 1) 
				pc0 = data[mask,0:3]
				rr.log("radar/pc_aptiv",rr.Points3D(pc0,radii=3.0))
				rr.log("radar/final_size",rr.Scalar(pc0.shape[0]))
			elif topic == topics[1]:	
				rr.log("camera/webcam",rr.Image(Image.open(BytesIO(data))))
			elif topic == topics[2]:
				rr.log("lidar/pc_os2",rr.Points3D(data[:,0:3],radii=1.0))
			#TODO: add time from MCAP metadata


#input examples
filename = r"C:\Users\martinsa\RISE\EPA - RISE - Dokument\RISE\3. Data\2024-09-06_Fiskebäck\Incident_kayak_in_front_of_arriving_ferry\0902_lidar_aptiv_webcam.mcap"
#filename = r"C:\Users\martinsa\RISE\EPA - RISE - Dokument\RISE\3. Data\2024-09-06_Fiskebäck\Incident_kayak_in_front_of_arriving_ferry\0902_radar_cam.mcap"
topics = ["rise/v0/landkrabba-two/pubsub/point_cloud/radar/aptiv/0",
		  "rise/v0/landkrabba-two/pubsub/compressed_image/usb",
		  "rise/v0/landkrabba-two/pubsub/point_cloud/lidar/os2/0",
		  "rise/v0/landkrabba/pubsub/point_cloud/1201"] #1202 och 1201 does not work yet
start = 6000 #6000
stop = 13000 #13000

plot_with_rerun(filename,topics,start,stop)