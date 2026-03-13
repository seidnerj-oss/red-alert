# Homebridge Integration

red-alert includes a lightweight HTTP server that exposes alert state for Homebridge. It uses the same core library as the Home Assistant integration but runs as a standalone Python process.

## How It Works

1. The red-alert server polls the Home Front Command API every second
2. It exposes simple HTTP endpoints with the current alert state
3. A generic Homebridge HTTP plugin (e.g. `homebridge-http-contact-sensor`) polls these endpoints
4. When an alert is active, the HomeKit accessory state changes (contact sensor opens)

## Setup

### 1. Install red-alert

```bash
# Clone the repository
git clone https://github.com/seidnerj/red-alert.git
cd red-alert

# Install dependencies (using pip or uv)
pip install aiohttp
```

### 2. Start the Server

**Basic (all alerts, default port 8512):**
```bash
python -m red_alert.integrations.outputs.homebridge
```

**With options:**
```bash
python -m red_alert.integrations.outputs.homebridge --port 8512 --interval 1
```

**With a config file:**
```bash
python -m red_alert.integrations.outputs.homebridge --config config.json
```

**Example `config.json`:**
```json
{
    "host": "0.0.0.0",
    "port": 8512,
    "interval": 1,
    "areas_of_interest": [
        "tel aviv - city center",
        "haifa - city center"
    ]
}
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `host` | Server bind address | `0.0.0.0` |
| `port` | Server port | `8512` |
| `interval` | API polling interval in seconds | `1` |
| `areas_of_interest` | Areas/cities to filter for the `/city` endpoint (empty = all) | `[]` |

### 3. Verify the Server

```bash
# Health check
curl http://localhost:8512/health
# {"status": "ok"}

# Full status
curl http://localhost:8512/status
# {"active": false, "city_active": false, "state": "routine", "alert": null, "last_update": "..."}

# Simple contact sensor value (0 = no alert, 1 = alert active)
curl http://localhost:8512/contact
# 0

# City-filtered contact sensor value
curl http://localhost:8512/city
# 0

# Alert state as text (routine / pre_alert / alert)
curl http://localhost:8512/state
# routine
```

### 4. Install the Homebridge Plugin

Install [homebridge-http-contact-sensor](https://www.npmjs.com/package/homebridge-http-contact-sensor):

```bash
npm install -g homebridge-http-contact-sensor
```

### 5. Configure Homebridge

Add the accessory to your Homebridge `config.json`:

**Basic - all alerts in Israel:**
```json
{
    "accessories": [
        {
            "accessory": "HttpContactSensor",
            "name": "Israel Alert",
            "pollInterval": 1000,
            "statusUrl": "http://localhost:8512/contact",
            "statusPattern": "1"
        }
    ]
}
```

**City-filtered - only alerts for your configured cities:**
```json
{
    "accessories": [
        {
            "accessory": "HttpContactSensor",
            "name": "My City Alert",
            "pollInterval": 1000,
            "statusUrl": "http://localhost:8512/city",
            "statusPattern": "1"
        }
    ]
}
```

**Both sensors:**
```json
{
    "accessories": [
        {
            "accessory": "HttpContactSensor",
            "name": "Israel Alert",
            "pollInterval": 1000,
            "statusUrl": "http://localhost:8512/contact",
            "statusPattern": "1"
        },
        {
            "accessory": "HttpContactSensor",
            "name": "My City Alert",
            "pollInterval": 1000,
            "statusUrl": "http://localhost:8512/city",
            "statusPattern": "1"
        }
    ]
}
```

## API Endpoints

| Endpoint | Response | Description |
|----------|----------|-------------|
| `GET /contact` | `0` or `1` | All alerts: `1` when any alert or pre-alert is active |
| `GET /city` | `0` or `1` | Filtered: `1` only when a configured city is in the alert |
| `GET /state` | text | Alert state: `routine`, `pre_alert`, or `alert` |
| `GET /status` | JSON | Full status with alert details, state, and timestamp |
| `GET /health` | JSON | Health check (`{"status": "ok"}`) |

## Running as a Service

To keep the server running in the background, use systemd (Linux) or launchd (macOS).

**systemd example (`/etc/systemd/system/redalert.service`):**
```ini
[Unit]
Description=red-alert Homebridge Server
After=network.target

[Service]
Type=simple
User=homebridge
WorkingDirectory=/path/to/red-alert
ExecStart=/usr/bin/python3 -m red_alert.integrations.outputs.homebridge --config /path/to/config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable redalert
sudo systemctl start redalert
```

## HomeKit Behavior

- **Contact Sensor "open"** = alert is active (abnormal state)
- **Contact Sensor "closed"** = no alert (normal/routine)

You can use this in HomeKit automations, e.g.:
- Turn on all lights when the contact sensor opens
- Send a notification via HomeKit when an alert is detected
- Trigger a scene that activates specific accessories
