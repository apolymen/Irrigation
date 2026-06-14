"""
Smart irrigation
"""
import network
import ntptime
import machine
import uasyncio as asyncio
import time

# --- CONFIGURATION & CREDENTIALS ---
WIFI_SSID = "ATHLON"
WIFI_PASSWORD = "Your_WiFi_Password"
TIMEZONE_OFFSET_HOURS = 2

# --- STATIC IP CONFIGURATION ---
# Format: (Static_IP, Subnet_Mask, Gateway_IP, DNS_Server)
STATIC_IP_SETTINGS = ("192.168.0.50", "255.255.255.0", "192.168.0.254", "1.1.1.1")

# --- HARDWARE CONFIGURATION ---
ZONE_A_PINS = [2, 3]  # Map to your input pins
ZONE_B_PINS = [4, 5]

valves_a = []
valves_b = []

# CRITICAL SAFETY: Pull pins high instantly to prevent low-level trigger on boot
for pin_num in ZONE_A_PINS:
    valves_a.append(machine.Pin(pin_num, machine.Pin.OUT, value=1))
for pin_num in ZONE_B_PINS:
    valves_b.append(machine.Pin(pin_num, machine.Pin.OUT, value=1))

# --- LIVE PARAMETERS (MODIFIABLE VIA WEB INTERFACE) ---
CONFIG = {
    "zone_a": {
        "name": "Zone A (Valves 1 & 2)", "valves": valves_a, "duration_sec": 600, "day_interval": 2,
        "sched_1_hr": 6, "sched_1_min": 0, "sched_1_en": 1,
        "sched_2_hr": 18, "sched_2_min": 0, "sched_2_en": 0, "last_watered_day": 0
    },
    "zone_b": {
        "name": "Zone B (Valves 3 & 4)", "valves": valves_b, "duration_sec": 600, "day_interval": 3,
        "sched_1_hr": 23, "sched_1_min": 0, "sched_1_en": 1,
        "sched_2_hr": 19, "sched_2_min": 30, "sched_2_en": 0, "last_watered_day": 0
    }
}

# Global rolling log text container
system_logs = "--- System Boot Init ---\n"

# Activate the Watchdog at 8 seconds
wdt = machine.WDT(timeout=8000)

# --- BASE SYSTEM UTILITIES ---

def log(text):
    """Outputs text to both the USB console and the internal web log array."""
    global system_logs
    try:
        t = time.localtime(time.time() + (TIMEZONE_OFFSET_HOURS * 3600))
        stamp = "[{:02d}:{:02d}:{:02d}] ".format(t[3], t[4], t[5])
    except:
        stamp = "[00:00:00] "

    line = stamp + text
    print(line)
    system_logs += line + "\n"

    # Clip memory buffer to last 25 entries
    lines = system_logs.split("\n")
    if len(lines) > 25:
        system_logs = "\n".join(lines[-25:])

def get_local_time():
    return time.localtime(time.time() + (TIMEZONE_OFFSET_HOURS * 3600))

