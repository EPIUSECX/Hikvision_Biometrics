import frappe
from frappe.utils import get_datetime
import requests

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


def sync_punches_to_employee_checkin():
    """
    Convert unsynced biometric punches into Employee Checkin records.

    Rules:
    - Group punches by (employee_no, event_date)
    - Map employee_no -> Employee via Employee.attendance_device_id
    - For each group (one employee, one date):
        * First punch of the day  -> Employee Checkin (IN)
        * Last punch of the day   -> Employee Checkin (OUT)
        * Middle punches stay only in the punch table, but are marked as synced
    - Avoid duplicate Employee Checkin rows (same employee + time)
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

    # Get all unsynced punches joined with their parent logs
    # Optimized query with better indexing hints
    punches = frappe.db.sql(
        """
        SELECT
            p.name AS punch_name,
            p.punch_time,
            p.punch_type,
            COALESCE(p.synced_to_employee_checkin, 0) AS synced,
            p.device_id AS punch_device_id,
            l.name AS log_name,
            l.employee_no,
            l.event_date,
            l.device_id AS log_device_id
        FROM `tabBiometric Attendance Punch Table` p
        INNER JOIN `tabBiometric Attendance Log` l ON l.name = p.parent
        WHERE COALESCE(p.synced_to_employee_checkin, 0) = 0
        ORDER BY l.employee_no, l.event_date, p.punch_time
        LIMIT 10000
        """,
        as_dict=True,
    )

    if not punches:
        return 0, 0

    created = 0
    already_synced = 0

    # Group punches by (employee_no, event_date)
    groups = {}
    for p in punches:
        key = (p["employee_no"], p["event_date"])
        groups.setdefault(key, []).append(p)

    for (emp_no, event_date), group_punches in groups.items():
        # If no device employee number, just skip this group
        if not emp_no:
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

        # Ensure sorted by time
        group_punches.sort(key=lambda x: x["punch_time"] or "")

        # Validate punch sequence
        from biometric_integration.biometric_integration.punch_validator import (
            validate_biometric_punch_table,
            flag_invalid_sequences_for_review,
        )

        # Convert to format for validator
        punch_table_rows = [
            type(
                "PunchRow", (), {"punch_time": p.get("punch_time"), "punch_type": p.get("punch_type", "Auto")}
            )()
            for p in group_punches
        ]

        validation = validate_biometric_punch_table(punch_table_rows)
        if not validation["valid"]:
            # Flag for review but continue processing
            flag_invalid_sequences_for_review(employee, event_date, [])
            if validation.get("errors"):
                frappe.log_error(
                    f"Invalid punch sequence for {employee} on {event_date}: {validation['errors']}",
                    "Biometric Punch Validation",
                )

        # Determine first and last punches
        # Handle different punch types: Auto, Break, Overtime
        # For checkin sync, we use first Auto punch as IN and last Auto punch as OUT
        auto_punches = [p for p in group_punches if p.get("punch_type") == "Auto"]
        break_punches = [p for p in group_punches if p.get("punch_type") == "Break"]

        # Use first and last auto punches for IN/OUT
        if auto_punches:
            first = auto_punches[0]
            last = auto_punches[-1]
        else:
            # Fallback to first and last of all punches
            first = group_punches[0]
            last = group_punches[-1]

        def _create_checkin_for_punch(punch, log_type):
            nonlocal created, already_synced

            time_str = f"{event_date} {punch['punch_time']}"
            punch_dt = get_datetime(time_str)

            # Avoid duplicate Employee Checkin rows for the same employee+time
            exists = frappe.db.exists(
                "Employee Checkin",
                {"employee": employee, "time": punch_dt},
            )
            if exists:
                already_synced += 1
                frappe.db.set_value(
                    "Biometric Attendance Punch Table",
                    punch["punch_name"],
                    "synced_to_employee_checkin",
                    1,
                )
                return

            checkin = frappe.new_doc("Employee Checkin")
            checkin.employee = employee
            checkin.time = punch_dt
            checkin.log_type = log_type  # 'IN' or 'OUT'

            # Set employee_name for HRMS compatibility
            if not checkin.employee_name:
                checkin.employee_name = frappe.db.get_value("Employee", employee, "employee_name")

            # Device ID (prefer punch device_id, fall back to log device_id)
            device_id = punch.get("punch_device_id") or punch.get("log_device_id")
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

            # Mark this punch as synced and link to Employee Checkin if possible
            update_values = {"synced_to_employee_checkin": 1}
            if punch_has_employee_checkin:
                update_values["employee_checkin"] = checkin.name

            frappe.db.set_value(
                "Biometric Attendance Punch Table",
                punch["punch_name"],
                update_values,
            )

            created += 1

        # Create IN checkin for first punch
        _create_checkin_for_punch(first, "IN")

        # If there is more than one punch, create OUT checkin for last punch
        if last["punch_name"] != first["punch_name"]:
            _create_checkin_for_punch(last, "OUT")

        # Mark middle punches as synced (but no Employee Checkin)
        middle = [
            p for p in group_punches if p["punch_name"] not in {first["punch_name"], last["punch_name"]}
        ]
        for p in middle:
            frappe.db.set_value(
                "Biometric Attendance Punch Table",
                p["punch_name"],
                "synced_to_employee_checkin",
                1,
            )

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
