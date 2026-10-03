from datetime import time, timedelta

import frappe
import requests
from frappe.utils import get_datetime

# -------------------------------
# Geolocation helpers
# -------------------------------


def get_geolocation(device_ip=None, device_name=None):
    """
    Get geolocation for device using a priority-based approach.

    Priority order:
    1. Device-specific location (from Biometric Device doctype if available)
    2. Company address location (from Company doctype)
    3. Default location from Biometric Integration Settings
    4. Server IP geolocation (via external API)
    5. None (if geolocation tracking is disabled or unavailable)

    Args:
        device_ip: Device IP address (optional)
        device_name: Device name/identifier (optional, for lookup in Biometric Device)

    Returns:
        tuple: (latitude, longitude) or (None, None) if not available/required
    """
    # Check if geolocation tracking is enabled in HR Settings
    # If disabled, return None (HRMS doesn't require coordinates)
    try:
        allow_geolocation = frappe.db.get_single_value("HR Settings", "allow_geolocation_tracking")
        if not allow_geolocation:
            return None, None
    except Exception:
        # If HR Settings doesn't exist or field is missing, assume disabled
        return None, None

    # Priority 1: Try to get device-specific location from Biometric Device
    if device_name or device_ip:
        try:
            device_filters = {}
            if device_name:
                device_filters["device"] = device_name
            elif device_ip:
                device_filters["ip_address"] = device_ip

            # Check if Biometric Device has latitude/longitude fields
            if frappe.db.has_column("Biometric Device", "latitude") and frappe.db.has_column(
                "Biometric Device", "longitude"
            ):
                device_coords = frappe.db.get_value(
                    "Biometric Device", device_filters, ["latitude", "longitude"], as_dict=True
                )
                if device_coords and device_coords.latitude and device_coords.longitude:
                    return float(device_coords.latitude), float(device_coords.longitude)
        except Exception:
            pass

    # Priority 2: Try to get Company address location
    try:
        company = frappe.db.get_single_value("Global Defaults", "default_company")
        if company:
            # Check if Company has latitude/longitude fields (custom fields)
            if frappe.db.has_column("Company", "latitude") and frappe.db.has_column("Company", "longitude"):
                company_coords = frappe.db.get_value(
                    "Company", company, ["latitude", "longitude"], as_dict=True
                )
                if company_coords and company_coords.latitude and company_coords.longitude:
                    return float(company_coords.latitude), float(company_coords.longitude)
    except Exception:
        pass

    # Priority 3: Get default location from Biometric Integration Settings
    try:
        settings = frappe.get_single("Biometric Integration Settings")
        if hasattr(settings, "default_latitude") and hasattr(settings, "default_longitude"):
            if settings.default_latitude and settings.default_longitude:
                return float(settings.default_latitude), float(settings.default_longitude)
    except Exception:
        pass

    # Priority 4: Fallback to server IP geolocation (external API)
    try:
        r = requests.get("https://ipinfo.io/json", timeout=5)
        if r.status_code == 200:
            data = r.json()
            loc = data.get("loc")
            if loc:
                lat_str, lon_str = loc.split(",")
                return float(lat_str), float(lon_str)
    except Exception:
        pass

    # Priority 5: Return None if nothing is available
    # This is acceptable if geolocation tracking is optional or not configured
    return None, None


def _punch_time_key(value):
    """Return a stable, sortable microsecond key for Frappe Time values."""
    if value is None:
        return None

    if isinstance(value, timedelta):
        return round(value.total_seconds() * 1_000_000)

    if isinstance(value, time):
        return ((value.hour * 60 + value.minute) * 60 + value.second) * 1_000_000 + value.microsecond

    parsed = get_datetime(f"2000-01-01 {value}")
    return ((parsed.hour * 60 + parsed.minute) * 60 + parsed.second) * 1_000_000 + parsed.microsecond


def _group_punches_by_time(punches):
    """Group duplicate device events while preserving chronological order."""
    groups = {}
    for punch in punches:
        key = _punch_time_key(punch.get("punch_time"))
        if key is None:
            continue
        groups.setdefault(key, []).append(punch)

    return [groups[key] for key in sorted(groups)]


