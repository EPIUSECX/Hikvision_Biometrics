# -*- coding: utf-8 -*-
"""
Overtime Calculator - Calculates overtime hours based on shift schedules
Uses HRMS standard functions
"""

import frappe
from frappe.utils import get_datetime
from hrms.hr.doctype.employee_checkin.employee_checkin import time_diff_in_hours

# HRMS standard imports
from hrms.hr.doctype.employee_checkin.employee_checkin import get_overtime_data
from hrms.hr.doctype.shift_assignment.shift_assignment import get_actual_start_end_datetime_of_shift


def calculate_overtime(employee, date, in_time, out_time):
    """
    Calculate overtime hours for an employee on a given date using HRMS function.

    Args:
        employee: Employee name
        date: Attendance date
        in_time: Check-in time
        out_time: Check-out time

    Returns:
        dict: {
            "overtime_hours": float,
            "regular_hours": float,
            "total_hours": float,
            "required_hours": float,
            "standard_working_hours": float,
            "actual_overtime_duration": float
        }
    """
    if not in_time or not out_time:
        return {
            "overtime_hours": 0,
            "regular_hours": 0,
            "total_hours": 0,
            "required_hours": 0,
            "standard_working_hours": 0,
            "actual_overtime_duration": 0,
        }

    total_hours = time_diff_in_hours(out_time, in_time)

    # Get shift information using HRMS function
    in_datetime = get_datetime(in_time)
    shift_info = get_actual_start_end_datetime_of_shift(
        employee=employee, for_timestamp=in_datetime, consider_default_shift=True
    )

    if not shift_info or not shift_info.get("shift_type"):
        # No shift assigned, cannot calculate overtime
        return {
            "overtime_hours": 0,
            "regular_hours": total_hours,
            "total_hours": total_hours,
            "required_hours": 0,
            "standard_working_hours": 0,
            "actual_overtime_duration": 0,
        }

    shift_name = shift_info.shift_type.name

    # Use HRMS get_overtime_data function
    overtime_data = get_overtime_data(shift_name, total_hours)

    standard_working_hours = overtime_data.get("standard_working_hours", 0)
    actual_overtime_duration = overtime_data.get("actual_overtime_duration", 0)

    # Calculate regular hours (total - overtime)
    regular_hours = total_hours - actual_overtime_duration

    return {
        "overtime_hours": actual_overtime_duration,
        "regular_hours": regular_hours,
        "total_hours": total_hours,
        "required_hours": standard_working_hours,
        "standard_working_hours": standard_working_hours,
        "actual_overtime_duration": actual_overtime_duration,
    }


def update_attendance_with_overtime(attendance_name, overtime_hours):
    """
    Update Attendance record with overtime hours.
    Note: HRMS handles overtime via get_overtime_data() in mark_attendance_and_link_log(),
    so this function may not be needed if using HRMS standard flow.

    Args:
        attendance_name: Attendance document name
        overtime_hours: Overtime hours to set
    """
    try:
        if not frappe.db.exists("Attendance", attendance_name):
            return

        # HRMS stores overtime in standard fields: overtime_type, standard_working_hours, actual_overtime_duration
        # These are set automatically by mark_attendance_and_link_log() if overtime_type is provided
        # This function is kept for backward compatibility but may not be needed
        frappe.logger().info(
            f"Overtime update requested for {attendance_name}, "
            "but HRMS handles overtime automatically via mark_attendance_and_link_log()"
        )

    except Exception as e:
        frappe.log_error(f"Error updating attendance with overtime: {str(e)}", "Overtime Calculator Error")


def calculate_overtime_for_date_range(employee, start_date, end_date):
    """
    Calculate total overtime for an employee over a date range.

    Args:
        employee: Employee name
        start_date: Start date
        end_date: End date

    Returns:
        dict: {
            "total_overtime": float,
            "total_regular_hours": float,
            "total_hours": float,
            "days_worked": int
        }
    """
    from datetime import timedelta

    total_overtime = 0
    total_regular_hours = 0
    total_hours = 0
    days_worked = 0

    current_date = start_date
    while current_date <= end_date:
        # Get attendance for the date
        attendance = frappe.db.get_value(
            "Attendance",
            {"employee": employee, "attendance_date": current_date, "status": "Present", "docstatus": 1},
            ["name", "check_in", "check_out"],
            as_dict=True,
        )

        if attendance and attendance.check_in and attendance.check_out:
            overtime_data = calculate_overtime(
                employee, current_date, attendance.check_in, attendance.check_out
            )

            # Use actual_overtime_duration from HRMS format
            total_overtime += overtime_data.get(
                "actual_overtime_duration", overtime_data.get("overtime_hours", 0)
            )
            total_regular_hours += overtime_data["regular_hours"]
            total_hours += overtime_data["total_hours"]
            days_worked += 1

        current_date += timedelta(days=1)

    return {
        "total_overtime": total_overtime,
        "total_regular_hours": total_regular_hours,
        "total_hours": total_hours,
        "days_worked": days_worked,
    }
