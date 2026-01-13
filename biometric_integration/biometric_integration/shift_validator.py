# -*- coding: utf-8 -*-
"""
Shift Validator - Validates attendance against shift schedules
Uses HRMS standard functions
"""
import frappe
from frappe.utils import getdate, get_datetime
from hrms.hr.doctype.employee_checkin.employee_checkin import time_diff_in_hours
from datetime import datetime, timedelta

# HRMS standard imports
from hrms.hr.doctype.shift_assignment.shift_assignment import (
    get_actual_start_end_datetime_of_shift,
    get_employee_shift,
)


def get_shift_for_employee(employee, date):
    """
    Get shift assignment for an employee on a given date using HRMS function.
    
    Args:
        employee: Employee name
        date: Date to check
    
    Returns:
        dict: Shift details or None (HRMS format)
    """
    # Convert date to datetime for HRMS function
    if isinstance(date, str):
        date = getdate(date)
    
    # Use noon time to avoid midnight shift issues
    for_timestamp = datetime.combine(date, datetime.min.time().replace(hour=12))
    
    # Use HRMS function to get shift details
    shift_info = get_actual_start_end_datetime_of_shift(
        employee=employee,
        for_timestamp=for_timestamp,
        consider_default_shift=True
    )
    
    if shift_info and shift_info.get("shift_type"):
        return shift_info
    
    return None


def get_shift_type_details(shift_type):
    """
    Get details of a shift type using HRMS function.
    
    Args:
        shift_type: Shift Type name
    
    Returns:
        dict: Shift details in HRMS format
    """
    if not frappe.db.exists("Shift Type", shift_type):
        return None
    
    # Use HRMS get_employee_shift or get shift details directly
    # For a generic shift type, we need to get shift details
    from hrms.hr.doctype.shift_assignment.shift_assignment import get_shift_details
    
    shift_details = get_shift_details(shift_type, for_timestamp=datetime.now())
    
    if shift_details:
        return {
            "name": shift_type,
            "shift_type": shift_details.get("shift_type"),
            "start_datetime": shift_details.get("start_datetime"),
            "end_datetime": shift_details.get("end_datetime"),
            "actual_start": shift_details.get("actual_start"),
            "actual_end": shift_details.get("actual_end"),
        }
    
    return None


def validate_shift_timing(employee, date, in_time, out_time=None):
    """
    Validate check-in and check-out times against shift schedule using HRMS functions.
    
    Args:
        employee: Employee name
        date: Attendance date
        in_time: Check-in time
        out_time: Check-out time (optional)
    
    Returns:
        dict: {
            "valid": bool,
            "late_arrival": bool,
            "early_departure": bool,
            "late_minutes": int,
            "early_minutes": int,
            "working_hours": float,
            "required_hours": float,
            "shift_details": dict
        }
    """
    # Use HRMS function to get shift details
    in_datetime = get_datetime(in_time) if in_time else None
    if not in_datetime:
        # Use date at noon if no in_time provided
        in_datetime = datetime.combine(getdate(date), datetime.min.time().replace(hour=12))
    
    shift_info = get_actual_start_end_datetime_of_shift(
        employee=employee,
        for_timestamp=in_datetime,
        consider_default_shift=True
    )
    
    if not shift_info or not shift_info.get("shift_type"):
        # No shift assigned, cannot validate
        return {
            "valid": True,
            "late_arrival": False,
            "early_departure": False,
            "late_minutes": 0,
            "early_minutes": 0,
            "working_hours": 0,
            "required_hours": 0,
            "shift_details": None
        }
    
    result = {
        "valid": True,
        "late_arrival": False,
        "early_departure": False,
        "late_minutes": 0,
        "early_minutes": 0,
        "working_hours": 0,
        "required_hours": 0,
        "shift_details": shift_info
    }
    
    # Get required hours from shift type
    shift_type = shift_info.get("shift_type")
    if shift_type:
        from hrms.hr.doctype.employee_checkin.employee_checkin import calculate_time_difference
        required_hours = calculate_time_difference(
            shift_type.start_time,
            shift_type.end_time
        )
        result["required_hours"] = required_hours
    
    # Use actual_start and actual_end from HRMS (includes grace periods)
    shift_start = shift_info.get("actual_start")
    shift_end = shift_info.get("actual_end") or shift_info.get("end_datetime")
    
    # Check late arrival (compare against start_datetime, not actual_start)
    start_datetime = shift_info.get("start_datetime")
    if in_datetime and start_datetime:
        if in_datetime > start_datetime:
            late_delta = in_datetime - start_datetime
            result["late_minutes"] = int(late_delta.total_seconds() / 60)
            result["late_arrival"] = True
            result["valid"] = False
    
    # Check early departure (compare against end_datetime, not actual_end)
    if out_time:
        out_datetime = get_datetime(out_time)
        end_datetime = shift_info.get("end_datetime")
        if end_datetime and out_datetime < end_datetime:
            early_delta = end_datetime - out_datetime
            result["early_minutes"] = int(early_delta.total_seconds() / 60)
            result["early_departure"] = True
            result["valid"] = False
        
        # Calculate working hours
        if in_datetime:
            result["working_hours"] = time_diff_in_hours(out_datetime, in_datetime)
    
    return result


def calculate_working_hours_vs_required(employee, date, in_time, out_time):
    """
    Calculate working hours vs required hours for an employee using HRMS functions.
    
    Args:
        employee: Employee name
        date: Attendance date
        in_time: Check-in time
        out_time: Check-out time
    
    Returns:
        dict: {
            "working_hours": float,
            "required_hours": float,
            "difference": float,
            "overtime": float
        }
    """
    in_datetime = get_datetime(in_time) if in_time else None
    if not in_datetime:
        return {
            "working_hours": 0,
            "required_hours": 0,
            "difference": 0,
            "overtime": 0
        }
    
    # Get shift using HRMS function
    shift_info = get_actual_start_end_datetime_of_shift(
        employee=employee,
        for_timestamp=in_datetime,
        consider_default_shift=True
    )
    
    if not shift_info or not shift_info.get("shift_type"):
        working_hours = time_diff_in_hours(out_time, in_time) if (in_time and out_time) else 0
        return {
            "working_hours": working_hours,
            "required_hours": 0,
            "difference": working_hours,
            "overtime": 0
        }
    
    # Calculate required hours from shift type
    shift_type = shift_info.get("shift_type")
    required_hours = 0
    if shift_type:
        from hrms.hr.doctype.employee_checkin.employee_checkin import calculate_time_difference
        required_hours = calculate_time_difference(
            shift_type.start_time,
            shift_type.end_time
        )
    
    # Calculate working hours
    working_hours = 0
    if in_time and out_time:
        working_hours = time_diff_in_hours(out_time, in_time)
    
    difference = working_hours - required_hours
    overtime = max(0, difference)  # Only positive difference is overtime
    
    return {
        "working_hours": working_hours,
        "required_hours": required_hours,
        "difference": difference,
        "overtime": overtime
    }
