#!/usr/bin/env python3
"""Monitoring daemon: reads host metrics (Linux) and shows them on the display
as rotating PAGES (NET -> CPU/MEM -> DISK -> SYSTEM), using the display-ctl
protocol (see PROTOCOL.md). Drives LED/buzzer/alert images by threshold.

Dependencies: pyserial + stdlib only (reads /proc, /sys; uses timedatectl/
systemctl/who if available). Portable to any headless server.

Usage:
    python tools/monitor.py [PORT] [--page-secs 5] [--refresh 1] [--disk /]
                            [--no-alerts] [--once]

Install as a service: see INSTALL.md.
"""
import argparse
import glob
import os
import signal
import socket
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import serial  # noqa: E402
from displayctl import DisplayCtl, DisplayError  # noqa: E402

# thresholds (warn, crit) per metric
THRESH = {"cpu": (85, 96), "mem": (85, 95), "disk": (90, 97), "temp": (75, 88)}


# ============ helpers ============
def gb(x):
    return x / (1024.0 ** 3)


def pct(v):
    return "%.0f%%" % v if v is not None else "--"


def val(v):
    return str(v) if v is not None else "?"


def human_rate(bps):
    """bytes/s -> compact string."""
    if bps is None:
        return "--"
    b = float(bps)
    for u in ("", "k", "M", "G"):
        if b < 1000:
            return ("%.0f%s" if u == "" else "%.1f%s") % (b, u)
        b /= 1000.0
    return "%.1fT" % b


def human_mem(b):
    """bytes -> compact RAM string (binary units)."""
    g = b / 1024.0 ** 3
    return "%.1fG" % g if g >= 1 else "%.0fM" % (b / 1024.0 ** 2)


class Cached:
    """Memoize the result for `ttl` seconds (for subprocess-based metrics)."""
    def __init__(self, fn, ttl):
        self.fn, self.ttl, self.t, self.v = fn, ttl, 0.0, None

    def __call__(self, *a):
        now = time.time()
        if now - self.t >= self.ttl:
            self.v = self.fn(*a)
            self.t = now
        return self.v


# ============ metrics ============
class CpuMeter:
    def __init__(self):
        self.prev = None

    def read(self):
        try:
            with open("/proc/stat") as f:
                vals = list(map(int, f.readline().split()[1:]))
        except OSError:
            return None
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        if self.prev is None:
            self.prev = (idle, total)
            return None
        pidle, ptotal = self.prev
        self.prev = (idle, total)
        dt = total - ptotal
        return 100.0 * (1.0 - (idle - pidle) / dt) if dt > 0 else None


class NetRate:
    def __init__(self):
        self.prev = None
        self.t = None

    def read(self, iface):
        if not iface:
            return (None, None)
        try:
            for line in open("/proc/net/dev"):
                if ":" not in line:
                    continue
                name, data = line.split(":")
                if name.strip() != iface:
                    continue
                v = data.split()
                rx, tx, now = int(v[0]), int(v[8]), time.time()
                if self.prev is None or self.t is None:
                    self.prev, self.t = (rx, tx), now
                    return (None, None)
                dt = now - self.t
                if dt <= 0:
                    return (None, None)
                r = ((rx - self.prev[0]) / dt, (tx - self.prev[1]) / dt)
                self.prev, self.t = (rx, tx), now
                return r
        except Exception:
            pass
        return (None, None)


class TopProc:
    """Top processes by CPU (over the interval) and by RSS memory, from a single
    pass over /proc/<pid>/stat. Returns {"cpu": [(comm, pct)...],
    "mem": [(comm, rss_bytes)...]}, each sorted desc and capped at `n`."""
    def __init__(self, n=5):
        self.prev, self.t, self.n = {}, None, n
        self.tck = os.sysconf("SC_CLK_TCK")
        self.pg = os.sysconf("SC_PAGE_SIZE")

    def read(self):
        now = time.time()
        dt = (now - self.t) if self.t else None
        cur, cpus, mems = {}, [], []
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open("/proc/%s/stat" % pid) as f:
                    data = f.read()
                rp = data.rfind(")")
                comm = data[data.find("(") + 1:rp]
                rest = data[rp + 2:].split()
                tot = int(rest[11]) + int(rest[12])     # utime + stime
                rss = int(rest[21]) * self.pg           # resident set, bytes
            except Exception:
                continue
            cur[pid] = (tot, comm)
            if rss > 0:
                mems.append((comm, rss))
            if dt and dt > 0 and pid in self.prev:
                cpu = 100.0 * ((tot - self.prev[pid][0]) / self.tck) / dt
                if cpu > 0:
                    cpus.append((comm, cpu))
        self.prev, self.t = cur, now
        cpus.sort(key=lambda c: -c[1])
        mems.sort(key=lambda c: -c[1])
        return {"cpu": cpus[:self.n], "mem": mems[:self.n]}


