import frappe
from frappe import _
from datetime import timedelta
from frappe.utils import nowdate


def execute(filters=None):
    columns = [
        {"fieldname": "employee_name", "label": _("Employee"), "fieldtype": "Data", "width": 200, "align": "left"},
        {"fieldname": "employee_id", "label": _("Device ID"), "fieldtype": "Data", "width": 90, "align": "center"},
        {"fieldname": "review_status", "label": _("Review Status"), "fieldtype": "Data", "width": 120},
        {"fieldname": "total_duration", "label": _("Worked"), "fieldtype": "Data", "width": 80, "align": "center"}
    ]

    if not filters:
        filters = {}

    if not filters.get("date"):
        filters["date"] = nowdate()

    selected_date = filters.get("date")
    # Fetch all active employees with attendance_device_id
    all_active_employees = frappe.db.sql(
        """
        SELECT employee_name, attendance_device_id
        FROM tabEmployee
        WHERE status = 'Active'
          AND attendance_device_id IS NOT NULL
          AND attendance_device_id != ''
        """,
        as_dict=True,
    )

    # Fetch present employees
    present_employees = frappe.db.sql(
        """
        SELECT DISTINCT
            bal.employee_no,
            e.employee_name,
            e.attendance_device_id
        FROM `tabBiometric Attendance Log` bal
        LEFT JOIN `tabEmployee` e ON e.attendance_device_id = bal.employee_no
        WHERE bal.event_date = %(selected_date)s
        """,
        {"selected_date": selected_date},
        as_dict=True,
    )

    present_employee_ids = {emp.attendance_device_id for emp in present_employees}

    # ⚠️ FIXED — safe natural sorting that never crashes
    def natural_sort_key(emp):
        """
        Sort employees by attendance_device_id numerically.
        If missing or non-numeric, treat as 0.
        """
        value = emp.get("attendance_device_id")

        if not value:
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    # Sort
    present_employees.sort(key=natural_sort_key)

    data = []
    max_punches = 0
    valid_minutes = []

    # Process each present employee
    for employee in present_employees:
        attendance_logs = frappe.db.sql(
            """
            SELECT al.name, al.event_date
            FROM `tabBiometric Attendance Log` al
            WHERE al.employee_no = %(emp)s
              AND al.event_date = %(date)s
            ORDER BY al.event_date
            """,
            {"emp": employee.attendance_device_id, "date": selected_date},
            as_dict=True,
        )

        for log in attendance_logs:
            punches = frappe.db.sql(
                """
                SELECT
                    at.punch_time,
                    CASE
                        WHEN SUM(at.punch_type = 'Manual') > 0 THEN 'Manual'
                        ELSE 'Auto'
                    END AS punch_type
                FROM `tabBiometric Attendance Punch Table` at
                WHERE at.parent = %(log)s
                GROUP BY at.punch_time
                ORDER BY at.punch_time
                """,
                {"log": log.name},
                as_dict=True,
            )

            row_data = {
                "employee_name": employee.employee_name,
                "employee_id": employee.attendance_device_id,
            }
            row_indicators = {}

            # Calculate durations
            if len(punches) % 2 != 0:
                total_duration = _("Incomplete")
                review_status = _("Needs OUT punch")
            else:
                total_minutes = calculate_total_minutes(punches)
                total_duration = format_minutes_to_hhmm(total_minutes)
                review_status = _("Complete")
                valid_minutes.append(total_minutes)

            row_data["total_duration"] = total_duration
            row_data["review_status"] = review_status

            # Add punches
            for i, punch in enumerate(punches, 1):
                field = f"punch_{i}"
                formatted = format_punch_with_type(punch)
                row_data[field] = formatted

                # Color punch if manual
                if punch.get("punch_type") == "Manual":
                    row_indicators[field] = "red"

            max_punches = max(max_punches, len(punches))
            data.append({"data": row_data, "indicators": row_indicators})

    # Generate punch columns
    for i in range(1, max_punches + 1):
        columns.append({
            "fieldname": f"punch_{i}",
            "label": _("Punch ") + str(i),
            "fieldtype": "Data",
            "width": 95,
            "align": "center",
        })

    # Build final report data
    formatted_data = []

    for row in data:
        row_data = row["data"].copy()

        # Apply color indicators
        for field, color in row["indicators"].items():
            row_data[field] = frappe.render_template(
                '<span style="color: {{ color }}">{{ value }}</span>',
                {"value": row_data[field], "color": color},
            )

        # Fill missing punches with None
        for i in range(1, max_punches + 1):
            row_data.setdefault(f"punch_{i}", None)

        formatted_data.append(row_data)

    # Total row
    total_minutes = sum(valid_minutes)
    formatted_data.append({
        "employee_name": "Total",
        "employee_id": len(present_employees),
        "review_status": _("{0} present").format(len(present_employees)),
        "total_duration": format_minutes_to_hhmm(total_minutes),
        **{f"punch_{i}": None for i in range(1, max_punches + 1)},
    })

    # Absent employees
    absent_employees = [
        emp for emp in all_active_employees if emp.attendance_device_id not in present_employee_ids
    ]
    absent_employees.sort(key=natural_sort_key)

    for emp in absent_employees:
        formatted_data.append({
            "employee_name": emp.employee_name,
            "employee_id": emp.attendance_device_id,
            "review_status": _("No device events"),
            "total_duration": None,
            **{f"punch_{i}": None for i in range(1, max_punches + 1)},
        })

    return columns, formatted_data


# ----------------------------------------------------------------------
# Helper Functions
# ----------------------------------------------------------------------

def calculate_total_minutes(punches):
    total = 0
    for i in range(0, len(punches) - 1, 2):
        try:
            t_in = punches[i]["punch_time"]
            t_out = punches[i + 1]["punch_time"]
            diff = int(t_out.total_seconds() / 60) - int(t_in.total_seconds() / 60)
            total += diff
        except Exception:
            return 0
    return total


def format_minutes_to_hhmm(minutes):
    h, m = divmod(minutes, 60)
    return f"{h:02}:{m:02}"


def format_timedelta_to_hhmm(td):
    if not td:
        return None
    total = int(td.total_seconds() / 60)
    h, m = divmod(total, 60)
    return f"{h:02}:{m:02}"


def format_punch_with_type(punch):
    if not punch.get("punch_time"):
        return None

    time_str = format_timedelta_to_hhmm(punch["punch_time"])
    if punch.get("punch_type") == "Manual":
        return f"{time_str} (MA)"

    return time_str
