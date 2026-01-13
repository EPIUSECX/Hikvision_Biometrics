# -*- coding: utf-8 -*-
"""
Attendance Processor - Creates Attendance records from Employee Checkin records
Integrates with Frappe HRMS Time and Attendance module
"""
import frappe
from frappe.utils import getdate, get_datetime
from datetime import timedelta, datetime

# HRMS standard imports
from hrms.hr.doctype.employee_checkin.employee_checkin import (
    mark_attendance_and_link_log,
    calculate_working_hours,
)
from hrms.hr.utils import get_holidays_for_employee, get_holiday_dates_for_employee
from hrms.hr.doctype.shift_assignment.shift_assignment import get_actual_start_end_datetime_of_shift


def create_attendance_from_checkins(date=None):
    """
    Create Attendance records from Employee Checkin records for a given date.
    Uses HRMS standard functions for attendance creation.
    
    Args:
        date: Date to process (default: today)
    
    Returns:
        tuple: (created_count, skipped_count, error_count)
    """
    if not date:
        date = getdate()
    
    if not frappe.db.exists("DocType", "Attendance"):
        frappe.logger().warning("Attendance doctype not found. HRMS module may not be installed.")
        return 0, 0, 0
    
    # Get all checkins for the date that don't have attendance yet
    # Get as documents, not dicts, for HRMS functions
    checkin_names = frappe.db.get_all(
        "Employee Checkin",
        filters={
            "time": ["between", [get_datetime(f"{date} 00:00:00"), get_datetime(f"{date} 23:59:59")]],
            "skip_auto_attendance": 0,
            "attendance": ["is", "not set"]
        },
        fields=["name", "employee"],
        order_by="employee, time"
    )
    
    if not checkin_names:
        return 0, 0, 0
    
    created = 0
    skipped = 0
    errors = 0
    
    # Group checkins by employee
    employee_checkins = {}
    for checkin in checkin_names:
        employee = checkin.employee
        if employee not in employee_checkins:
            employee_checkins[employee] = []
        employee_checkins[employee].append(checkin.name)
    
    # Process each employee
    for employee, checkin_name_list in employee_checkins.items():
        try:
            # Check if attendance already exists
            existing_attendance = frappe.db.exists(
                "Attendance",
                {
                    "employee": employee,
                    "attendance_date": date,
                    "docstatus": ["<", 2]
                }
            )
            if existing_attendance:
                skipped += 1
                continue
            
            # Check holiday using HRMS function
            holiday_dates = get_holiday_dates_for_employee(employee, date, date)
            if date in holiday_dates:
                skipped += 1
                continue
            
            # Check leave using HRMS pattern (similar to Attendance.check_leave_record)
            leave_record = _check_leave_record_hrms(employee, date)
            if leave_record:
                # Employee is on leave - HRMS handles this in Attendance doctype
                # Skip creating attendance as leave application should handle it
                skipped += 1
                continue
            
            # Get checkin documents
            checkin_docs = [frappe.get_doc("Employee Checkin", name) for name in checkin_name_list]
            
            # Process checkins to determine attendance using HRMS functions
            attendance = _process_checkins_for_attendance_hrms(
                employee=employee,
                date=date,
                checkins=checkin_docs
            )
            
            if attendance:
                created += 1
            else:
                skipped += 1
                
        except Exception as e:
            errors += 1
            frappe.log_error(
                f"Error creating attendance for employee {employee} on {date}: {str(e)}",
                "Attendance Creation Error"
            )
    
    frappe.db.commit()
    return created, skipped, errors