def get_epoch_days():
    return int((time.time() + (TIMEZONE_OFFSET_HOURS * 3600)) // 86400)

# --- CORE NETWORKING & TIMER SCHEDULER ENGINE ---

async def connect_and_sync():
    """Asynchronously loops until Wi-Fi connects and NTP time matches perfectly."""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    log("Configuring Static IP profile...")
    wlan.ifconfig(STATIC_IP_SETTINGS)

    while not wlan.isconnected():
        log("Attempting Wi-Fi Connection...")
        wlan.connect(WIFI_SSID, WIFI_PASSWORD)
        for _ in range(15):
            if wlan.isconnected(): break
            await asyncio.sleep(1)
            wdt.feed()

        if not wlan.isconnected():
            log("Link down. Retrying router in 30 seconds...")
            for _ in range(30):
                await asyncio.sleep(1)
                wdt.feed()

    log("Connected successfully! System Address: http://" + str(wlan.ifconfig()[0]))

    # Force sync with internet time server
    while True:
        try:
            wdt.feed()
            ntptime.settime()
            t = get_local_time()
            log("NTP Time Synchronised: {:02d}:{:02d}".format(t[3], t[4]))
            return True
        except:
            log("NTP handshake failed. Retrying in 10s...")
            for _ in range(10):
                await asyncio.sleep(1)
                wdt.feed()

async def execute_watering(zone_id):
    """Asynchronously drives valves, feeding the watchdog during runtime."""
    z = CONFIG[zone_id]
    log("Executing scheduled cycle for " + z["name"])
    for i, valve_pin in enumerate(z["valves"]):
        log("Opening Valve " + str(i+1) + " of " + z["name"])
        valve_pin.value(0)

        # Safe countdown step segments
        rem = z["duration_sec"]
        while rem > 0:
            await asyncio.sleep(1)
            wdt.feed()
            rem -= 1

        valve_pin.value(1) # Relay Off
        log("Safely Closed Valve " + str(i+1))

        # Hydraulic line buffer pause
        await asyncio.sleep(1); wdt.feed()
        await asyncio.sleep(1); wdt.feed()

    log("Cycle finished for " + z["name"])

async def scheduler_task():
    """Background task monitoring the current clock time against target thresholds."""
    log("Scheduler monitoring loop initialised.")
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

# --- USER-FACING FRONTEND RESPONSE MANAGER ---

def generate_html_page():
    """Reads the raw standalone HTML document file and populates it with system tokens."""
    try:
        f = open("dashboard.html", "r")
        html = f.read()
        f.close()
    except Exception as e:
        return "<html><body><h1>Internal Storage Read Error: " + str(e) + "</h1></body></html>"

    t = get_local_time()
    time_str = "{:02d}:{:02d}".format(t[3], t[4])

    # Global replacement array
    html = html.replace("{{TIME}}", time_str)
    html = html.replace("{{LOGS}}", system_logs)

    for k in ["zone_a", "zone_b"]:
        sfx = "_A" if k == "zone_a" else "_B"
        z = CONFIG[k]
        html = html.replace("{{NAME" + sfx + "}}", z["name"])
        html = html.replace("{{DUR" + sfx + "}}", str(z["duration_sec"]))
        html = html.replace("{{INT" + sfx + "}}", str(z["day_interval"]))
        html = html.replace("{{S1H" + sfx + "}}", str(z["sched_1_hr"]))
        html = html.replace("{{S1M" + sfx + "}}", str(z["sched_1_min"]))
        html = html.replace("{{S2H" + sfx + "}}", str(z["sched_2_hr"]))
        html = html.replace("{{S2M" + sfx + "}}", str(z["sched_2_min"]))
        html = html.replace("{{S1E" + sfx + "}}", "checked" if z["sched_1_en"] else "")
        html = html.replace("{{S2E" + sfx + "}}", "checked" if z["sched_2_en"] else "")

    return html

def parse_url_params(path):
    """Utility helper to isolate parameters passed from browser form queries."""
    params = {}
    if "?" not in path: return params
    try:
        query_str = path.split("?")[1]
        pairs = query_str.split("&")
        for pair in pairs:
            if "=" in pair:
                parts = pair.split("=")
                params[parts[0]] = parts[1]
    except: pass
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
                log("Updated settings for " + CONFIG[zk]["name"])
            # Redirect user cleanly back to homepage root index
            writer.write(b"HTTP/1.1 303 See Other\r\nLocation: /\r\n\r\n")
            await writer.drain()

        # INSTANT MANUAL RUN OVERRIDE HANDLER
        elif path.startswith("/manual"):
            p = parse_url_params(path)
            zk = p.get("zone")
            if zk in CONFIG:
                log("Manual override triggered for " + CONFIG[zk]["name"])
                asyncio.create_task(execute_watering(zk)) # Fire process asynchronously
            writer.write(b"HTTP/1.1 303 See Other\r\nLocation: /\r\n\r\n")
            await writer.drain()

        # DEFAULT LANDING SCREEN INDEX
        else:
            response = generate_html_page()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nConnection: close\r\n\r\n")
            writer.write(response.encode("utf-8"))
            await writer.drain()

    except Exception as e:
        print("Web internal routing error:", e)
    finally:
        await writer.close()
        await writer.wait_closed()

# --- MAIN SYSTEM INITIALIZATION ROUTINE ---
async def main():
    log("Booting system setup architecture...")
    # 1. Block operations until network link established
    await connect_and_sync()
    # 2. Kick off parallel network listener and time scheduler tasks
    asyncio.create_task(scheduler_task())
    log("Starting asynchronous web server on Port 80...")
    server = await asyncio.start_server(handle_client, "0.0.0.0", 80)
    # 3. Keep main loop running forever to feed watchdog
    while True:
        wdt.feed()
        await asyncio.sleep(1)

# Execute the asynchronous engine loop
try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("Forced termination. Clearing execution blocks.")
