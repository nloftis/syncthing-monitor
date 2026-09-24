#!/usr/bin/env python3
import json, os, smtplib, ssl, sys, tempfile, time
import urllib.error, urllib.parse, urllib.request
from collections import deque
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

ST_URL = os.getenv("ST_URL", "http://127.0.0.1:8384").rstrip("/")
ST_API_KEY = os.getenv("ST_API_KEY", "")
STATE_FILE = Path(os.getenv("STATE_FILE", "/state/monitor-state.json"))
EVENT_TYPES = "RemoteChangeDetected"
STATUS_INTERVAL = int(os.getenv("STATUS_INTERVAL", "60"))
RESTART_CHECK_INTERVAL = int(os.getenv("RESTART_CHECK_INTERVAL", "60"))
REMOTE_DELETE_THRESHOLD = int(os.getenv("REMOTE_DELETE_THRESHOLD", "50"))
REMOTE_DELETE_WINDOW = int(os.getenv("REMOTE_DELETE_WINDOW", "300"))
NOTIFY_RETRY_INTERVAL = int(os.getenv("NOTIFY_RETRY_INTERVAL", "60"))
MAX_PATHS = int(os.getenv("MAX_PATHS", "20"))
NOTIFY_METHOD = os.getenv("NOTIFY_METHOD", "email").lower()

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
MAIL_FROM = os.getenv("MAIL_FROM", SMTP_USER)
MAIL_TO = os.getenv("MAIL_TO", "")

def parse_folders(value):
    folders = {}
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue

        fid, sep, label = item.partition(":")
        fid = fid.strip()
        label = label.strip()

        if not sep or not fid or not label:
            raise ValueError(
                f"invalid FOLDERS entry {item!r}; expected id:label"
            )

        folders[fid] = label

    return folders

FOLDERS = parse_folders(os.getenv("FOLDERS", ""))

def validate_config(
    *,
    st_api_key,
    folders,
    notify_method,
    smtp_user,
    smtp_password,
    mail_from,
    mail_to,
):
    required = {
        "ST_API_KEY": st_api_key,
        "FOLDERS": folders,
        "SMTP_USER": smtp_user,
        "SMTP_PASSWORD": smtp_password,
        "MAIL_FROM": mail_from,
        "MAIL_TO": mail_to,
    }

    for name, value in required.items():
        if not value:
            raise RuntimeError(f"{name} is empty")

    if notify_method != "email":
        raise RuntimeError(
            f"unsupported NOTIFY_METHOD: {notify_method!r}"
        )

def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)