def _process_checkins_for_attendance_hrms(employee, date, checkins):
    """
    Process checkins for an employee on a date and create Attendance record using HRMS functions.
    
    Args:
        employee: Employee name
        date: Attendance date
        checkins: List of Employee Checkin document objects
    
    Returns:
        Attendance document or None
    """
    if not checkins:
        return None
    
    # Sort checkins by time
    checkins.sort(key=lambda x: x.time)
    
    # Get shift information using HRMS function
    # Use first checkin time to determine shift
    first_checkin_time = get_datetime(checkins[0].time)
    shift_info = get_actual_start_end_datetime_of_shift(employee, first_checkin_time, True)
    
    shift = None
    overtime_type = None
    if shift_info:
        shift = shift_info.shift_type.name
        overtime_type = shift_info.overtime_type
    
    # Use HRMS calculate_working_hours function
    # Get shift type to determine check-in/out type and working hours calculation method
    check_in_out_type = "Strictly based on Log Type in Employee Checkin"
    working_hours_calc_type = "First Check-in and Last Check-out"
    
    if shift:
        shift_type_doc = frappe.get_doc("Shift Type", shift)
        # Use shift type's configuration if available
        if hasattr(shift_type_doc, "determine_check_in_and_check_out"):
            check_in_out_type = shift_type_doc.determine_check_in_and_check_out or check_in_out_type
        if hasattr(shift_type_doc, "working_hours_calculation_based_on"):
            working_hours_calc_type = shift_type_doc.working_hours_calculation_based_on or working_hours_calc_type
    
    working_hours, in_time, out_time = calculate_working_hours(
        checkins,
        check_in_out_type,
        working_hours_calc_type
    )
    
    # Determine attendance status based on working hours
    # Get shift type details for thresholds
    working_hours_threshold_for_absent = 0
    working_hours_threshold_for_half_day = 0
    
    if shift:
        shift_type_doc = frappe.get_doc("Shift Type", shift)
        working_hours_threshold_for_absent = shift_type_doc.working_hours_threshold_for_absent or 0
        working_hours_threshold_for_half_day = shift_type_doc.working_hours_threshold_for_half_day or 0
    
    # Determine status (same logic as HRMS Shift Type.get_attendance())
    if working_hours_threshold_for_absent and working_hours < working_hours_threshold_for_absent:
        status = "Absent"
    elif working_hours_threshold_for_half_day and working_hours < working_hours_threshold_for_half_day:
        status = "Half Day"
    else:
        status = "Present"
    
    # Check for late entry and early exit (using HRMS logic)
    late_entry = False
    early_exit = False
    
    if shift_info and shift and in_time and out_time:
        shift_type_doc = frappe.get_doc("Shift Type", shift)
        
        # Late entry check (HRMS pattern)
        if (
            shift_type_doc.enable_late_entry_marking
            and in_time
            and shift_info.start_datetime
        ):
            late_entry_grace_period = shift_type_doc.late_entry_grace_period or 0
            if in_time > shift_info.start_datetime + timedelta(minutes=late_entry_grace_period):
                late_entry = True
        
        # Early exit check (HRMS pattern)
        if (
            shift_type_doc.enable_early_exit_marking
            and out_time
            and shift_info.end_datetime
        ):
            early_exit_grace_period = shift_type_doc.early_exit_grace_period or 0
            if out_time < shift_info.end_datetime - timedelta(minutes=early_exit_grace_period):
                early_exit = True
    
    # Use HRMS mark_attendance_and_link_log function
    attendance = mark_attendance_and_link_log(
        logs=checkins,
        attendance_status=status,
        attendance_date=date,
        working_hours=working_hours,
        late_entry=late_entry,
        early_exit=early_exit,
        in_time=in_time,
        out_time=out_time,
        shift=shift,
        overtime_type=overtime_type
    )
    
    return attendance


def _check_leave_record_hrms(employee, attendance_date):
    """
    Check leave record using HRMS pattern (similar to Attendance.check_leave_record).
    
    Args:
        employee: Employee name
        attendance_date: Date to check
    
    Returns:
        dict: Leave record if found, None otherwise
    """
    LeaveApplication = frappe.qb.DocType("Leave Application")
    leave_record = (
        frappe.qb.from_(LeaveApplication)
        .select(
            LeaveApplication.leave_type,
            LeaveApplication.half_day,
            LeaveApplication.half_day_date,
            LeaveApplication.name,
        )
        .where(
            (LeaveApplication.employee == employee)
            & (attendance_date >= LeaveApplication.from_date)
            & (attendance_date <= LeaveApplication.to_date)
            & (LeaveApplication.status == "Approved")
            & (LeaveApplication.docstatus == 1)
        )
    ).run(as_dict=True)
    
    return leave_record[0] if leave_record else None




def on_employee_checkin_insert(doc, method):
    """
    Hook function called after Employee Checkin is inserted.
    Optionally creates Attendance record if auto_create_attendance is enabled.
    """
    try:
        settings = frappe.get_single("Biometric Integration Settings")
        if not settings.get("auto_create_attendance", 0):
            return
        
        # Process attendance for the checkin date
        checkin_date = getdate(doc.time)
        create_attendance_from_checkins(checkin_date)
    except Exception as e:
        frappe.log_error(
            f"Error in on_employee_checkin_insert: {str(e)}",
            "Attendance Hook Error"
        )


@frappe.whitelist()
def process_attendance_for_date_range(start_date, end_date):
    """
    Process attendance for a date range.
    
    Args:
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
    
    Returns:
        dict: Summary of processing
    """
    start = getdate(start_date)
    end = getdate(end_date)
    
    total_created = 0
    total_skipped = 0
    total_errors = 0
    
    current_date = start
    while current_date <= end:
        created, skipped, errors = create_attendance_from_checkins(current_date)
        total_created += created
        total_skipped += skipped
        total_errors += errors
        current_date += timedelta(days=1)
    
    return {
        "created": total_created,
        "skipped": total_skipped,
        "errors": total_errors,
        "message": f"Processed {total_created} attendance records, {total_skipped} skipped, {total_errors} errors"
    }
