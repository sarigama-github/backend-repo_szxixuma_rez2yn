import os
from datetime import datetime, date
from typing import List, Optional, Literal

from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from bson import ObjectId

from database import db, create_document, get_documents
from schemas import (
    User,
    Employee as EmployeeSchema,
    Payroll as PayrollSchema,
    PayrollEmployeeItem,
    Payslip as PayslipSchema,
    HRMSConnection as HRMSConnectionSchema,
    Attendance as AttendanceSchema,
    PayrollSettings,
)

app = FastAPI(title="SyncZenith API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Helpers ----------
class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class LoginResponse(BaseModel):
    token: str
    role: Literal["admin", "hr", "employee", "accountant"]
    redirect: str


def oid(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


# ---------- Root & Health ----------
@app.get("/")
def read_root():
    return {"message": "SyncZenith Backend Running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name
            response["connection_status"] = "Connected"
            response["collections"] = db.list_collection_names()
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:100]}"
    return response


# ---------- Auth ----------
@app.post("/api/auth/login", response_model=LoginResponse)
def login(req: LoginRequest):
    # Simple demo auth: role inferred by email prefix; in real app validate password hash
    role_map = {
        "admin": "admin",
        "hr": "hr",
        "acct": "accountant",
        "employee": "employee",
    }
    role: Literal["admin", "hr", "employee", "accountant"] = "employee"
    for key, val in role_map.items():
        if req.email.startswith(key):
            role = val  # type: ignore
            break
    redirect = "/admin" if role in ("admin", "hr", "accountant") else "/employee"
    token = f"demo-token-{role}"
    return LoginResponse(token=token, role=role, redirect=redirect)


# ---------- HRMS Integration ----------
@app.post("/api/hrms/connect")
def connect_hrms(conn: HRMSConnectionSchema):
    # store single connection document (upsert)
    conn.lastSync = datetime.utcnow() if conn.connected else None
    existing = db["hrmsconnection"].find_one({})
    if existing:
        db["hrmsconnection"].update_one({"_id": existing["_id"]}, {"$set": conn.model_dump()})
        return {"status": "updated", "connected": conn.connected}
    else:
        create_document("hrmsconnection", conn)
        return {"status": "created", "connected": conn.connected}


@app.post("/api/hrms/sync")
def sync_hrms():
    # Seed a few demo employees and attendance
    sample = [
        EmployeeSchema(name="Aarav Mehta", department="Engineering", email="employee1@synczenith.com"),
        EmployeeSchema(name="Diya Kapoor", department="HR", email="employee2@synczenith.com"),
        EmployeeSchema(name="Kabir Singh", department="Finance", email="employee3@synczenith.com"),
    ]
    created = 0
    for emp in sample:
        exists = db["employee"].find_one({"email": emp.email})
        if not exists:
            emp.source = "HRMS"
            create_document("employee", emp)
            created += 1
    # Attendance
    for e in db["employee"].find():
        if not db["attendance"].find_one({"employeeId": str(e["_id"])}):
            att = AttendanceSchema(employeeId=str(e["_id"]), presentDays=20, leaveDays=2, overtimeHours=5)
            create_document("attendance", att)
    db["hrmsconnection"].update_one({}, {"$set": {"lastSync": datetime.utcnow(), "connected": True}}, upsert=True)
    return {"status": "ok", "created": created}


# ---------- Employees ----------
@app.get("/api/employees")
def list_employees(department: Optional[str] = None, source: Optional[str] = None):
    filt = {}
    if department:
        filt["department"] = department
    if source:
        filt["source"] = source
    docs = get_documents("employee", filt)
    for d in docs:
        d["_id"] = str(d["_id"])  # make serializable
    return docs


@app.post("/api/employees")
def create_employee(emp: EmployeeSchema):
    _id = create_document("employee", emp)
    return {"_id": _id}


# ---------- Payroll ----------
class CreatePayrollRequest(BaseModel):
    month: date
    type: Literal["Monthly", "Hourly", "Project-based"] = "Monthly"
    employee_ids: List[str]


@app.post("/api/payroll")
def create_payroll(req: CreatePayrollRequest):
    items: List[PayrollEmployeeItem] = []
    for eid in req.employee_ids:
        e = db["employee"].find_one({"_id": oid(eid)})
        if not e:
            continue
        # very simple earnings/deductions example
        basic = e.get("payrollProfile", {}).get("basic", 30000)
        hra = e.get("payrollProfile", {}).get("hra", basic * 0.4)
        gross = basic + hra
        deductions = gross * 0.12  # EPF approx
        net = gross - deductions
        items.append(PayrollEmployeeItem(employee_id=eid, earnings=gross, deductions=deductions, net=net))
    payroll = PayrollSchema(month=req.month, status="Draft", type=req.type, employees=items)
    _id = create_document("payroll", payroll)
    return {"_id": _id, "status": "Draft"}


@app.get("/api/payroll")
def list_payroll(status: Optional[str] = None, month: Optional[str] = None):
    filt = {}
    if status:
        filt["status"] = status
    if month:
        try:
            y, m = month.split("-")
            # store month as date; filter by year-month
            start = datetime(int(y), int(m), 1)
            end_month = int(m) + 1 if int(m) < 12 else 1
            end_year = int(y) if int(m) < 12 else int(y) + 1
            end = datetime(end_year, end_month, 1)
            filt["month"] = {"$gte": start.date(), "$lt": end.date()}
        except Exception:
            pass
    docs = get_documents("payroll", filt)
    for d in docs:
        d["_id"] = str(d["_id"])  # serialize
        for it in d.get("employees", []):
            # ensure serialization for nested
            if isinstance(it, dict) and "employee_id" in it:
                pass
    return docs


@app.get("/api/payroll/{payroll_id}")
def get_payroll(payroll_id: str):
    p = db["payroll"].find_one({"_id": oid(payroll_id)})
    if not p:
        raise HTTPException(404, "Payroll not found")
    p["_id"] = str(p["_id"])
    return p


class ProcessPayrollRequest(BaseModel):
    approve: bool = True


@app.post("/api/payroll/{payroll_id}/process")
def process_payroll(payroll_id: str, req: ProcessPayrollRequest):
    p = db["payroll"].find_one({"_id": oid(payroll_id)})
    if not p:
        raise HTTPException(404, "Payroll not found")
    # Step validations could be added; here we directly mark processed
    db["payroll"].update_one({"_id": p["_id"]}, {"$set": {"status": "Processed"}})
    # Generate payslips
    generated = 0
    for it in p.get("employees", []):
        ps = PayslipSchema(
            employeeId=it["employee_id"],
            payrollMonth=p["month"],
            grossSalary=it["earnings"],
            deductions=it["deductions"],
            netSalary=it["net"],
        )
        create_document("payslip", ps)
        generated += 1
    return {"status": "Processed", "payslips": generated}


# ---------- Payslips ----------
@app.get("/api/payslips")
def list_payslips(employeeId: Optional[str] = None):
    filt = {"employeeId": employeeId} if employeeId else {}
    slips = get_documents("payslip", filt)
    for s in slips:
        s["_id"] = str(s["_id"])  # serialize
    return slips


class SendPayslipsRequest(BaseModel):
    payroll_id: Optional[str] = None
    via: Literal["email", "portal"] = "portal"


@app.post("/api/payslips/send")
def send_payslips(req: SendPayslipsRequest):
    filt = {}
    if req.payroll_id:
        p = db["payroll"].find_one({"_id": oid(req.payroll_id)})
        if not p:
            raise HTTPException(404, "Payroll not found")
        filt["payrollMonth"] = p["month"]
    count = 0
    for s in db["payslip"].find(filt):
        db["payslip"].update_one({"_id": s["_id"]}, {"$set": {"sent": True}})
        count += 1
    if req.payroll_id:
        db["payroll"].update_one({"_id": oid(req.payroll_id)}, {"$set": {"status": "Sent"}})
    return {"status": "sent", "count": count, "via": req.via}


# ---------- Reports ----------
@app.get("/api/reports/summary")
def payroll_summary(month: Optional[str] = None):
    filt = {}
    if month:
        try:
            y, m = month.split("-")
            start = datetime(int(y), int(m), 1).date()
            end_month = int(m) + 1 if int(m) < 12 else 1
            end_year = int(y) if int(m) < 12 else int(y) + 1
            end = datetime(end_year, end_month, 1).date()
            filt["month"] = {"$gte": start, "$lt": end}
        except Exception:
            pass
    total_payrolls = db["payroll"].count_documents(filt)
    processed = db["payroll"].count_documents({**filt, "status": "Processed"})
    sent = db["payroll"].count_documents({**filt, "status": "Sent"})
    # aggregate totals
    gross_total = 0.0
    net_total = 0.0
    for p in db["payroll"].find(filt):
        for it in p.get("employees", []):
            gross_total += float(it.get("earnings", 0))
            net_total += float(it.get("net", 0))
    return {
        "counts": {"total": total_payrolls, "processed": processed, "sent": sent},
        "totals": {"gross": gross_total, "net": net_total},
    }


# ---------- Settings ----------
@app.get("/api/settings")
def get_settings():
    doc = db["payrollsettings"].find_one({})
    if not doc:
        settings = PayrollSettings().model_dump()
        create_document("payrollsettings", PayrollSettings())
        return settings
    doc["_id"] = str(doc["_id"])  # serialize
    return doc


@app.put("/api/settings")
def update_settings(settings: PayrollSettings):
    existing = db["payrollsettings"].find_one({})
    if existing:
        db["payrollsettings"].update_one({"_id": existing["_id"]}, {"$set": settings.model_dump()})
        return {"status": "updated"}
    else:
        create_document("payrollsettings", settings)
        return {"status": "created"}


# ---------- Schema endpoint for tooling ----------
@app.get("/schema")
def schema_index():
    return {
        "collections": [
            "user",
            "employee",
            "payroll",
            "payslip",
            "attendance",
            "hrmsconnection",
            "payrollsettings",
        ]
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
