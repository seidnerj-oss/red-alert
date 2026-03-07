# Telegram Integration

red-alert can send alert notifications to a Telegram chat or channel via the Bot API. Messages are sent on state transitions:

- **Alert start** - rocket emoji + alert title, cities, description
- **Pre-alert** - warning with affected areas
- **All-clear** - explicit all-clear from the Home Front Command (category 13)
- **Alert ended** - when the alert state returns to routine (cooldown expired)

## How It Works

1. The red-alert monitor polls the Home Front Command API every second
2. It classifies the response into one of four states: ROUTINE, PRE_ALERT, ALERT, or ALL_CLEAR
3. On state transitions, it sends a formatted HTML message to the configured Telegram chat
4. No messages are sent when the state hasn't changed (no spam during ongoing alerts)

## Prerequisites

- A Telegram Bot (create one via [@BotFather](https://t.me/BotFather))
- The bot's API token
- The target chat ID (user, group, or channel)
- Python 3.14+ with httpx installed

## Setup

### 1. Create a Telegram Bot

1. Open Telegram and search for [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts
3. Save the API token (looks like `123456789:ABCdefGhIjKlMnOpQrStUvWxYz`)

### 2. Get Your Chat ID

**For a personal chat:** Send any message to your bot, then visit:
```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```
Look for `"chat":{"id":123456789}` in the response.

**For a group:** Add the bot to the group, send a message, and check `getUpdates`. Group chat IDs are negative numbers.

**For a channel:** Add the bot as an admin of the channel. Use `@channelname` as the chat ID, or get the numeric ID from `getUpdates`.

### 3. Install red-alert

```bash
git clone https://github.com/seidnerj/red-alert.git
cd red-alert

pip install httpx
```

### 4. Create a Config File

**`config.json`:**
```json
{
    "bot_token": "123456789:ABCdefGhIjKlMnOpQrStUvWxYz",
    "chat_id": "-1001234567890",
    "interval": 1,
    "cooldown": 30,
    "areas_of_interest": []
}
```

| Parameter | Description | Default |
|-----------|-------------|---------|
| `bot_token` | Telegram Bot API token from BotFather | required |
| `chat_id` | Target chat/group/channel ID | required |
| `interval` | API polling interval in seconds | `1` |
| `cooldown` | Seconds to hold alert state after API goes empty (prevents false "ended" between salvos) | `None` |
| `areas_of_interest` | Cities/areas to filter alerts for (empty = all of Israel) | `[]` |

### 5. Start the Monitor

```bash
python -m red_alert.integrations.telegram --config config.json
```

## Areas of Interest

By default, notifications are sent for alerts anywhere in Israel. To only receive notifications for specific areas:

```json
{
    "areas_of_interest": [
        "tel aviv - city center",
        "haifa - city center",
        "kfar saba"
    ]
}
```

## Cooldown

The `cooldown` parameter prevents premature "alert ended" messages. During a multi-salvo attack, the API may briefly return empty between waves. With cooldown set (e.g., 30 seconds), the alert state is held until either:

- An explicit all-clear (category 13) is received from the API
- The cooldown period expires without new alerts

## Message Format

Messages use Telegram HTML formatting:

**Alert:** `🚀 <b>Rockets and missiles</b>` followed by city names and shelter instructions

**Pre-alert:** `⚠️ <b>Pre-alert warning</b>` with affected areas

**All-clear:** `✅ <b>All clear</b>`

**Alert ended:** `✅ <b>Alert ended</b>`

Each alert type shows its specific emoji (rocket for missiles, earth for earthquake, airplane for hostile aircraft, etc.).

## Running as a Service

**systemd example (`/etc/systemd/system/redalert-telegram.service`):**
```ini
[Unit]
Description=red-alert Telegram Notification Monitor
After=network.target

[Service]
Type=simple
User=pi
WorkingDirectory=/path/to/red-alert
ExecStart=/usr/bin/python3 -m red_alert.integrations.telegram --config /path/to/config.json
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable redalert-telegram
sudo systemctl start redalert-telegram
```

## Technical Details

The integration uses httpx (the same HTTP client as the core library) to send messages via the Telegram Bot HTTP API. No additional dependencies are required beyond what the core library already uses.
