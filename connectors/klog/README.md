# klog

klog is very simple data logging to file interface for keelson. It provides two binaries:

## klog-record

  Records all envelopes on the user-defined subscription topics to a length-delimited binary file (a klog-file). Inspired by https://github.com/sebnyberg/ldproto-py

### Exaple run command 

```bash
# Show help 
docker run ghcr.io/mo-rise/keelson:0.3.4 "klog-record -h"

# Record 
docker run --network host --volume /home/user/rec_klog:/rec_klog ghcr.io/mo-rise/keelson:0.3.4 "klog-record --output rec_klog/2024-05-15.klog -k rise/v0/masslab/pubsub/**"
```


## klog2mcap

Converts a klog-file to a mcap-compatible file.

```bash
# Show help 
docker run ghcr.io/mo-rise/keelson:0.3.4 "klog2mcap -h"

# Convert 
docker run --network host --volume /home/user/rec_klog:/rec_klog ghcr.io/mo-rise/keelson:0.3.1 "klog2mcap --input rec_klog/2024-05-15.klog --output rec_klog/2024-05-15.mcap"
```


  