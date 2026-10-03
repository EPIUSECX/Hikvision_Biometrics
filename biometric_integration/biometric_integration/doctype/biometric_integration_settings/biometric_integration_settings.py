import re
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from zoneinfo import ZoneInfo

import frappe
import requests
from frappe.model.document import Document
from frappe.utils import get_datetime, get_system_timezone, now_datetime
from requests.auth import HTTPDigestAuth

from biometric_integration.employee_checkin_sync import (
    _punch_time_key,
    sync_punches_to_employee_checkin,
)


class BiometricIntegrationSettings(Document):
    def validate(self):
        self.ip = _normalize_device_host(getattr(self, "ip", ""))
        _get_device_timezone(getattr(self, "timezone", None))

        for device in self.devices or []:
            device.ip_address = _normalize_device_host(device.ip_address)
            username = device.username or self.username
            password = device.get_password("password")
            if device.is_active and (not device.ip_address or not username or not password):
                frappe.throw(
                    f"Device row #{device.idx}: IP, Username and Password are required for active devices."
                )


def _normalize_device_host(raw_ip):
    host = (raw_ip or "").strip()
    host = re.sub(r"^https?://", "", host, flags=re.IGNORECASE)
    return host.split("/")[0].strip()


def _get_device_timezone(configured_offset=None):
    if not configured_offset:
        return ZoneInfo(get_system_timezone())

    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", configured_offset.strip())
    if not match:
        frappe.throw("Timezone must be an offset such as +02:00, or left blank to use the site timezone.")

    sign = 1 if match.group(1) == "+" else -1
    offset = timedelta(hours=int(match.group(2)), minutes=int(match.group(3))) * sign
    return datetime_timezone(offset)


def _format_device_datetime(value, configured_offset=None):
    local_datetime = get_datetime(value)
    return local_datetime.replace(tzinfo=_get_device_timezone(configured_offset)).isoformat(
        timespec="seconds"
    )


def _get_device_configs(settings):
    """Return a list of (label, ip, username, password) for all active devices.

    Priority:
    - If child table has active rows -> use those
    - Else -> use main IP/username/password from settings
    """
    devices = []

    # Child table devices
    if getattr(settings, "devices", None):
        for d in settings.devices:
            # Expecting fields: device, ip_address, username, password, is_active
            if getattr(d, "is_active", 0):
                ip = _normalize_device_host(d.ip_address)
                username = d.username or settings.username
                pwd = d.get_password("password") if hasattr(d, "get_password") else d.password
                if ip and username and pwd:
                    devices.append((d.device or ip, ip, username, pwd))

    # Fallback to main IP if no active child devices
    if not devices and settings.ip:
        ip = _normalize_device_host(settings.ip)
        pwd = settings.get_password("password")
        if ip and settings.username and pwd:
            devices.append(("Main Device", ip, settings.username, pwd))

    return devices


@frappe.whitelist()
def test_connection():
    settings = frappe.get_doc("Biometric Integration Settings", "Biometric Integration Settings")
    device_configs = _get_device_configs(settings)
    if not device_configs:
        frappe.throw("No active device is configured with complete credentials.")

    start_time = _format_device_datetime("2000-01-01 00:00:00", settings.timezone)
    end_time = _format_device_datetime("2000-01-02 00:00:00", settings.timezone)
    results = []

    for label, ip, username, password in device_configs:
        response = requests.post(
            f"http://{ip}/ISAPI/AccessControl/AcsEvent?format=json",
            auth=HTTPDigestAuth(username, password),
            headers={"Content-Type": "application/json"},
            json={
                "AcsEventCond": {
                    "searchID": "connection-test",
                    "searchResultPosition": 0,
                    "maxResults": 1,
                    "major": 5,
                    "minor": 75,
                    "startTime": start_time,
                    "endTime": end_time,
                }
            },
            verify=False,
            timeout=10,
        )
        status = "success" if response.status_code == 200 else "error"
        message = (
            "Connected (HTTP 200)"
            if response.status_code == 200
            else f"Unexpected response: HTTP {response.status_code}"
        )
        results.append({"label": label, "ip": ip, "status": status, "message": message})

    return results


