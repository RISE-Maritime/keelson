# Message defections  

In the folder messages are the defections for subjects and procedures

## Messages are divided into the following packages:

- Primitives (Simple and flexible for of messages)
- Topic specific Package (Combined messages or specific purpose messages)

## Third party message definition integrated to Keelson

- Foxglove ([Foxglove formatdefintion](https://github.com/foxglove/schemas/tree/main/schemas/proto/foxglove))

## Message Core ENVELOPE

Each message is wrapped in an [Envelope](./Envelope.proto) with include the payload message and all the payloads are located in the [payloads folder](./payloads/). Each payload is an protobuf definition as the file type .proto

TODO: Should envelop continue to exist or be moved to 

### To add

1) Just create a new proto file and name it descriptively in CamelCase.



### Design philosophy

#### Naming variables  

The naming describes the type of measurement and adheres to the format <property>_<unit>, where:

- property: Describes the property measured in the entity.
- unit: Describes the units of the measurement.

For example, the following tag markers:
speed_rpm
power_kw

#### Packages are by entity 

#### Messages structure 

- Messages are built up by common/Primitives  

## Tool for visualizing protobuf files

[https://protodot.seamia.net/](https://protodot.seamia.net/)





## Generic Command Message Structure and Function Logic

When managing or setting up commands for a controlled object, the command structure and logic should ensure clarity, reliability, and synchronization between components.

### Command Structure

1. **Object State Publishing**  
    - The controlled object continuously publishes its current state at a fixed frequency. This ensures that the system has up-to-date information about the object's status.
    - Keyexpression example: rise/@v0/seahorse/pubsub/rudder_angle_deg/rudder/0 

2. **Command Unit Publishing**  
    - The commanding unit publishes its intended commands at a fixed frequency. This allows the system to track the desired state and compare it with the actual state.
    - Keyexpression example: rise/@v0/seahorse/pubsub/wheel_position_pct/wheel/0 

3. **Control Manager**  
    - A processor within the controlled device includes a control manager responsible for:
      - Determining which command unit is active and publishing the active key expression. 
      - Forwarding commands from the active command unit to the controlled object.
      - Validating commands to ensure they are safe and applicable before execution.
    - Keyexpression example: rise/@v0/seahorse/pubsub/cmd_pct/rudder/0 

### Design Considerations

- **Synchronization**: Ensure that the publishing frequencies of the object state and command unit are aligned to avoid conflicts or delays.
- **Validation**: Commands must be validated to prevent unintended or unsafe operations.
- **Redundancy**: Implement fallback mechanisms in case of communication failures or invalid commands.
- **Scalability**: The structure should support multiple command units and controlled objects without significant modifications.

This approach ensures a robust and efficient command management system for controlled devices.




## Target from AIS, RADAR, LIDAR or other sensors 

All targets should follow common keyexpression 

Keyexpression example: rise/@v0/seahorse/pubsub/<subject>/<source>/<mmsi or target_id> 


## Sensors 

Sensor type should use well known names in key expression 

Keyexpression example: rise/@v0/seahorse/pubsub/<subject>/<sensor>/<sensor_id>

### Sensor ID Guidelines

The sensor ID should start from `0`, with `0` designated as the primary sensor. Subsequent sensor IDs should follow a hierarchical ordering based on accuracy or a similar logical sequence. This ensures a consistent and intuitive structure for identifying and prioritizing sensors.

### Follwing are well know:
- UNKNOWN  
- CAMERA_RBG  
- LIDAR  
- RADAR_MARINE  
- RADAR_VEHICLE  
- GNSS  
- IMU  
- SONAR  
- THERMAL  
- HYDROPHONE  
- MICROPHONE  
- PRESSURE  
- TEMPERATURE  
- HUMIDITY  
- ANOMETER  
- CURRENT  
- VOLTAGE  
