# -*- coding: utf-8 -*-
"""
Device Monitor - Monitors device health and status
"""
import frappe
import requests
from requests.auth import HTTPDigestAuth
from datetime import datetime, timedelta
from frappe.utils import now_datetime


def check_device_health(device_ip, username, password, timeout=5):
    """
    Check if a device is online and responding.
    
    Args:
        device_ip: Device IP address
        username: Device username
        password: Device password
        timeout: Request timeout in seconds
    
    Returns:
        dict: {
            "online": bool,
            "response_time": float,
            "error": str or None,
            "last_check": datetime
        }
    """
    start_time = datetime.now()
    
    try:
        # Try to access device info endpoint
        url = f"http://{device_ip}/ISAPI/System/deviceInfo"
        
        response = requests.get(
            url,
            auth=HTTPDigestAuth(username, password),
            timeout=timeout,
            verify=False
        )
        
        response_time = (datetime.now() - start_time).total_seconds()
        
        if response.status_code == 200:
            return {
                "online": True,
                "response_time": response_time,
                "error": None,
                "last_check": now_datetime()
            }
        else:
            return {
                "online": False,
                "response_time": response_time,
                "error": f"HTTP {response.status_code}",
                "last_check": now_datetime()
            }
    
    except requests.exceptions.Timeout:
        return {
            "online": False,
            "response_time": timeout,
            "error": "Connection timeout",
            "last_check": now_datetime()
        }
    except requests.exceptions.ConnectionError:
        return {
            "online": False,
            "response_time": 0,
            "error": "Connection refused",
            "last_check": now_datetime()
        }
    except Exception as e:
        return {
            "online": False,
            "response_time": 0,
            "error": str(e),
            "last_check": now_datetime()
        }


def update_device_status(device_ip, status, error_message=None):
    """
    Update device status in Biometric Device table.
    
    Args:
        device_ip: Device IP address
        status: Status (Online, Offline, Error)
        error_message: Error message if any
    """
    try:
        # Find device by IP in settings
        settings = frappe.get_single("Biometric Integration Settings")
        
        # Check child table devices
        device_found = False
        if hasattr(settings, "devices") and settings.devices:
            for device in settings.devices:
                if device.ip_address == device_ip:
                    if not frappe.db.has_column("Biometric Device", "status"):
                        # Add status field via patch if needed
                        pass
                    else:
                        frappe.db.set_value("Biometric Device", device.name, {
                            "status": status,
                            "last_status_check": now_datetime(),
                            "last_error": error_message
                        })
                        device_found = True
                        break
        
        if not device_found:
            # Could also check main IP
            if settings.ip == device_ip:
                # Main device - could store status in settings
                pass
        
        frappe.db.commit()
    
    except Exception as e:
        frappe.log_error(
            f"Error updating device status: {str(e)}",
            "Device Monitor Error"
        )


def monitor_all_devices():
    """
    Check health of all configured devices.
    Called by scheduler.
    """
    try:
        settings = frappe.get_single("Biometric Integration Settings")
        
        devices_to_check = []
        
        # Get devices from child table
        if hasattr(settings, "devices") and settings.devices:
            for device in settings.devices:
                if device.is_active:
                    devices_to_check.append({
                        "ip": device.ip_address,
                        "username": device.username or settings.username,
                        "password": device.get_password("password") if hasattr(device, "get_password") else device.password,
                        "name": device.name
                    })
        
        # Also check main device if configured
        if settings.ip and not any(d["ip"] == settings.ip for d in devices_to_check):
            devices_to_check.append({
                "ip": settings.ip,
                "username": settings.username,
                "password": settings.get_password("password"),
                "name": "Main Device"
            })
        
        # Check each device
        for device in devices_to_check:
            health = check_device_health(
                device["ip"],
                device["username"],
                device["password"]
            )
            
            status = "Online" if health["online"] else "Offline"
            update_device_status(device["ip"], status, health.get("error"))
            
            # Log if device is offline
            if not health["online"]:
                frappe.logger().warning(
                    f"Device {device['ip']} is offline: {health.get('error')}"
                )
    
    except Exception as e:
        frappe.log_error(
            f"Error monitoring devices: {str(e)}",
            "Device Monitor Error"
        )


def get_device_status_summary():
    """
    Get summary of all device statuses.
    
    Returns:
        dict: Summary of device statuses
    """
    try:
        settings = frappe.get_single("Biometric Integration Settings")
        
        devices = []
        
        # Get devices from child table
        if hasattr(settings, "devices") and settings.devices:
            for device in settings.devices:
                status = "Unknown"
                last_check = None
                last_error = None
                
                if frappe.db.has_column("Biometric Device", "status"):
                    status = device.status or "Unknown"
                    last_check = device.last_status_check
                    last_error = device.last_error
                
                devices.append({
                    "name": device.device or device.ip_address,
                    "ip": device.ip_address,
                    "status": status,
                    "is_active": device.is_active,
                    "last_check": last_check,
                    "last_error": last_error
                })
        
        online_count = sum(1 for d in devices if d["status"] == "Online")
        offline_count = sum(1 for d in devices if d["status"] == "Offline")
        
        return {
            "total": len(devices),
            "online": online_count,
            "offline": offline_count,
            "devices": devices
        }
    
    except Exception as e:
        frappe.log_error(
            f"Error getting device status summary: {str(e)}",
            "Device Monitor Error"
        )
        return {
            "total": 0,
            "online": 0,
            "offline": 0,
            "devices": []
        }
