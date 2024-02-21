# klog

klog is very simple data logging to file interface for keelson. It provides two binaries:

* klog-record

  Records all envelopes on the user-defined subscription topics to a length-delimited binary file (a klog-file). Inspired by https://github.com/sebnyberg/ldproto-py

* klog2mcap

  Converts a klog-file to a mcap-compatible file.