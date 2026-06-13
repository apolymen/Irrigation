import network
import ntptime
import machine
import uasyncio as asyncio
from machine import WDT
import time

# --- CONFIGURATION & CREDENTIALS ---
WIFI_SSID = "Your_WiFi_Name"
WIFI_PASSWORD = "Your_WiFi_Password"
TIMEZONE_OFFSET_HOURS = 1 

# --- HARDWARE CONFIGURATION ---
ZONE_A_PINS = [2, 3]  
ZONE_B_PINS = [4, 5]  

valves_a = []
valves_b = []

# CRITICAL SAFETY: Immediately secure all low-level trigger pins to High (1)
for pin_num in ZONE_A_PINS:
    valves_a.append(machine.Pin(pin_num, machine.Pin.OUT, value=1))
for pin_num in ZONE_B_PINS:
    valves_b.append(machine.Pin(pin_num, machine.Pin.OUT, value=1))

# --- LIVE PARAMETERS (MODIFIABLE VIA WEB INTERFACE) ---
CONFIG = {
    "zone_a": {
        "name": "Zone A (Valves 1 & 2)",
        "valves": valves_a,
        "duration_sec": 600,
        "day_interval": 2,
        "sched_1_hr": 6, "sched_1_min": 30, "sched_1_en": 1,
        "sched_2_hr": 18, "sched_2_min": 0, "sched_2_en": 1,
        "last_watered_day": 0
    },
    "zone_b": {
        "name": "Zone B (Valves 3 & 4)",
        "valves": valves_b,
        "duration_sec": 600,
        "day_interval": 3,
        "sched_1_hr": 7, "sched_1_min": 30, "sched_1_en": 1,
        "sched_2_hr": 19, "sched_2_min": 30, "sched_2_en": 0,
        "last_watered_day": 0
    }
}

# Global rolling log text container
system_logs = "--- System Boot Init ---\n"

# Activate the Watchdog at 8 seconds
wdt = WDT(timeout=8000)

# --- SYSTEM UTILITIES ---

def log(text):
    """Outputs text to both the USB console and the internal web log array."""
    global system_logs
    try:
        t = time.localtime(time.time() + (TIMEZONE_OFFSET_HOURS * 3600))
        stamp = f"[{t[3]:02d}:{t[4]:02d}:{t[5]:02d}] "
    except:
        stamp = "[00:00:00] "
    
    line = stamp + text
    print(line)
    system_logs += line + "\n"
    
    # Clip memory buffer to last 30 entries
    lines = system_logs.split("\n")
    if len(lines) > 30:
        system_logs = "\n".join(lines[-30:])

def get_local_time():
    return time.localtime(time.time() + (TIMEZONE_OFFSET_HOURS * 3600))

