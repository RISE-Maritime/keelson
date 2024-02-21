Needs installed 

pip install grpclib protobuf grpcio-tools


protoc -I=. --python_out=. Rudder.proto

protoc --python_out=. --grpclib_python_out=. Rudder.proto


