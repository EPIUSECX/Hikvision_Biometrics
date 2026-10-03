import frappe
import requests
import re
from zoneinfo import ZoneInfo
from frappe.model.document import Document
from frappe.utils import get_system_timezone, now_datetime
from requests.auth import HTTPDigestAuth
from datetime import datetime, timedelta

from biometric_integration.employee_checkin_sync import (
    sync_punches_to_employee_checkin,
)


class BiometricIntegrationSettings(Document):
    def validate(self):
        # Keep device host clean (no protocol/path) and ensure active rows are complete.
        for d in (self.devices or []):
            d.ip_address = _normalize_device_host(getattr(d, "ip_address", ""))

            pwd = (
                d.get_password("password") if hasattr(d, "get_password") else None
            ) or getattr(d, "password", None)

            if getattr(d, "is_active", 0) and (
                not d.ip_address or not d.username or not pwd
            ):
                frappe.throw(
                    f"Device row #{d.idx}: IP, Username and Password are required for active devices."
                )


def _normalize_device_host(raw_ip):
    ip = (raw_ip or "").strip()
    ip = re.sub(r"^https?://", "", ip, flags=re.IGNORECASE)
    ip = ip.split("/")[0].strip()
    return ip


def _format_device_datetime(value):
    """Format a site-local datetime with the UTC offset expected by Hikvision ISAPI."""
    value = str(value)
    local_datetime = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    return local_datetime.replace(tzinfo=ZoneInfo(get_system_timezone())).isoformat(
        timespec="seconds"
    )


def _post_device_request(url, username, password, payload, timeout):
    try:
        return requests.post(
            url,
            auth=HTTPDigestAuth(username, password),
            headers={"Content-Type": "application/json"},
            json=payload,
            verify=False,
            timeout=timeout,
        )
    except requests.exceptions.ConnectTimeout:
        raise RuntimeError("Connection timed out")
    except requests.exceptions.ReadTimeout:
        raise RuntimeError("Device response timeout")
    except requests.exceptions.ConnectionError as e:
        raise RuntimeError(f"Connection failed: {str(e)}")
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"HTTP error: {str(e)}")


def _get_device_configs(settings):
    """Return a list of (label, ip, username, password) for all active devices.
    Only active rows from the Devices child table are used.
    """
    devices = []

    # Child table devices
    if getattr(settings, "devices", None):
        for d in settings.devices:
            # Expecting fields: device, ip_address, username, password, is_active
            if getattr(d, "is_active", 0):
                ip = _normalize_device_host(d.ip_address)
                username = d.username
                pwd = d.get_password("password") if hasattr(d, "get_password") else d.password
                if ip and username and pwd:
                    devices.append((d.device or ip, ip, username, pwd))

    return devices