def read_meminfo():
    info = {}
    for line in open("/proc/meminfo"):
        k, v = line.split(":")
        info[k] = int(v.split()[0])      # kB
    return info


def mem_pct(info):
    t = info["MemTotal"]
    a = info.get("MemAvailable", info.get("MemFree", 0))
    return 100.0 * (1.0 - a / t) if t else None


def mem_used_bytes(info):
    t = info["MemTotal"]
    a = info.get("MemAvailable", info.get("MemFree", 0))
    return (t - a) * 1024


def swap_pct(info):
    st, sf = info.get("SwapTotal", 0), info.get("SwapFree", 0)
    return 100.0 * (st - sf) / st if st else 0.0


def disk_info(path):
    try:
        s = os.statvfs(path)
        bs = s.f_frsize
        total, free = s.f_blocks * bs, s.f_bavail * bs
        used = (s.f_blocks - s.f_bfree) * bs
        p = 100.0 * used / (used + free) if (used + free) else None
        return p, used, total, free
    except Exception:
        return None, 0, 0, 0


def temp_c():
    best = None
    for p in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
        try:
            v = int(open(p).read().strip()) / 1000.0
        except Exception:
            continue
        if 0 < v < 200 and (best is None or v > best):
            best = v
    return best


def primary_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))      # sends nothing; just resolves the route
        ip = s.getsockname()[0]
        return ip if ip and ip != "0.0.0.0" else None
    except OSError:
        return None
    finally:
        s.close()


def gateway_iface():
    try:
        for line in open("/proc/net/route").readlines()[1:]:
            f = line.split()
            if f[1] == "00000000" and (int(f[3], 16) & 2):   # default + RTF_GATEWAY
                return socket.inet_ntoa(struct.pack("<L", int(f[2], 16))), f[0]
    except Exception:
        pass
    return None, None


def link_down(iface):
    """Carrier/cable state of the gateway interface (the one bearing the default
    route): True = no carrier (cable unplugged / link down), False = link up,
    None = unknown (no iface, or it can't report). We check only this interface
    on purpose — scanning every NIC would false-trigger on idle bridges/down
    spares. /sys/.../carrier returns EINVAL while the iface is administratively
    down, so we fall back to operstate in that case."""
    if not iface:
        return None
    base = "/sys/class/net/%s" % iface
    try:
        with open(base + "/carrier") as f:
            return f.read().strip() == "0"
    except OSError:
        pass
    try:
        with open(base + "/operstate") as f:
            return f.read().strip() not in ("up", "unknown")
    except OSError:
        return None


def _estab_on_port(path, port_hex):
    n = 0
    try:
        for line in open(path).readlines()[1:]:
            f = line.split()
            if f[1].split(":")[1].upper() == port_hex and f[3] == "01":
                n += 1
    except Exception:
        pass
    return n


def ssh_sessions():
    return _estab_on_port("/proc/net/tcp", "0016") + _estab_on_port("/proc/net/tcp6", "0016")


def reboot_required():
    return os.path.exists("/run/reboot-required") or os.path.exists("/var/run/reboot-required")


def uptime_s():
    try:
        return float(open("/proc/uptime").read().split()[0])
    except Exception:
        return None


def human_uptime(s):
    if s is None:
        return "--"
    d, h, m = int(s // 86400), int(s % 86400 // 3600), int(s % 3600 // 60)
    return "%dd %dh" % (d, h) if d else "%dh %dm" % (h, m) if h else "%dm" % m


def loadtuple():
    try:
        return os.getloadavg()
    except Exception:
        return None


def _ntp_synced():
    try:
        out = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True, timeout=2)
        v = out.stdout.strip()
        return True if v == "yes" else False if v == "no" else None
    except Exception:
        return None