def _sync_for_single_device(settings, label, ip, username, password, start_time, end_time):
    """
    Sync attendance for a single device (one IP).
    Returns (count, skipped) for that device.
    Also sets device_id (IP) on logs and punches if those fields exist.
    """
    url = f"http://{ip}/ISAPI/AccessControl/AcsEvent?format=json"
    headers = {"Content-Type": "application/json"}

    log_has_device_id = frappe.db.has_column("Biometric Attendance Log", "device_id")
    punch_has_device_id = frappe.db.has_column("Biometric Attendance Punch Table", "device_id")

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

    # Use retry logic for device connection
    from biometric_integration.biometric_integration.error_handler import (
        handle_device_connection_error,
        retry_with_backoff,
    )

    def fetch_device_data():
        return requests.post(
            url,
            auth=HTTPDigestAuth(username, password),
            headers=headers,
            json=payload,
            verify=False,
            timeout=600,
        )

    try:
        response = retry_with_backoff(fetch_device_data, max_retries=3, initial_delay=2)
    except Exception as e:
        handle_device_connection_error(ip, e, {"label": label, "operation": "initial_fetch"})
        raise

    if response.status_code != 200:
        error_msg = f"[{label}] Failed to fetch attendance logs. Status: {response.status_code}, Response: {response.text}"
        handle_device_connection_error(
            ip, Exception(error_msg), {"label": label, "status_code": response.status_code}
        )
        frappe.throw(error_msg)

    data = response.json()
    total_records = data.get("AcsEvent", {}).get("totalMatches", 0)

    if total_records == 0:
        return 0, 0

    if total_records > 1500:
        frappe.throw(f"[{label}] Too many records to process ({total_records}). Reduce date range.")

    count = 0
    skipped = 0
    position = 0
    batch_size = 100  # Increased from 30 to 100 for better performance

    while True:
        payload["AcsEventCond"]["searchResultPosition"] = position
        payload["AcsEventCond"]["maxResults"] = batch_size

        try:
            response = retry_with_backoff(fetch_device_data, max_retries=2, initial_delay=1)
        except Exception as e:
            handle_device_connection_error(
                ip, e, {"label": label, "operation": "batch_fetch", "position": position}
            )
            # Continue with next batch instead of failing completely
            position += batch_size
            continue

        if response.status_code != 200:
            error_msg = f"[{label}] Failed to fetch attendance logs. Status: {response.status_code}, Response: {response.text}"
            handle_device_connection_error(
                ip, Exception(error_msg), {"label": label, "status_code": response.status_code}
            )
            # Continue with next batch
            position += batch_size
            continue

        data = response.json()
        events = data.get("AcsEvent", {}).get("InfoList", [])

        if not events:
            break

        # Batch process events for better performance
        events_to_process = []
        for log in events:
            emp_no = log.get("employeeNoString")
            event_timestamp = log.get("time", "")
            if not emp_no or not event_timestamp:
                continue

            # Convert device time format to Frappe format
            try:
                event_datetime = datetime.strptime(event_timestamp[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                continue

            # Determine punch type from event (if available)
            # Hikvision events may have different types: CheckIn, CheckOut, BreakIn, BreakOut, etc.
            event_type = log.get("eventType") or log.get("event_type") or "CheckIn"
            punch_type_map = {
                "CheckIn": "Auto",
                "CheckOut": "Auto",
                "BreakIn": "Break",
                "BreakOut": "Break",
                "OvertimeIn": "Overtime",
                "OvertimeOut": "Overtime",
            }
            punch_type = punch_type_map.get(event_type, "Auto")

            events_to_process.append(
                {"emp_no": emp_no, "event_datetime": event_datetime, "punch_type": punch_type, "log": log}
            )

        # Batch fetch existing logs
        if events_to_process:
            emp_dates = {(e["emp_no"], e["event_datetime"].date()) for e in events_to_process}
            existing_logs = {}

            for emp_no, event_date in emp_dates:
                attendance_log = frappe.get_all(
                    "Biometric Attendance Log",
                    filters={"employee_no": emp_no, "event_date": event_date},
                    limit_page_length=1,
                )
                if attendance_log:
                    existing_logs[(emp_no, event_date)] = attendance_log[0].name

            # Process events in batches
            docs_to_save = {}
            for event_data in events_to_process:
                emp_no = event_data["emp_no"]
                event_date = event_data["event_datetime"].date()
                event_time = event_data["event_datetime"].time()
                punch_type = event_data["punch_type"]

                # Get or create log doc
                log_key = (emp_no, event_date)
                if log_key not in docs_to_save:
                    if log_key in existing_logs:
                        doc = frappe.get_doc("Biometric Attendance Log", existing_logs[log_key])
                    else:
                        doc = frappe.new_doc("Biometric Attendance Log")
                        doc.employee_no = emp_no
                        doc.event_date = event_date
                        if log_has_device_id:
                            doc.device_id = ip
                    docs_to_save[log_key] = {"doc": doc, "new_punches": 0}

                entry = docs_to_save[log_key]
                doc = entry["doc"]

                # Check for duplicate punch time
                existing_punch = any(
                    _punch_time_key(p.punch_time) == _punch_time_key(event_time)
                    for p in doc.punch_table
                    if getattr(p, "punch_time", None) is not None
                )

                if not existing_punch:
                    punch_row = {
                        "punch_time": event_time,
                        "punch_type": punch_type,
                    }
                    if punch_has_device_id:
                        punch_row["device_id"] = ip

                    doc.append("punch_table", punch_row)
                    entry["new_punches"] += 1
                else:
                    skipped += 1

            # Bulk save all documents
            for entry in docs_to_save.values():
                doc = entry["doc"]
                try:
                    doc.save(ignore_permissions=True)
                    count += entry["new_punches"]
                except Exception:
                    frappe.log_error(
                        frappe.get_traceback(),
                        f"[{label}] Insert failed for employee {doc.employee_no}",
                    )
                    continue

        position += len(events)

        if len(events) < batch_size:
            break

    return count, skipped


@frappe.whitelist()
def sync_attendance():
    """
    Manual sync from device(s) AND directly to Employee Checkin.
    This is used by the button "Sync Attendance (Device + Checkin)".
    """
    settings = frappe.get_doc("Biometric Integration Settings", "Biometric Integration Settings")
    msg_parts = []

    # First: sync from devices into logs/punches
    device_msg = sync_attendance_device_only()
    msg_parts.append(device_msg)

    # Then: convert punches -> Employee Checkin
    created, already_synced = sync_punches_to_employee_checkin()
    msg_parts.append(f"{created} Employee Checkins created, {already_synced} punches were already synced.")

    if settings.get("auto_create_attendance", 0):
        try:
            from biometric_integration.biometric_integration.attendance_processor import (
                create_attendance_from_checkins,
            )

            start_date = get_datetime(settings.start_date_and_time).date()
            end_date = get_datetime(settings.end_date_and_time).date()
            att_created = 0
            att_skipped = 0
            att_errors = 0

            current_date = start_date
            while current_date <= end_date:
                created_count, skipped_count, error_count = create_attendance_from_checkins(current_date)
                att_created += created_count
                att_skipped += skipped_count
                att_errors += error_count
                current_date += timedelta(days=1)

            if att_created > 0:
                msg_parts.append(
                    f"{att_created} Attendance records created, {att_skipped} skipped, {att_errors} errors."
                )
        except ImportError:
            pass

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
    configured_offset = getattr(settings, "timezone", None)
    start_time = _format_device_datetime(settings.start_date_and_time, configured_offset)
    end_time = _format_device_datetime(settings.end_date_and_time, configured_offset)

    device_configs = _get_device_configs(settings)
    if not device_configs:
        frappe.throw("No device configured (no IP in settings and no active rows in Devices table).")

    total_count = 0
    total_skipped = 0

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

    # Save all logs/punches
    frappe.db.commit()

    msg = f"{total_count} attendance records synced from devices; {total_skipped} duplicate punches skipped."

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
            f"{created} Employee Checkins created from punches. {already_synced} punches were already synced."
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

        start_time = datetime.combine(start_date, datetime.strptime("00:00:00", "%H:%M:%S").time())
        end_time = datetime.combine(today, datetime.strptime("23:59:59", "%H:%M:%S").time())

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
