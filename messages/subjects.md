## Well-known subjects

### raw
- raw [keelson.primitives.TimestampedBytes](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedBytes.proto)
- raw_bytes [keelson.primitives.TimestampedBytes](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedBytes.proto)
- raw_string [keelson.primitives.TimestampedString](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedString.proto)
- raw_json [keelson.primitives.TimestampedString](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedString.proto)

### log
- log [foxglove.Log](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/Log.proto)

### primitive
- percentage [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
- degrees [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
- rpm [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
- meters [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
- kilometers [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
- meters_per_second [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
- kilometers_per_hour [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
- nautical_miles [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)
- knots [keelson.primitives.TimestampedFloat](https://github.com/RISE-Maritime/keelson/blob/main/messages/payloads/TimestampedFloat.proto)

### network
- network_ping [keelson.compound.NetworkPing]
- network_result [keelson.compound.NetworkResult]

### navigation
- target [keelson.compound.Target]
- target_description [keelson.compound.TargetDescription]

### config
- config_perception_sensor [keelson.experimental.ConfigurationSensorPerception]

### image
- raw_image [foxglove.RawImage]
- compressed_image [foxglove.CompressedImage]

### lidar
- laser_scan [foxglove.LaserScan]

### radar
- radar_spoke [keelson.compound.RadarSpoke]
- radar_sweep [keelson.compound.RadarSweep]

### point cloud
- point_cloud [foxglove.PointCloud]
- point_cloud_simplified [keelson.experimental.PointCloudSimplified]

### imu
- imu_reading [keelson.compound.ImuReading]

### control
- sail_control_state [keelson.compound.SailControlState]
- sail_state [keelson.compound.SailState]
- command_thrust [keelson.compound.CommandThruster]
- command_camera_xy [keelson.compound.CommandCameraXY]

### simulation
- simulation_state [keelson.compound.SimulationState]
- simulation_ship [keelson.compound.SimulationShip]

### nmea
- nmea_string [keelson.primitives.TimestampedString]
- nmea_gngns [keelson.compound.GNGNS]

### mavlink
- flight_controller_telemetry_vfrhud [keelson.experimental.VFRHUD]
- flight_controller_telemetry_rawimu [keelson.experimental.RawIMU]
- flight_controller_telemetry_ahrs [keelson.experimental.AHRSs]
- flight_controller_telemetry_vibration [keelson.experimental.Vibration]
- flight_controller_telemetry_battery [keelson.experimental.BatteryStatus]
- lever_position_pct [keelson.primitives.TimestampedFloat]
- propeller_rate_rpm [keelson.primitives.TimestampedFloat]
- propeller_pitch_rpm [keelson.primitives.TimestampedFloat]
