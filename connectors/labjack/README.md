# labjack

Reads analog voltage from a LabJack T-series DAQ (T4 / T7 / T8) and publishes
the values onto the Keelson bus. Each channel can compensate for an external
resistor voltage-divider (or an [LJTick-Divider](https://labjack.com/products/ljtick-divider))
used to bring a higher voltage down into the device's input range, so the
*true* voltage is what ends up on the bus.

> **Native library:** real-device use requires the LabJack
> [LJM library](https://labjack.com/ljm) (the `labjack-ljm` pip package is only
> the Python wrapper). The published Keelson Docker image already bundles LJM;
> for a local/host run install it from LabJack first. `--help` and `--simulate`
> work without it.

## `labjack2keelson`

```
usage: labjack2keelson [-h] [--log-level LOG_LEVEL] [--mode {peer,client}]
                       [--connect CONNECT] [--listen LISTEN] -r REALM
                       -e ENTITY_ID --config CONFIG [--device-type DEVICE_TYPE]
                       [--connection-type CONNECTION_TYPE]
                       [--identifier IDENTIFIER] [--simulate]

Read analog voltage from a LabJack T-series DAQ and publish it to Keelson, with
per-channel high-voltage divider/scale compensation.
```

One connector process serves one LabJack device, pinned to one `--entity-id`.
Individual analog inputs are distinguished by their per-channel `source_id`.

### Example

Simulated run (no hardware, no LJM library needed):

```bash
uv run python connectors/labjack/bin/labjack2keelson.py \
  -r rise -e rov \
  --config connectors/labjack/example-config.json \
  --simulate --mode peer
```

Against a real T7 over USB:

```bash
uv run python connectors/labjack/bin/labjack2keelson.py \
  -r rise -e rov --config myconfig.json \
  --device-type T7 --connection-type USB
```

### Configuration

The `--config` file is JSON validated against
[config-schema.json](config-schema.json). Each channel reads one analog input
register and publishes a `keelson.TimestampedFloat`:

```json
{
  "poll_interval_s": 1.0,
  "channels": [
    {
      "ain": "AIN0",
      "source_id": "voltage_ch0",
      "subject": "analog_voltage_v",
      "ain_range": 10.0,
      "divider": { "r1_ohms": 470000, "r2_ohms": 470000 }
    },
    {
      "ain": "AIN1",
      "source_id": "battery_main",
      "subject": "battery_voltage_v",
      "scale": 4.0,
      "offset": 0.0
    }
  ]
}
```

Per-channel fields:

| Field | Required | Description |
|---|---|---|
| `ain` | yes | LabJack analog input register, e.g. `AIN0`. |
| `source_id` | yes | Keelson source_id (must be unique across channels). |
| `subject` | no | Keelson subject (default `analog_voltage_v`); must map to `keelson.TimestampedFloat`. |
| `ain_range` | no | `AINx_RANGE` register (volts). |
| `resolution_index` | no | `AINx_RESOLUTION_INDEX` register (0 = default). |
| `settling_us` | no | `AINx_SETTLING_US` register (0 = auto). |
| `divider` | no | `{ "r1_ohms", "r2_ohms" }` external divider. |
| `scale` / `offset` | no | Linear scaling. |

**Higher-voltage scaling** (a channel may use *either* form, not both):

- **Resistor divider** — R1 in series with the signal, R2 from the AIN
  terminal to ground:
  `true = measured * (r1_ohms + r2_ohms) / r2_ohms`.
- **Scale + offset** — also covers LJTick-Divider ratios (/4, /5, /10, /25)
  and linear sensor calibration:
  `true = measured * scale + offset`.

A channel with neither form publishes the measured voltage directly.

### Live reconfiguration

The connector exposes the standard Keelson `get_config` / `set_config` RPC
(via `make_configurable`) on the connector's `--entity-id`, so the channel
configuration can be queried and updated at runtime.