def _sync_for_single_device(settings, label, ip, username, password, start_time, end_time):
    """
    Sync attendance for a single device (one IP).
    Returns (count, skipped) for that device.
    Also sets device_id (IP) on logs and punches if those fields exist.
    """
    url = f"http://{ip}/ISAPI/AccessControl/AcsEvent?format=json"
    headers = {"Content-Type": "application/json"}

    log_has_device_id = frappe.db.has_column("Biometric Attendance Log", "device_id")
    punch_has_device_id = frappe.db.has_column(
        "Biometric Attendance Punch Table", "device_id"
    )

    # Initial fetch to determine total records
    payload = {
        "AcsEventCond": {
            "searchID": "123456789",
            "searchResultPosition": 0,
            "maxResults": 1,
            "major": 5,
            "minor": 75,
            "startTime": start_time,
            "endTime": end_time,
        }
    }

    response = _post_device_request(
        url=url,
        username=username,
        password=password,
        payload=payload,
        timeout=30,
    )

    if response.status_code != 200:
        frappe.throw(
            f"[{label}] Failed to fetch attendance logs. "
            f"Status: {response.status_code}, Response: {response.text}"
        )

    data = response.json()
    total_records = data.get("AcsEvent", {}).get("totalMatches", 0)

    if total_records == 0:
        return 0, 0

    if total_records > 1500:
        frappe.throw(f"[{label}] Too many records to process ({total_records}). Reduce date range.")

    count = 0
    skipped = 0
    position = 0
    batch_size = 30

    while True:
        payload["AcsEventCond"]["searchResultPosition"] = position
        payload["AcsEventCond"]["maxResults"] = batch_size

        response = _post_device_request(
            url=url,
            username=username,
            password=password,
            payload=payload,
            timeout=30,
        )

        if response.status_code != 200:
            frappe.throw(
                f"[{label}] Failed to fetch attendance logs. "
                f"Status: {response.status_code}, Response: {response.text}"
            )

        data = response.json()
        events = data.get("AcsEvent", {}).get("InfoList", [])

        if not events:
            break

        for log in events:
            emp_no = log.get("employeeNoString")
            event_timestamp = log.get("time", "")
            if not emp_no or not event_timestamp:
                continue

            # Convert device time format to Frappe format
            event_datetime = datetime.strptime(event_timestamp[:19], "%Y-%m-%dT%H:%M:%S")

            # Create or get Attendance Log doc for employee and date
            attendance_log = frappe.get_all(
                "Biometric Attendance Log",
                filters={"employee_no": emp_no, "event_date": event_datetime.date()},
                limit_page_length=1,
            )
            if attendance_log:
                doc = frappe.get_doc("Biometric Attendance Log", attendance_log[0].name)
            else:
                doc = frappe.new_doc("Biometric Attendance Log")
                doc.employee_no = emp_no
                doc.event_date = event_datetime.date()

            # Set device_id on log if field exists
            if log_has_device_id:
                doc.device_id = ip

            # Avoid exact duplicate punch time for that employee/date
            existing_punch = (
                frappe.db.sql(
                    """
                    SELECT COUNT(*)
                    FROM `tabBiometric Attendance Punch Table`
                    WHERE parent = %(parent)s
                      AND punch_time = %(punch_time)s
                """,
                    {
                        "parent": doc.name,
                        "punch_time": event_datetime.time(),
                    },
                )[0][0]
                > 0
            )

            if not existing_punch:
                punch_row = {
                    "punch_time": event_datetime.time(),
                    "punch_type": "Auto",  # device punch
                }
                if punch_has_device_id:
                    punch_row["device_id"] = ip

                doc.append("punch_table", punch_row)
                try:
                    doc.save(ignore_permissions=True)
                    count += 1
                except Exception:
                    frappe.log_error(
                        frappe.get_traceback(),
                        f"[{label}] Insert failed for employee {emp_no}",
                    )
                    continue
            else:
                skipped += 1

        position += len(events)

        if len(events) < batch_size:
            break

    return count, skipped


@frappe.whitelist()
def test_connection():
    """
    Test connectivity to all configured devices.
    Returns a list of dicts: {label, ip, status, message}
    """
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    settings = frappe.get_doc("Biometric Integration Settings", "Biometric Integration Settings")
    device_configs = _get_device_configs(settings)

    if not device_configs:
        frappe.throw("No active device configured. Add a device in the Devices table or fill in the main IP.")

    results = []
    for label, ip, username, password in device_configs:
        url = f"http://{ip}/ISAPI/AccessControl/AcsEvent?format=json"
        try:
            resp = _post_device_request(
                url=url,
                username=username,
                password=password,
                payload={
                    "AcsEventCond": {
                        "searchID": "test",
                        "searchResultPosition": 0,
                        "maxResults": 1,
                        "major": 5,
                        "minor": 75,
                        "startTime": _format_device_datetime("2000-01-01 00:00:00"),
                        "endTime": _format_device_datetime("2000-01-02 00:00:00"),
                    }
                },
                timeout=10,
            )
            if resp.status_code == 200:
                results.append({"label": label, "ip": ip, "status": "success", "message": f"Connected (HTTP 200)"})
            elif resp.status_code == 401:
                results.append({"label": label, "ip": ip, "status": "error", "message": f"Authentication failed (HTTP 401) — check username/password"})
            else:
                results.append({"label": label, "ip": ip, "status": "error", "message": f"Unexpected response: HTTP {resp.status_code}"})
        except Exception as e:
            results.append({"label": label, "ip": ip, "status": "error", "message": str(e)})

    return results


@frappe.whitelist()
def sync_attendance():
    """
    Manual sync from device(s) AND directly to Employee Checkin.
    This is used by the button "Sync Attendance (Device + Checkin)".
    """
    msg_parts = []

    # First: sync from devices into logs/punches
    device_msg = sync_attendance_device_only()
    msg_parts.append(device_msg)

    # Then: convert punches -> Employee Checkin
    created, already_synced = sync_punches_to_employee_checkin()
    msg_parts.append(
        f"{created} Employee Checkins created, {already_synced} punches were already synced."
    )

    full_msg = " ".join(msg_parts)
    frappe.msgprint(full_msg)
    return full_msg