def sync_punches_to_employee_checkin(commit=True):
    """
    Convert unsynced biometric punches into Employee Checkin records.

    Rules:
    - Group punches by (employee_no, event_date)
    - Map employee_no -> Employee via Employee.attendance_device_id
    - Convert every unique punch time into an alternating IN/OUT sequence
      (IN, OUT, IN, OUT, ...), so employees may leave and return repeatedly
      during the same day.
    - Collapse duplicate device events at the same timestamp into one check-in.
    - Reconcile punches that older versions marked synced without creating a
      linked Employee Checkin.
    - Avoid duplicate Employee Checkin rows (same employee + time).
    - Mark punches as synced via Biometric Attendance Punch Table.synced_to_employee_checkin
    - Copy device_id (device IP) from punches/logs into Employee Checkin.device_id
    - When HRMS geolocation is enabled, fill latitude/longitude fields so validation passes.

    SIMPLE RULE for missing/inactive employees:
    - If no Employee is found for attendance_device_id, or Employee is NOT Active:
        -> Do NOT create Employee Checkin
        -> Do NOT mark punches as synced
        -> Just skip silently (punches remain in biometric tables only)
    """

    # Check optional columns exist (DB-level)
    punch_has_employee_checkin = frappe.db.has_column("Biometric Attendance Punch Table", "employee_checkin")
    punch_has_device_id = frappe.db.has_column("Biometric Attendance Punch Table", "device_id")
    log_has_device_id = frappe.db.has_column("Biometric Attendance Log", "device_id")
    checkin_has_biometric_log = frappe.db.has_column("Employee Checkin", "biometric_log")
    checkin_has_biometric_punch = frappe.db.has_column("Employee Checkin", "biometric_punch")
    checkin_has_device_id = frappe.db.has_column("Employee Checkin", "device_id")

    # Geolocation-related fields
    checkin_has_latitude = frappe.db.has_column("Employee Checkin", "latitude")
    checkin_has_longitude = frappe.db.has_column("Employee Checkin", "longitude")
    checkin_has_geo_latitude = frappe.db.has_column("Employee Checkin", "geo_latitude")
    checkin_has_geo_longitude = frappe.db.has_column("Employee Checkin", "geo_longitude")

    # Check if geolocation tracking is enabled (will be checked per device in _create_checkin_for_punch)
    # We don't fetch default geolocation here - it will be fetched per device when needed

    if punch_has_employee_checkin:
        employee_checkin_join = "LEFT JOIN `tabEmployee Checkin` ec ON ec.name = p.employee_checkin"
        reconciliation_filter = (
            "COALESCE(p.synced_to_employee_checkin, 0) = 0 OR p.employee_checkin IS NULL OR ec.name IS NULL"
        )
    else:
        employee_checkin_join = ""
        reconciliation_filter = "COALESCE(p.synced_to_employee_checkin, 0) = 0"

    # Select only employee/date groups needing work, then load the complete day
    # for each group so incremental runs continue the correct IN/OUT sequence.
    pending_groups = frappe.db.sql(
        f"""
        SELECT DISTINCT l.employee_no, l.event_date
        FROM `tabBiometric Attendance Punch Table` p
        INNER JOIN `tabBiometric Attendance Log` l ON l.name = p.parent
        {employee_checkin_join}
        WHERE {reconciliation_filter}
        ORDER BY l.event_date, l.employee_no
        LIMIT 1000
        """,
        as_dict=True,
    )

    if not pending_groups:
        return 0, 0

    created = 0
    already_synced = 0

    employee_checkin_select = (
        "p.employee_checkin" if punch_has_employee_checkin else "NULL AS employee_checkin"
    )
    punch_device_select = "p.device_id" if punch_has_device_id else "NULL AS punch_device_id"
    if punch_has_device_id:
        punch_device_select += " AS punch_device_id"
    log_device_select = "l.device_id" if log_has_device_id else "NULL AS log_device_id"
    if log_has_device_id:
        log_device_select += " AS log_device_id"

    for pending_group in pending_groups:
        emp_no = pending_group.employee_no
        event_date = pending_group.event_date

        # If no device employee number, just skip this group
        if not emp_no:
            continue

        group_punches = frappe.db.sql(
            f"""
            SELECT
                p.name AS punch_name,
                p.punch_time,
                p.punch_type,
                COALESCE(p.synced_to_employee_checkin, 0) AS synced,
                {employee_checkin_select},
                {punch_device_select},
                l.name AS log_name,
                {log_device_select}
            FROM `tabBiometric Attendance Punch Table` p
            INNER JOIN `tabBiometric Attendance Log` l ON l.name = p.parent
            WHERE l.employee_no = %s AND l.event_date = %s
            ORDER BY p.punch_time, p.creation, p.name
            """,
            (emp_no, event_date),
            as_dict=True,
        )

        unique_punch_groups = _group_punches_by_time(group_punches)
        if not unique_punch_groups:
            continue

        # Map device employee_no -> Employee via attendance_device_id
        emp_row = frappe.db.get_value(
            "Employee",
            {"attendance_device_id": emp_no},
            ["name", "status", "date_of_joining", "relieving_date"],
            as_dict=True,
        )

        # If no Employee found OR Employee is not Active → skip silently
        if not emp_row:
            continue

        # Validate employee status using HRMS function
        try:
            from hrms.hr.utils import validate_active_employee

            # Validate that employee is active (HRMS function throws exception if not)
            validate_active_employee(emp_row.name)
        except Exception:
            # Employee is not active, skip
            continue

        # Additional validation: check if employee was active during punch period
        if not _is_employee_active_for_date(emp_row, event_date):
            # Employee was not active during punch period
            continue

        employee = emp_row.name

        def _bucket_needs_reconciliation(punch_group):
            for punch in punch_group:
                if not punch.get("synced"):
                    return True
                if punch_has_employee_checkin:
                    linked_checkin = punch.get("employee_checkin")
                    if not linked_checkin or not frappe.db.exists("Employee Checkin", linked_checkin):
                        return True
            return False

        def _mark_punch_group_synced(punch_group, employee_checkin):
            update_values = {"synced_to_employee_checkin": 1}
            if punch_has_employee_checkin:
                update_values["employee_checkin"] = employee_checkin
            for punch in punch_group:
                frappe.db.set_value(
                    "Biometric Attendance Punch Table",
                    punch["punch_name"],
                    update_values,
                )

        for index, punch_group in enumerate(unique_punch_groups):
            if not _bucket_needs_reconciliation(punch_group):
                continue

            punch = punch_group[0]
            log_type = "IN" if index % 2 == 0 else "OUT"
            punch_dt = get_datetime(f"{event_date} {punch['punch_time']}")

            # Avoid duplicate Employee Checkin rows for the same employee+time.
            existing_checkin = frappe.db.get_value(
                "Employee Checkin",
                {"employee": employee, "time": punch_dt},
                ["name", "log_type", "attendance"],
                as_dict=True,
            )
            if existing_checkin:
                if existing_checkin.log_type != log_type:
                    if existing_checkin.attendance:
                        frappe.log_error(
                            f"Checkin {existing_checkin.name} is linked to attendance "
                            f"{existing_checkin.attendance} with log type "
                            f"{existing_checkin.log_type}; expected {log_type}.",
                            "Biometric Checkin Sequence Mismatch",
                        )
                    else:
                        frappe.db.set_value("Employee Checkin", existing_checkin.name, "log_type", log_type)

                _mark_punch_group_synced(punch_group, existing_checkin.name)
                already_synced += 1
                continue

            checkin = frappe.new_doc("Employee Checkin")
            checkin.employee = employee
            checkin.time = punch_dt
            checkin.log_type = log_type  # 'IN' or 'OUT'

            # Set employee_name for HRMS compatibility
            if not checkin.employee_name:
                checkin.employee_name = frappe.db.get_value("Employee", employee, "employee_name")

            # Device ID (prefer punch device_id, fall back to log device_id)
            device_id = next(
                (
                    row.get("punch_device_id") or row.get("log_device_id")
                    for row in punch_group
                    if row.get("punch_device_id") or row.get("log_device_id")
                ),
                None,
            )
            if checkin_has_device_id and device_id:
                checkin.device_id = device_id

            # Get geolocation for this specific device if available
            # This function checks if geolocation tracking is enabled and returns None if disabled
            device_lat, device_lon = get_geolocation(device_id, device_id)

            # Geolocation fields: only set if geolocation tracking is enabled and coordinates are available
            # HRMS will validate these only if allow_geolocation_tracking is enabled
            if device_lat is not None and device_lon is not None:
                if checkin_has_latitude:
                    checkin.latitude = device_lat
                if checkin_has_longitude:
                    checkin.longitude = device_lon
                if checkin_has_geo_latitude:
                    checkin.geo_latitude = device_lat
                if checkin_has_geo_longitude:
                    checkin.geo_longitude = device_lon
            # If coordinates are None, leave fields empty (HRMS allows this if geolocation tracking is disabled)

            # Optional back-links to biometric log/punch if those fields exist
            if checkin_has_biometric_log:
                checkin.biometric_log = punch["log_name"]
            if checkin_has_biometric_punch:
                checkin.biometric_punch = punch["punch_name"]

            checkin.insert(ignore_permissions=True)

            # Duplicate device events at this timestamp all point to the same check-in.
            _mark_punch_group_synced(punch_group, checkin.name)
            created += 1

    if commit:
        frappe.db.commit()
    return created, already_synced


def _is_employee_active_for_date(emp_row, date):
    """
    Check if employee was active on the given date.
    Validates status, date of joining, and relieving date.

    Args:
        emp_row: Employee row dict with name, status, date_of_joining, relieving_date
        date: Date to check

    Returns:
        bool: True if employee was active on the date
    """
    # Check current status
    if emp_row.status != "Active":
        return False

    # Check date of joining
    if emp_row.date_of_joining and emp_row.date_of_joining > date:
        return False

    # Check relieving date
    if emp_row.relieving_date and emp_row.relieving_date < date:
        return False

    return True