def api(path, params=None, timeout=35):
    url = ST_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"X-API-Key": ST_API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"Syncthing API request failed: {e}") from e
    try:
        return json.loads(raw.decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RuntimeError("Syncthing API returned invalid JSON") from e

def atomic_save(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=STATE_FILE.name + ".", dir=STATE_FILE.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    finally:
        try: os.unlink(tmp)
        except FileNotFoundError: pass

def load_state():
    if not STATE_FILE.exists():
        return None
    try:
        with STATE_FILE.open() as f:
            state = json.load(f)
        if not isinstance(state, dict):
            raise ValueError("state is not an object")
        return state
    except (OSError, json.JSONDecodeError, ValueError) as e:
        raise RuntimeError(f"Cannot load state file: {e}") from e

def start_time():
    value = api("/rest/system/status", timeout=10).get("startTime")
    if not value:
        raise RuntimeError("system/status did not contain startTime")
    return value

def newest_event_id():
    events = api("/rest/events",
                 {"events": EVENT_TYPES, "limit": 1, "timeout": 1}, timeout=5)
    return int(events[-1]["id"]) if events else 0

def fresh_state(st, last_id):
    return {
        "version": 4, "syncthingStartTime": st, "lastEventId": last_id,
        "receiveOnly": {fid: None for fid in FOLDERS},
        "remoteDeletes": {}, "pendingNotifications": []
    }

def normalize_state(s):
    s["version"] = 4
    s.setdefault("syncthingStartTime", "")
    s.setdefault("lastEventId", 0)
    s.setdefault("receiveOnly", {})

    for fid in FOLDERS:
        value = s["receiveOnly"].get(fid)
        if isinstance(value, bool):
            # Stage 1 persisted only clean/dirty Boolean state. It cannot
            # establish the Stage 2 counter baseline, so require a fresh
            # /rest/db/status observation.
            s["receiveOnly"][fid] = None
        elif fid not in s["receiveOnly"]:
            s["receiveOnly"][fid] = None

    s.setdefault("remoteDeletes", {})
    s.setdefault("pendingNotifications", [])
    return s

def queue_notice(s, subject, body):
    s["pendingNotifications"].append({
        "subject": subject, "body": body,
        "created": datetime.now(timezone.utc).isoformat()
    })

def send_notice(subject, body):
    if NOTIFY_METHOD == "email":
        if not (SMTP_USER and SMTP_PASSWORD and MAIL_TO):
            raise RuntimeError("SMTP credentials/MAIL_TO incomplete")
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = subject, MAIL_FROM, MAIL_TO
        msg.set_content(body)
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT,
                              context=ssl.create_default_context(), timeout=30) as smtp:
            smtp.login(SMTP_USER, SMTP_PASSWORD)
            smtp.send_message(msg)
    else:
        raise RuntimeError(f"unknown NOTIFY_METHOD={NOTIFY_METHOD!r}")

def flush_notice(s):
    if not s["pendingNotifications"]: return
    n = s["pendingNotifications"][0]
    try:
        send_notice(n["subject"], n["body"])
    except Exception as e:
        log(f"notification failed; retained for retry: {e}")
        return
    s["pendingNotifications"].pop(0)
    atomic_save(s)
    log(f"notification sent: {n['subject']}")

def event_epoch(value):
    # Syncthing can emit nanoseconds; Python datetime accepts microseconds.
    if value.endswith("Z"): value = value[:-1] + "+00:00"
    if "." in value:
        head, tail = value.split(".", 1)
        plus = tail.find("+")
        minus = tail.find("-")
        indexes = [x for x in (plus, minus) if x >= 0]
        pos = min(indexes) if indexes else -1
        frac, offset = (tail[:pos], tail[pos:]) if pos >= 0 else (tail, "")
        value = head + "." + frac[:6] + offset
    return datetime.fromisoformat(value).timestamp()

def label(fid, data=None):
    return FOLDERS.get(fid) or (data or {}).get("label") or fid

RECEIVE_ONLY_COUNTERS = (
    "receiveOnlyChangedFiles",
    "receiveOnlyChangedDirectories",
    "receiveOnlyChangedSymlinks",
    "receiveOnlyChangedDeletes",
    "receiveOnlyChangedBytes",
    "receiveOnlyTotalItems",
)

def ro_status(fid):
    status = api("/rest/db/status", {"folder": fid}, timeout=15)
    return {
        key: int(status.get(key, 0) or 0)
        for key in RECEIVE_ONLY_COUNTERS
    }

def evaluate_receive_only(previous, observation, *, observed_at):
    current = {
        key: int(observation.get(key, 0) or 0)
        for key in RECEIVE_ONLY_COUNTERS
    }
    count = current["receiveOnlyTotalItems"]

    previous_count = (
        None
        if previous is None
        else int(previous.get("lastObservedCount", 0))
    )
    last_alerted_count = (
        None if previous is None else previous.get("lastAlertedCount")
    )
    last_alert_time = (
        None if previous is None else previous.get("lastAlertTime")
    )

    alerts = []

    if previous_count is None:
        if count > 0:
            alerts.append("initial")
            last_alerted_count = count
            last_alert_time = observed_at
    elif previous_count == 0 and count > 0:
        alerts.append("initial")
        last_alerted_count = count
        last_alert_time = observed_at
    elif previous_count > 0 and count > previous_count:
        alerts.append("worsened")
    elif previous_count > 0 and count == 0:
        last_alerted_count = None
        last_alert_time = None

    current.update({
        "lastObservedCount": count,
        "lastAlertedCount": last_alerted_count,
        "lastAlertTime": last_alert_time,
    })

    return current, alerts

def check_receive_only(s, force_save=False):
    changed = False

    for fid in FOLDERS:
        try:
            observation = ro_status(fid)
        except Exception as e:
            log(f"status check failed for {label(fid)}: {e}")
            continue

        previous = s["receiveOnly"].get(fid)
        observed_at = datetime.now(timezone.utc).isoformat()
        new_state, alerts = evaluate_receive_only(
            previous,
            observation,
            observed_at=observed_at,
        )

        if previous != new_state:
            s["receiveOnly"][fid] = new_state
            changed = True

        if "initial" in alerts:
            queue_notice(
                s,
                f"[Syncthing] local changes detected — {label(fid)}",
                f"Receive Only folder '{label(fid)}' has local NAS changes.\n"
                f"Changed items: {new_state['receiveOnlyTotalItems']}\n"
                f"Changed bytes: {new_state['receiveOnlyChangedBytes']}\n\n"
                "Review Syncthing before using Revert Local Changes.",
            )
            changed = True

        if "worsened" in alerts:
            log(
                f"receive-only divergence worsened: {label(fid)} "
                f"{previous['lastObservedCount']} -> "
                f"{new_state['lastObservedCount']}; "
                "notification deferred pending coalescing policy"
            )

        if (
            previous is not None
            and previous.get("lastObservedCount", 0) > 0
            and new_state["lastObservedCount"] == 0
        ):
            log(f"receive-only state cleared: {label(fid)}")

    if changed or force_save:
        atomic_save(s)

def incident(s, fid):
    return s["remoteDeletes"].setdefault(fid, {
        "events": [], "active": False, "burstTotal": 0,
        "firstTime": None, "lastTime": None, "samplePaths": []
    })

def remote_delete(s, ev):
    d = ev.get("data", {})
    if d.get("action") != "deleted" or d.get("type") != "file": return
    fid = d.get("folder") or d.get("folderID")
    if not fid: return
    ts, path = event_epoch(ev["time"]), d.get("path", "(unknown)")
    inc = incident(s, fid)
    q = deque((float(t), p) for t, p in inc.get("events", []))
    q.append((ts, path))
    cutoff = ts - REMOTE_DELETE_WINDOW
    while q and q[0][0] < cutoff: q.popleft()
    inc["events"] = [[t, p] for t, p in q]

    if not inc["active"] and len(q) >= REMOTE_DELETE_THRESHOLD:
        inc.update(active=True, burstTotal=len(q), firstTime=q[0][0],
                   lastTime=ts, samplePaths=[p for _, p in list(q)[:MAX_PATHS]])
        queue_notice(
            s, f"[Syncthing] HIGH remote deletion activity — {label(fid, d)}",
            f"At least {len(q)} file deletions were observed in '{label(fid, d)}' "
            f"within {REMOTE_DELETE_WINDOW} seconds.\n\n"
            "The deletions originated on the authoritative side and may already "
            "have propagated to the NAS. Review before recovery. Staggered File "
            "Versioning should retain eligible deleted/replaced files.\n\n"
            "Sample paths:\n" + "\n".join(f"- {p}" for p in inc["samplePaths"]))
        log(f"remote deletion threshold crossed: {label(fid, d)} ({len(q)} files)")
    elif inc["active"]:
        inc["burstTotal"] = int(inc["burstTotal"]) + 1
        inc["lastTime"] = ts
        if len(inc["samplePaths"]) < MAX_PATHS: inc["samplePaths"].append(path)
    elif len(q) >= max(1, REMOTE_DELETE_THRESHOLD // 2):
        log(f"remote deletion activity: {label(fid, d)} ({len(q)}/{REMOTE_DELETE_THRESHOLD})")

def close_quiet_incidents(s):
    now, changed = time.time(), False
    for fid, inc in list(s["remoteDeletes"].items()):
        if not inc.get("active"): continue
        last = inc.get("lastTime")
        if last is None or now - float(last) < REMOTE_DELETE_WINDOW: continue
        total = int(inc.get("burstTotal", 0))
        first = inc.get("firstTime")
        duration = max(0, int(float(last) - float(first))) if first is not None else 0
        queue_notice(s, f"[Syncthing] remote deletion burst ended — {label(fid)}",
                     f"Remote deletion incident for '{label(fid)}' is quiet.\n"
                     f"Total observed file deletions: {total}\n"
                     f"Observed duration: {duration} seconds\n\n"
                     "Review the live folder and .stversions before recovery.")
        s["remoteDeletes"][fid] = {
            "events": [], "active": False, "burstTotal": 0,
            "firstTime": None, "lastTime": None, "samplePaths": []
        }
        changed = True
        log(f"remote deletion incident ended: {label(fid)} ({total} files)")
    if changed: atomic_save(s)

def process_event(s, ev):
    if ev.get("type") == "RemoteChangeDetected":
        remote_delete(s, ev)

def process_event_batch(s, events):
    for event in events:
        try:
            process_event(s, event)
            s["lastEventId"] = int(event["id"])
            atomic_save(s)
        except Exception as e:
            log(
                f"event {event.get('id', '?')} failed; "
                f"cursor not advanced: {e}"
            )
            break

def restart(s, new_st):
    log("Syncthing restart detected; resetting cursor for new process")
    s["syncthingStartTime"], s["lastEventId"], s["remoteDeletes"] = new_st, 0, {}
    atomic_save(s)
    check_receive_only(s, True)

def wait_syncthing():
    while True:
        try: return start_time()
        except Exception as e:
            log(f"waiting for Syncthing: {e}")
            time.sleep(10)

def main():
    validate_config(
        st_api_key=ST_API_KEY,
        folders=FOLDERS,
        notify_method=NOTIFY_METHOD,
        smtp_user=SMTP_USER,
        smtp_password=SMTP_PASSWORD,
        mail_from=MAIL_FROM,
        mail_to=MAIL_TO,
    )
    current = wait_syncthing()
    s = load_state()
    if s is None:
        baseline = newest_event_id()
        s = fresh_state(current, baseline)
        atomic_save(s)
        log(f"initialized new state at combined event id {baseline}")
        check_receive_only(s, True)
    else:
        s = normalize_state(s)
        atomic_save(s)
        if s["syncthingStartTime"] == current:
            log(f"resuming combined event stream after id {s['lastEventId']}")
        else:
            restart(s, current)

    last_status = last_restart = last_notify = 0.0
    while True:
        now = time.time()
        if now - last_restart >= RESTART_CHECK_INTERVAL:
            try:
                current = start_time()
                if current != s["syncthingStartTime"]: restart(s, current)
                last_restart = now
            except Exception as e: log(f"restart check failed: {e}")

        if now - last_status >= STATUS_INTERVAL:
            check_receive_only(s)
            last_status = now

        close_quiet_incidents(s)

        if s["pendingNotifications"] and now - last_notify >= NOTIFY_RETRY_INTERVAL:
            flush_notice(s); last_notify = now

        try:
            events = api("/rest/events", {
                "events": EVENT_TYPES, "since": int(s["lastEventId"]), "timeout": 30
            }, timeout=35)
        except Exception as e:
            log(f"event poll failed: {e}")
            try:
                current = start_time()
                if current != s["syncthingStartTime"]: restart(s, current)
            except Exception as e2: log(f"restart check after poll failure failed: {e2}")
            time.sleep(5)
            continue

        process_event_batch(s, events)

if __name__ == "__main__":
    try: main()
    except KeyboardInterrupt: pass
    except Exception as e:
        log(f"fatal: {e}")
        sys.exit(1)
