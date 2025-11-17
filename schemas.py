"""
Database Schemas for SyncZenith (Payroll + HRMS + CRM)

Each Pydantic model maps to a MongoDB collection (lowercased class name).
Use these schemas for validation when inserting/updating via database helpers.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal
from datetime import date, datetime

# Authentication / Users
class User(BaseModel):
    email: EmailStr
    name: str
    role: Literal["admin", "hr", "employee", "accountant"] = "employee"
    department: Optional[str] = None
    password_hash: Optional[str] = Field(None, description="Hashed password")
    employee_id: Optional[str] = Field(None, description="Link to employee document _id if role is employee")
    is_active: bool = True

# Core domain
class PayrollProfile(BaseModel):
    basic: float
    hra: float
    ta: float = 0
    bonus: float = 0
    epf: float = 0  # percent
    esi: float = 0  # percent
    totalCTC: float

class Employee(BaseModel):
    name: str
    department: Optional[str] = None
    email: EmailStr
    paymentType: Literal["Monthly", "Project", "Hourly"] = "Monthly"
    payrollProfile: Optional[PayrollProfile] = None
    status: Literal["Active", "Inactive"] = "Active"
    source: Literal["HRMS", "Manual"] = "Manual"

class PayrollEmployeeItem(BaseModel):
    employee_id: str
    earnings: float
    deductions: float
    net: float

class Payroll(BaseModel):
    month: date
    status: Literal["Draft", "Processed", "Sent"] = "Draft"
    type: Literal["Monthly", "Hourly", "Project-based"] = "Monthly"
    employees: List[PayrollEmployeeItem] = []

class Payslip(BaseModel):
    employeeId: str
    payrollMonth: date
    grossSalary: float
    deductions: float
    netSalary: float
    pdfPath: Optional[str] = None
    sent: bool = False

class HRMSConnection(BaseModel):
    connected: bool = False
    apiKey: Optional[str] = None
    lastSync: Optional[datetime] = None

class Attendance(BaseModel):
    employeeId: str
    presentDays: int = 0
    leaveDays: int = 0
    overtimeHours: float = 0

# Settings / Statutory
class PayrollSettings(BaseModel):
    epf_percent: float = 12.0
    esi_percent: float = 0.75
    tax_rules: dict = Field(default_factory=dict)
    payslip_logo_url: Optional[str] = None
    payslip_header: Optional[str] = "SyncZenith Payslip"

# Reporting helper
class DateRange(BaseModel):
    start: date
    end: date
