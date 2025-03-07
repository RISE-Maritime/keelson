## Well-known subjects
 Organized by packages:


### PRIMITIVES
- **raw** [keelson.primitives.TimestampedBytes](./payloads/primitives.proto#7)
- **raw_bytes** [keelson.primitives.TimestampedBytes](./payloads/primitives.proto#7)
- **raw_string** [keelson.primitives.TimestampedString](./payloads/primitives.proto#30)
- **raw_json** [keelson.primitives.TimestampedString](./payloads/primitives.proto#30)
- **raw_double** [keelson.primitives.TimestampedDouble](./payloads/primitives.proto#14)
- **raw_float** [keelson.primitives.TimestampedFloat](./payloads/primitives.proto#18)
- **raw_int** [keelson.primitives.TimestampedInt](./payloads/primitives.proto#30)

### LOG
- **log** [foxglove.Log](./payloads/Log.proto#10)

### AIS
- **ais_messages** [keelson.ais.AISMessages](./payloads/Ais.proto#4)
- **ais_message** [keelson.ais.AISMessage](./payloads/Ais.proto#7)
- **ais_vessel_message** [keelson.ais.AISVesselMessage](./payloads/Ais.proto#10)
- **ais_vessel** [keelson.ais.AISVessel](./payloads/Ais.proto#22)
- **ais_vessel_statics** [keelson.ais.AISVesselStatics](./payloads/Ais.proto#38)
- **ais_vessel_statics_class_a** [keelson.ais.AISVesselStaticsClassA](./payloads/Ais.proto#49)
- **ais_vessel_position_class_a** [keelson.ais.AISVesselPositionClassA](./payloads/Ais.proto#62)

### ALARM
- **alarms** [keelson.alarm.Alarms](./payloads/Alarms.proto#16)
- **alarm** [keelson.alarm.Alarm](./payloads/Alarms.proto#27)
- **alarm_acknowledgement** [keelson.alarm.AlarmAcknowledgment](./payloads/Alarms.proto#109)
- **alarm_visual** [keelson.alarm.Visual](./payloads/Alarms.proto#115)

### AUDIO
- **audio** [keelson.audio.Audio](./payloads/Audio.proto#8)

### COMMAND
- **command_thruster** [keelson.command.CommandThruster](./payloads/Commands.proto#10)
- **command_engine** [keelson.command.CommandEngine](./payloads/Commands.proto#19)
- **command_engine_percentage** [keelson.command.CommandEnginePercentage](./payloads/Commands.proto#25)
- **command_engine_mode** [keelson.command.CommandEngineMode](./payloads/Commands.proto#31)
- **command_rudder** [keelson.command.CommandRudder](./payloads/Commands.proto#46)
- **command_pan_tilt** [keelson.command.CommandPanTiltXY](./payloads/Commands.proto#54)
- **command_primitive_float** [keelson.command.CommandPrimitiveFloat](./payloads/Commands.proto#71)
- **command_primitive_integer** [keelson.command.CommandPrimitiveInt](./payloads/Commands.proto)
- **command_primitive_boolean** [keelson.command.CommandPrimitiveBool](./payloads/Commands.proto)
- **command_primitive_string** [keelson.command.CommandPrimitiveString](./payloads/Commands.proto)

### FLIGHT_CONTROLLER
- **flight_controller_power_status** [keelson.flightcontroller.PowerStatus](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_mem_info** [keelson.flightcontroller.MemInfo](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_mission_current** [keelson.flightcontroller.MissionCurrent](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_servo_output_raw** [keelson.flightcontroller.ServoOutputRaw](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_rc_channels** [keelson.flightcontroller.RCChannels](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_raw_imu** [keelson.flightcontroller.RawIMU](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_scaled_pressure** [keelson.flightcontroller.ScaledPressure](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_gps_raw_int** [keelson.flightcontroller.GPSRawInt](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_system_time** [keelson.flightcontroller.SystemTime](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_ahrs** [keelson.flightcontroller.AHRS](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_ekf_status_report** [keelson.flightcontroller.EKFStatusReport](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_vibration** [keelson.flightcontroller.Vibration](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_battery_status** [keelson.flightcontroller.BatteryStatus](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_rcc_channels_selected** [keelson.flightcontroller.RCChannelsScaled](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_attitude** [keelson.flightcontroller.Attitude](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_vfrhud** [keelson.flightcontroller.VFRHUD](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_ahrs2** [keelson.flightcontroller.AHRS2](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_global_position_int** [keelson.flightcontroller.GlobalPositionInt](./payloads/FlightControllerTelemetry.proto)
- **flight_controller_sys_status** [keelson.flightcontroller.SysStatus](./payloads/FlightControllerTelemetry.proto)

### FRAME_TRANSFORM
- **frame_transforms** [foxglove.FrameTransforms](./payloads/FrameTransform.proto)
- **frame_transform** [foxglove.FrameTransform](./payloads/FrameTransform.proto)

### IMAGE
- **image_raw** [foxglove.ImageRaw](./payloads/Image.proto)
- **image_compressed** [foxglove.ImageCompressed](./payloads/Image.proto)
- **video_compressed** [foxglove.VideoCompressed](./payloads/Image.proto)

### IMU
- **imu** [keelson.imu.ImuReading](./payloads/Imu.proto)

### FOXGLOVE
- **laser_scan** [foxglove.LaserScan](./payloads/LaserScan.proto)
- **location_fix** [foxglove.LocationFix](./payloads/Localization.proto)
- **position_fix** [foxglove.PositionFix](./payloads/Localization.proto)
- **packed_elements_field** [foxglove.PackedElementField](./payloads/PackedElements.proto)

### NAVIGATION
- **stw** [keelson.navigation.SpeedThroughWater](./payloads/Navigation.proto)
- **sog** [keelson.navigation.SpeedOverGround](./payloads/Navigation.proto)
- **rot** [keelson.navigation.RateOfTurn](./payloads/Navigation.proto)
- **heading** [keelson.navigation.Heading](./payloads/Navigation.proto)
- **common_ref_point** [keelson.navigation.CommonReferencePoint](./payloads/Navigation.proto)
- **sonar** [keelson.navigation.Sonar](./payloads/Navigation.proto)
- **collision_monitoring** [keelson.navigation.CollisionMonitoring](./payloads/Navigation.proto)
- **steering_angle** [keelson.navigation.SteeringAngle](./payloads/Navigation.proto)
- **navigation_status** [keelson.navigation.NavigationStatus](./payloads/Navigation.proto)

### NETWORK
- **network_ping** [keelson.network.NetworkPing](./payloads/Network.proto)
- **network_result** [keelson.network.NetworkResult](./payloads/Network.proto)

### NMEA
- **nmea_raw_string** [keelson.primitives.TimestampedString](./payloads/Nmea.proto)
- **nmea_gngns** [keelson.nmea.GNGNS](./payloads/Nmea.proto)
- **nmea_gngga** [keelson.nmea.GNGGA](./payloads/Nmea.proto)

### POINT CLOUD
- **point_cloud** [foxglove.PointCloud](./payloads/PointCloud.proto)
- **point_cloud_simplified** [foxglove.PointCloudSimplified](./payloads/PointCloud.proto)

### FOXGLOVE
- **pose** [foxglove.Pose](./payloads/Pose.proto)
- **pose_frame** [foxglove.PoseInFrame](./payloads/Pose.proto)
- **poses_in_frames** [foxglove.PosesInFrames](./payloads/Pose.proto)
- **quaternion** [foxglove.Quaternion](./payloads/Pose.proto)

### RADAR
- **radar_spoke** [keelson.radar.RadarSpoke](./payloads/Radar.proto)
- **radar_sweep** [keelson.radar.RadarSweep](./payloads/Radar.proto)

### ROC
- **roc_assignment** [keelson.roc.Assignment](./payloads/Roc.proto)
- **roc_assignments** [keelson.roc.Assignments](./payloads/Roc.proto)

### SENSOR_PLATFORM
- **sensor_platform_configuration** [keelson.platform.ConfigurationSensorPlatform](./payloads/SensorPlatform.proto)
- **sensor_platform_device** [keelson.platform.Device](./payloads/SensorPlatform.proto)

### SIMULATION
- **simulation_state** [keelson.simulation.SimulationState](./payloads/Simulation.proto)

### NAVIGATION
- **targets** [keelson.target.Targets](./payloads/Target.proto)

### TARGET
- **target** [keelson.target.Target](./payloads/Target.proto)

### NAVIGATION
- **target_definition** [keelson.target.TargetIdentification](./payloads/Target.proto)

### TARGET
- **target_data_source** [keelson.target.TargetDataSource](./payloads/Target.proto)

### VESSEL
- **vessels** [keelson.vessel.Vessels](./payloads/Vessel.proto)
- **vessel** [keelson.vessel.Vessel](./payloads/Vessel.proto)
- **vessel_information** [keelson.vessel.VesselInformation](./payloads/Vessel.proto)
- **vessel_voyage** [keelson.vessel.VesselVoyage](./payloads/Vessel.proto)
- **vessel_data_source** [keelson.vessel.VesselDataSource](./payloads/Vessel.proto)
- **vessel_statics** [keelson.vessel.VesselStatics](./payloads/Vessel.proto)
- **vessel_autopilot** [keelson.vessel.Autopilot](./payloads/Vessel.proto)
- **vessel_orientation** [keelson.vessel.Orientation](./payloads/Vessel.proto)
- **vessel_device** [keelson.vessel.Device](./payloads/Vessel.proto)
- **vessel_location** [keelson.vessel.Location](./payloads/Vessel.proto)
- **vessel_min_max** [keelson.vessel.LimitMinMax](./payloads/Vessel.proto)

### VOYAGE
- **voyage_plan** [keelson.voyage.VoyagePlan](./payloads/Voyage.proto)
- **voyage_route** [keelson.voyage.VoyageRoute](./payloads/Voyage.proto)
- **voyage_waypoint** [keelson.voyage.VoyageWaypoint](./payloads/Voyage.proto)

### CONTROL
- **sail_control_state** [keelson.windpower.SailControlState](./payloads/WindPower.proto)
- **sail_state** [keelson.windpower.SailState](./payloads/WindPower.proto)
- **lever_position_pct** [keelson.primitives.TimestampedFloat](./payloads/Primitives.proto)
- **propeller_rate_rpm** [keelson.primitives.TimestampedFloat](./payloads/Primitives.proto)
- **propeller_pitch_rpm** [keelson.primitives.TimestampedFloat](./payloads/Primitives.proto)