@frappe.whitelist()
def sync_attendance_device_only():
    """
    Manual sync from device(s) ONLY:
    - For each active device (child table) OR main IP:
        -> fetch events
        -> fill Biometric Attendance Log + Punch Table
    Does NOT create Employee Checkins. Used internally and can be called separately.
    """
    settings = frappe.get_doc("Biometric Integration Settings", "Biometric Integration Settings")

    # Prepare time window used for ALL devices
    start_time = _format_device_datetime(settings.start_date_and_time)
    end_time = _format_device_datetime(settings.end_date_and_time)

    device_configs = _get_device_configs(settings)
    if not device_configs:
        frappe.throw(
            "No active device configured in Devices table, or required credentials are missing."
        )

    total_count = 0
    total_skipped = 0
    failed_devices = []

    frappe.publish_progress(
        0,
        title="Attendance Sync",
        description="Starting attendance sync from devices...",
    )

    for idx, (label, ip, username, password) in enumerate(device_configs, start=1):
        frappe.publish_progress(
            (idx - 1) * 100.0 / max(len(device_configs), 1),
            title="Attendance Sync",
            description=f"Syncing device {idx}/{len(device_configs)}: {label} ({ip})",
        )

        try:
            c, s = _sync_for_single_device(
                settings=settings,
                label=label,
                ip=ip,
                username=username,
                password=password,
                start_time=start_time,
                end_time=end_time,
            )
            total_count += c
            total_skipped += s
        except Exception as e:
            err = f"{label} ({ip}): {str(e)}"
            failed_devices.append(err)
            frappe.log_error(frappe.get_traceback(), f"Device sync failed: {label} ({ip})")
            continue

    # Save all logs/punches
    frappe.db.commit()

    msg = (
        f"{total_count} attendance records synced from devices; "
        f"{total_skipped} duplicate punches skipped."
    )
    if failed_devices:
        msg += f" Failed devices: {len(failed_devices)}. " + " | ".join(failed_devices)

    frappe.publish_progress(100, title="Attendance Sync", description=msg)
    return msg


@frappe.whitelist()
def sync_to_employee_checkin_only():
    """
    Manual sync: ONLY convert Biometric Attendance Punch Table -> Employee Checkin,
    without calling any device.
    Used by 'Sync to Employee Checkin' button.
    """
    try:
        created, already_synced = sync_punches_to_employee_checkin()
        msg = (
            f"{created} Employee Checkins created from punches. "
            f"{already_synced} punches were already synced."
        )
        frappe.msgprint(msg)
        return msg
    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Error in sync_to_employee_checkin_only")
        frappe.throw(f"Error syncing to Employee Checkin: {str(e)}")


def scheduled_attendance_sync():
    """
    AUTO sync (every 15 minutes via hooks.py scheduler):

    - Set Biometric Integration Settings date range to last N days (default: 3)
    - Enqueue:
        * sync_attendance_device_only()      -> get logs from device(s)
        * sync_to_employee_checkin_only()    -> convert punches -> Employee Checkin
    """
    try:
        settings = frappe.get_doc("Biometric Integration Settings", "Biometric Integration Settings")

        BACK_DAYS = 3  # change to 5 if you prefer last 5 days

        today = now_datetime().date()
        start_date = today - timedelta(days=BACK_DAYS - 1)

        start_time = datetime.combine(
            start_date, datetime.strptime("00:00:00", "%H:%M:%S").time()
        )
        end_time = datetime.combine(
            today, datetime.strptime("23:59:59", "%H:%M:%S").time()
        )

        settings.start_date_and_time = start_time.strftime("%Y-%m-%d %H:%M:%S")
        settings.end_date_and_time = end_time.strftime("%Y-%m-%d %H:%M:%S")
        settings.save()

        # 1) Fetch from devices (logs + punches)
        frappe.enqueue(
            "biometric_integration.biometric_integration.doctype.biometric_integration_settings.biometric_integration_settings.sync_attendance_device_only",
            queue="long",
            timeout=1500,
        )

        # 2) Convert punches -> Employee Checkin
        frappe.enqueue(
            "biometric_integration.biometric_integration.doctype.biometric_integration_settings.biometric_integration_settings.sync_to_employee_checkin_only",
            queue="long",
            timeout=1500,
        )

        frappe.logger().info("Scheduled attendance sync (device + checkin) started successfully")

    except Exception as e:
        frappe.logger().error(f"Scheduled attendance sync failed: {str(e)}")
        frappe.log_error(
            f"Scheduled attendance sync failed: {str(e)}",
            "Scheduled Attendance Sync Error",
        )