def _failed_unit_names():
    """Names of failed systemd units (e.g. 'nginx.service'); None if unknown."""
    try:
        out = subprocess.run(["systemctl", "--failed", "--no-legend", "--plain"],
                             capture_output=True, text=True, timeout=3)
        return [l.split()[0] for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return None


def _logged_users():
    try:
        out = subprocess.run(["who"], capture_output=True, text=True, timeout=2)
        return len([l for l in out.stdout.splitlines() if l.strip()])
    except Exception:
        return None


# subprocess-based: cache for 5s so we don't spawn one every refresh
ntp_synced = Cached(_ntp_synced, 5)
failed_unit_names = Cached(_failed_unit_names, 5)
logged_users = Cached(_logged_users, 5)


def collect(st, disk_path):
    info = read_meminfo()
    gw, iface = gateway_iface()
    rx, tx = st["net"].read(iface)
    dpct, dused, dtot, dfree = disk_info(disk_path)
    fu = failed_unit_names()
    top = st["top"].read()
    return {
        "host": socket.gethostname(), "ip": primary_ip(), "gw": gw, "iface": iface,
        "link_down": link_down(iface),
        "rx": rx, "tx": tx, "cpu": st["cpu"].read(),
        "mem": mem_pct(info), "mem_used": gb(mem_used_bytes(info)),
        "mem_total": gb(info["MemTotal"] * 1024), "swap": swap_pct(info),
        "disk": dpct, "diskpath": disk_path, "disk_used": gb(dused),
        "disk_total": gb(dtot), "disk_free": gb(dfree), "temp": temp_c(),
        "up": uptime_s(), "load": loadtuple(), "ntp": ntp_synced(),
        "failed": (len(fu) if fu is not None else None), "failed_list": fu,
        "ssh": ssh_sessions(), "users": logged_users(),
        "reboot": reboot_required(), "top": (top["cpu"][0] if top["cpu"] else None),
        "cpu_top": top["cpu"], "mem_top": top["mem"],
        "clock": time.strftime("%H:%M:%S"),
    }


# ============ severity ============
def severity(m):
    cands = []
    def chk(v, label, unit):
        if v is None or label.lower() not in THRESH:
            return
        warn, crit = THRESH[label.lower()]
        if v >= crit:
            cands.append((2, "%s %.0f%s" % (label, v, unit)))
        elif v >= warn:
            cands.append((1, "%s %.0f%s" % (label, v, unit)))
    chk(m["cpu"], "CPU", "%"); chk(m["mem"], "MEM", "%")
    chk(m["disk"], "DISK", "%"); chk(m["temp"], "TEMP", "C")
    if m.get("failed"):
        cands.append((1, "%d svc fail" % m["failed"]))
    if m.get("link_down"):
        cands.append((2, "LINK DOWN"))         # cable/carrier on the gateway iface
    elif not m.get("ip"):
        cands.append((2, "NO NETWORK"))
    if not cands:
        return (0, None)
    cands.sort(key=lambda c: -c[0])
    return cands[0]


# ============ pages ============
def page_net(m):
    return "NET", [
        "IP  LINK DOWN" if m.get("link_down") else "IP  " + (m["ip"] or "no link"),
        "gw  " + ((m["gw"] + " " + (m["iface"] or "")) if m["gw"] else "--"),
        "net D%s U%s" % (human_rate(m["rx"]), human_rate(m["tx"])),
        "ssh %s  users %s" % (val(m["ssh"]), val(m["users"])),
    ]


def page_cpu(m):
    ld = m["load"]
    return "CPU/MEM", [
        "CPU  %s" % pct(m["cpu"]),
        "load %s" % ("%.2f %.2f %.2f" % ld if ld else "--"),
        "MEM  %s  %.1f/%.0fG" % (pct(m["mem"]), m["mem_used"], m["mem_total"]),
        "swap %s" % pct(m["swap"]),
    ]


def page_disk(m):
    top = m["top"]
    tops = ("%s %s" % (top[0][:9], pct(top[1]))) if top and top[0] else "--"
    return "DISK", [
        "%s  %s" % (m["diskpath"], pct(m["disk"])),
        "used %.0f/%.0fG" % (m["disk_used"], m["disk_total"]),
        "temp %s" % (("%.0fC" % m["temp"]) if m["temp"] is not None else "--"),
        "top  %s" % tops,
    ]


def page_sys(m):
    return "SYSTEM", [
        "up %s" % human_uptime(m["up"]),
        "failed: %s" % (("%d svc" % m["failed"]) if m["failed"] is not None else "?"),
        "reboot: %s" % ("YES" if m["reboot"] else "no"),
        "users: %s  ssh %s" % (val(m["users"]), val(m["ssh"])),
    ]


def frame(d, title, lines, m, level, reason):
    d.cls()
    d.font("SMALL")
    d.text(0, "L", title)
    d.text(0, "R", (reason or "")[:14] if level else m["host"][:12])
    for i, txt in enumerate(lines[:4]):
        d.text(i + 1, "L", txt[:21])
    ntp = "ok" if m["ntp"] else "NO" if m["ntp"] is False else "?"
    d.text(5, "L", m["clock"])
    d.text(5, "R", "NTP " + ntp)
    d.show()


def make_simple(builder):
    """Wrap a (title, lines) builder into a full-frame renderer."""
    def render(d, m, level, reason):
        title, lines = builder(m)
        frame(d, title, lines, m, level, reason)
    return render


def render_host(d, m, level, reason):
    """Identity page: hostname (large) + primary IP."""
    d.cls()
    d.font("SMALL")
    d.text(0, "L", "HOST")
    if level:
        d.text(0, "R", (reason or "")[:14])
    d.font("MED")
    d.text(1, "C", m["host"][:18])
    d.font("SMALL")
    d.text(3, "C", "IP " + (m["ip"] or "no link"))
    ntp = "ok" if m["ntp"] else "NO" if m["ntp"] is False else "?"
    d.text(5, "L", m["clock"])
    d.text(5, "R", "NTP " + ntp)
    d.show()


def short_unit(name):
    """Drop the noisy '.service' suffix; keep other unit types (.timer, .mount)."""
    return name[:-len(".service")] if name.endswith(".service") else name


def render_failed(d, m, level, reason):
    """Alert page: lists the failed systemd units. Only shown while any is down."""
    units = m.get("failed_list") or []
    d.cls()
    d.font("SMALL")
    d.text(0, "L", "FAILED")
    d.text(0, "R", "%d svc" % len(units))
    rows = [short_unit(u) for u in units]
    if len(rows) > 5:                            # 5 content lines (1..5) available
        rows = rows[:4] + ["+%d more" % (len(rows) - 4)]
    for i, name in enumerate(rows[:5]):
        d.text(i + 1, "L", name[:21])
    d.show()


def render_cpu_top(d, m, level, reason):
    """Alert page: processes using the most CPU. Shown only while CPU is high."""
    d.cls()
    d.font("SMALL")
    d.text(0, "L", "CPU TOP")
    d.text(0, "R", pct(m["cpu"]))
    for i, (comm, v) in enumerate((m.get("cpu_top") or [])[:5]):
        d.text(i + 1, "L", ("%-15s %3.0f%%" % (comm[:15], v))[:21])
    d.show()


def render_mem_top(d, m, level, reason):
    """Alert page: processes using the most memory. Shown only while MEM is high."""
    d.cls()
    d.font("SMALL")
    d.text(0, "L", "MEM TOP")
    d.text(0, "R", pct(m["mem"]))
    for i, (comm, rss) in enumerate((m.get("mem_top") or [])[:5]):
        d.text(i + 1, "L", ("%-14s %s" % (comm[:14], human_mem(rss)))[:21])
    d.show()


def render_disk_alert(d, m, level, reason):
    """Alert page: the disk that is filling up (mount + usage; no scan)."""
    d.cls()
    d.font("SMALL")
    d.text(0, "L", "DISK")
    d.text(0, "R", pct(m["disk"]))
    d.text(1, "L", ("mount %s" % m["diskpath"])[:21])
    d.text(2, "L", "used %.0f/%.0fG" % (m["disk_used"], m["disk_total"]))
    d.text(3, "L", "free %.1fG" % m["disk_free"])
    ntp = "ok" if m["ntp"] else "NO" if m["ntp"] is False else "?"
    d.text(5, "L", m["clock"])
    d.text(5, "R", "NTP " + ntp)
    d.show()


def render_alert_image(d, m, level, reason):
    """Attention card flashed right before each alert detail page: the warning
    image at level 1, the panic image at level 2 (IMG renders immediately)."""
    d.img("panic" if level == 2 else "warning")


render_alert_image.alert_image = True            # loop gives it a short dwell


PAGES = [render_host, make_simple(page_net), make_simple(page_cpu),
         make_simple(page_disk), make_simple(page_sys)]


def _over_warn(m, key):
    """True when metric `key` is at or above its warning threshold."""
    v = m.get(key)
    return v is not None and v >= THRESH[key][0]


def active_pages(m):
    """Base pages, plus one alert page per problem (right after HOST): failed
    units, and the top CPU/memory consumers or filling disk when over warning.
    Each alert detail page is preceded by the warning/panic image so the screen
    that's about to show the problem is announced."""
    if not m:
        return list(PAGES)
    extra = []
    if m.get("failed"):
        extra.append(render_failed)
    if _over_warn(m, "cpu"):
        extra.append(render_cpu_top)
    if _over_warn(m, "mem"):
        extra.append(render_mem_top)
    if _over_warn(m, "disk"):
        extra.append(render_disk_alert)
    alerts = []
    for p in extra:
        alerts += [render_alert_image, p]        # flash the image before each detail
    return PAGES[:1] + alerts + PAGES[1:]


def actuate(d, level, no_alerts):
    """Edge-triggered: called only when the level changes."""
    if no_alerts:
        if level == 0:
            try: d.led("OFF"); d.buzzer("OFF")
            except Exception: pass
        return
    if level == 0:
        d.led("OFF"); d.buzzer("OFF")
    elif level == 1:
        d.img("warning"); d.beep(2000, 200); d.led("BLINK", 600)
        time.sleep(1.2)
    else:
        d.img("panic"); d.beep(1500, 500); d.led("BLINK", 150)
        time.sleep(1.5)


# ============ main loop ============
def open_display(port):
    while True:
        try:
            return DisplayCtl(port)
        except Exception as e:
            print("waiting for display on %s: %s" % (port, e), file=sys.stderr)
            time.sleep(2)


def main():
    ap = argparse.ArgumentParser(description="Host monitor -> display-ctl")
    ap.add_argument("port", nargs="?", default="/dev/ttyACM0")
    ap.add_argument("--page-secs", type=float, default=8.0, help="seconds per page")
    ap.add_argument("--refresh", type=float, default=1.0, help="refresh within a page")
    ap.add_argument("--disk", default="/")
    ap.add_argument("--no-alerts", action="store_true")
    ap.add_argument("--panic-beep-secs", type=float, default=5.0,
                    help="re-beep interval (s) while panic persists; 0 disables")
    ap.add_argument("--warn-beep-secs", type=float, default=30.0,
                    help="re-beep interval (s) while warning persists; 0 disables")
    ap.add_argument("--alert-img-secs", type=float, default=2.5,
                    help="seconds the warning/panic image flashes before each alert page")
    ap.add_argument("--once", action="store_true", help="render one frame and exit")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    st = {"cpu": CpuMeter(), "net": NetRate(), "top": TopProc()}
    d = open_display(args.port)
    prev, last_beep = -1, 0.0
    pages, m = list(PAGES), None
    try:
        while True:
            for render in pages:
                secs = args.alert_img_secs if getattr(render, "alert_image", False) else args.page_secs
                t_end = time.time() + secs
                while True:
                    try:
                        m = collect(st, args.disk)
                        level, reason = severity(m)
                        now = time.time()
                        if level != prev:
                            actuate(d, level, args.no_alerts)   # beeps on entry
                            prev = level
                            last_beep = now
                        elif level >= 1 and not args.no_alerts:
                            iv = args.panic_beep_secs if level == 2 else args.warn_beep_secs
                            if iv > 0 and now - last_beep >= iv:   # nag while it persists
                                d.beep(1500, 500) if level == 2 else d.beep(2000, 200)
                                last_beep = now
                        render(d, m, level, reason)
                    except DisplayError as e:
                        print("command error (continuing): %s" % e, file=sys.stderr)
                    except (serial.SerialException, OSError) as e:
                        print("display dropped, reconnecting: %s" % e, file=sys.stderr)
                        try: d.close()
                        except Exception: pass
                        d = open_display(args.port)
                        prev = -1
                        break
                    if args.once:
                        return
                    time.sleep(args.refresh)
                    if time.time() >= t_end:
                        break
            pages = active_pages(m)              # recompute once per full cycle
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        try:
            d.led("OFF"); d.buzzer("OFF"); d.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