def get_epoch_days():
    return int((time.time() + (TIMEZONE_OFFSET_HOURS * 3600)) // 86400)

# --- BACKEND SCHEDULER ENGINE ---

async def connect_and_sync():
    """Configures a static IP, connects to Wi-Fi, and fetches NTP time."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    
    # --- STATIC IP CONFIGURATION ---
    # Format: (Static_IP, Subnet_Mask, Gateway_IP, DNS_Server)
    # ADJUST THESE FOUR VALUES TO MATCH YOUR SPECIFIC HOME NETWORK:
    STATIC_IP_SETTINGS = ("192.168.1.50", "255.255.255.0", "192.168.1.1", "8.8.8.8")
    
    log(f"Configuring Static IP to: {STATIC_IP_SETTINGS}")
    wlan.ifconfig(STATIC_IP_SETTINGS)
    # -------------------------------
    
    while not wlan.isconnected():
        log("Attempting Wi-Fi Connection...")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        for _ in range(15):
            if wlan.isconnected(): break
            await asyncio.sleep(1)
            wdt.feed()
            
        if not wlan.isconnected():
            log("Wi-Fi down. Retrying connection loop in 30 seconds...")
            for _ in range(30):
                await asyncio.sleep(1)
                wdt.feed()

    log(f"Connected successfully! Fixed Controller URL: http://{wlan.ifconfig()}")
    
    # Force sync with atomic internet clocks
    while True:
        try:
            wdt.feed()
            ntptime.settime()
            t = get_local_time()
            log(f"NTP Time Synchronized: {t}-{t}-{t}")
            return True
        except Exception as e:
            log(f"NTP server handshake failed ({e}). Retrying in 10s...")
            for _ in range(10):
                await asyncio.sleep(1)
                wdt.feed()


async def execute_watering(zone_id):
    """Asynchronously drives valves, feeding the watchdog during runtime."""
    z = CONFIG[zone_id]
    log(f"Executing scheduled cycle for {z['name']}")
    
    for i, valve_pin in enumerate(z["valves"]):
        log(f"Opening Valve {i+1} of {z['name']}")
        valve_pin.value(0) # Relay On
        
        # Safe countdown step segments
        rem = z["duration_sec"]
        while rem > 0:
            await asyncio.sleep(1)
            wdt.feed()
            rem -= 1
            
        valve_pin.value(1) # Relay Off
        log(f"Safely Closed Valve {i+1}")
        
        # Hydraulic line buffer pause
        await asyncio.sleep(1); wdt.feed()
        await asyncio.sleep(1); wdt.feed()
        
    log(f"Cycle finished for {z['name']}")

async def scheduler_task():
    """Background task monitoring the current clock time against target thresholds."""
    log("Scheduler monitoring loop initialized.")
    while True:
        wdt.feed()
        t = get_local_time()
        hr, mn, epoch_day = t[3], t[4], get_epoch_days()
        
        for zone_id in ["zone_a", "zone_b"]:
            z = CONFIG[zone_id]
            
            # Evaluate days interval constraint
            days_since = epoch_day - z["last_watered_day"]
            if z["last_watered_day"] != 0 and days_since < z["day_interval"]:
                continue
                
            # Match active target parameters
            run_triggered = False
            if z["sched_1_en"] and hr == z["sched_1_hr"] and mn == z["sched_1_min"]:
                run_triggered = True
            elif z["sched_2_en"] and hr == z["sched_2_hr"] and mn == z["sched_2_min"]:
                run_triggered = True
                
            if run_triggered:
                z["last_watered_day"] = epoch_day
                await execute_watering(zone_id)
                # Sleep past the active trigger minute mark safely
                for _ in range(60):
                    await asyncio.sleep(1)
                    wdt.feed()

        # Re-verify NTP drift sync daily at midnight
        if hr == 0 and mn == 0:
            try:
                ntptime.settime()
                log("Daily Midnight Time Drift Sync Completed.")
            except:
                log("Midnight NTP adjustment failed; skipping.")
            for _ in range(60):
                await asyncio.sleep(1)
                wdt.feed()

        await asyncio.sleep(5)

# --- WEB DASHBOARD FRONTEND ---

def generate_html_page():
    """Assembles a clean, responsive HTML web console layout."""
    t = get_local_time()
    time_str = f"{t[3]:02d}:{t[4]:02d}"
    
def get_zone_html(z_key):
    z = CONFIG[z_key]
    return f"""
    <div class="zone-card">
        <h2>{z['name']}</h2>
        <form action="/update" method="GET">
            <input type="hidden" name="zone" value="{z_key}">
            <label>Run Duration (Seconds): <input type="number" name="duration" value="{z['duration_sec']}"></label><br>
            <label>Interval (Every X Days): <input type="number" name="interval" value="{z['day_interval']}"></label><br>
            
            <h3>Schedule 1</h3>
            <label>Time: <input type="number" name="s1_hr" min="0" max="23" value="{z['sched_1_hr']}"> : 
            <input type="number" name="s1_mn" min="0" max="59" value="{z['sched_1_min']}"></label><br>
            <label><input type="checkbox" name="s1_en" value="1" {'checked' if z['sched_1_en'] else ''}> Enable Schedule 1</label>
            
            <h3>Schedule 2</h3>
            <label>Time: <input type="number" name="s2_hr" min="0" max="23" value="{z['sched_2_hr']}"> : 
            <input type="number" name="s2_mn" min="0" max="59" value="{z['sched_2_min']}"></label><br>
            <label><input type="checkbox" name="s2_en" value="1" {'checked' if z['sched_2_en'] else ''}> Enable Schedule 2</label><br><br>
            
            <button type="submit" class="btn">Save Configuration</button>
            <a href="/manual?zone={z_key}" class="btn manual-btn">Manual Instant Run</a>
        </form>
    </div>"""

    html = f"""<!DOCTYPE html>
    <html>
    <head>
        <title>Pico 2 W Smart Irrigation</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: Arial, sans-serif; margin: 20px; background: #f0f2f5; color: #333; }}
            .container {{ max-width: 900px; margin: auto; }}
            .header {{ background: #007bff; color: white; padding: 15px; border-radius: 8px; text-align: center; margin-bottom: 20px; }}
            .flex-grid {{ display: flex; gap: 20px; flex-wrap: wrap; }}
            .zone-card {{ background: white; padding: 20px; border-radius: 8px; flex: 1; min-width: 280px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
            input[type=number] {{ width: 60px; padding: 5px; margin: 5px 0; }}
            .btn {{ display: inline-block; background: #28a745; color: white; padding: 10px 15px; text-decoration: none; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; }}
            .manual-btn {{ background: #dc3545; margin-left: 10px; }}
            .logs-card {{ background: #222; color: #00ff00; padding: 15px; border-radius: 8px; margin-top: 20px; font-family: monospace; white-space: pre-wrap; height: 200px; overflow-y: scroll; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Smart Irrigation Dashboard</h1>
                <p>Current Controller Local Time: <strong>{time_str}</strong></p>
            </div>
            <div class="flex-grid">
                {get_zone_html('zone_a')}
                {get_zone_html('zone_b')}
            </div>
            <h2>Live System Output History</h2>
            <div class="logs-card">{system_logs}</div>
        </div>
    </body>
    </html>"""
    return html

def parse_url_params(path):
    """Utility helper to isolate parameters passed from browser form queries."""
    params = {}
    if "?" not in path: return params
    query_str = path.split("?")[1]
    pairs = query_str.split("&")
    for pair in pairs:
        if "=" in pair:
            k, v = pair.split("=")
            params[k] = v
    return params

async def handle_client(reader, writer):
    """Processes incoming HTTP requests and updates internal parameter matrices."""
    wdt.feed()
    try:
        request_line = await reader.readline()
        request = request_line.decode("utf-8")
# Read past remaining HTTP header streams to clear buffer channels
while True:
line = await reader.readline()
if line == b"\r\n" or line == b"": break
parts = request.split(" ")
if len(parts) < 2: return
path = parts[1]
# PARAMETER FORM UPDATES HANDLER
if path.startswith("/update"):
p = parse_url_params(path)
zk = p.get("zone")
if zk in CONFIG:
CONFIG[zk]["duration_sec"] = int(p.get("duration", 600))
CONFIG[zk]["day_interval"] = int(p.get("interval", 1))
CONFIG[zk]["sched_1_hr"] = int(p.get("s1_hr", 6))
CONFIG[zk]["sched_1_min"] = int(p.get("s1_mn", 0))
CONFIG[zk]["sched_1_en"] = 1 if "s1_en" in p else 0
CONFIG[zk]["sched_2_hr"] = int(p.get("s2_hr", 18))
CONFIG[zk]["sched_2_min"] = int(p.get("s2_mn", 0))
CONFIG[zk]["sched_2_en"] = 1 if "s2_en" in p else 0
log(f"Updated settings for {CONFIG[zk]['name']} via dashboard form input.")
# Redirect user cleanly back to homepage root index
writer.write(b"HTTP/1.1 332 Found\r\nLocation: /\r\n\r\n")
await writer.drain()
# INSTANT MANUAL RUN OVERRIDE HANDLER
elif path.startswith("/manual"):
p = parse_url_params(path)
zk = p.get("zone")
if zk in CONFIG:
log(f"Manual override button pressed for {CONFIG[zk]['name']}.")
asyncio.create_task(execute_watering(zk)) # Fire process asynchronously
writer.write(b"HTTP/1.1 332 Found\r\nLocation: /\r\n\r\n")
await writer.drain()
# DEFAULT LANDING SCREEN INDEX
else:
response = generate_html_page()
writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n")
writer.write(response.encode("utf-8"))
await writer.drain()
except Exception as e:
print("Web handler encountered routine error:", e)
finally:
await writer.close()
await writer.wait_closed()
--- MAIN SYSTEM INITIALIZATION ROUTINE ---
async def main():
log("Booting system setup architecture...")
# 1. Block operations until network link established
await connect_and_sync()
# 2. Kick off parallel network listener and time scheduler tasks
asyncio.create_task(scheduler_task())
log("Starting asynchronous web engine listening socket on Port 80...")
server = await asyncio.start_server(handle_client, "0.0.0.0", 80)
# 3. Keep main loop running forever to feed watchdog
while True:
wdt.feed()
await asyncio.sleep(1)
Execute the asynchronous engine loop
try:
asyncio.run(main())
except KeyboardInterrupt:
print("Forced termination. Clearing execution blocks.")
