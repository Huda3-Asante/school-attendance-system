from fastapi import FastAPI, Depends, HTTPException, Request, Query, Body
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, OAuth2PasswordRequestForm
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from fastapi.templating import Jinja2Templates
from sqlalchemy import extract
from datetime import date, datetime, time
from .database import engine, SessionLocal
from . import models, schemas
import secrets
import ipaddress
from .auth import create_access_token, SECRET_KEY, ALGORITHM, verify_password, hash_password

# Create tables
models.Base.metadata.create_all(bind=engine)

security = HTTPBearer()

app = FastAPI(
    title="Attendance System",
    description="A secure attendance system",
    version="1.0.0"
)

templates = Jinja2Templates(directory="templates")


app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/register-page")
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@app.get("/login-page", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.get("/admin-dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request):
    return templates.TemplateResponse("admin-dashboard.html", {"request": request})

@app.get("/staff-dashboard", response_class=HTMLResponse)
def staff_dashboard(request: Request):
    return templates.TemplateResponse("staff-dashboard.html", {"request": request})

@app.get("/forgot-page", response_class=HTMLResponse)
def forgot_page(request: Request):
    return templates.TemplateResponse("forgot.html", {"request": request})

# DATABASE DEPENDENCY

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# AUTO CREATE DEFAULT ADMIN

@app.on_event("startup")
def create_default_admin():
    db = SessionLocal()
    admin_exists = db.query(models.User).filter(models.User.role == "admin").first()

    if not admin_exists:
        admin_user = models.User(
            full_name="System Admin",
            email="admin@school.com",
            password=hash_password("Admin@123"),
            role="admin"
        )
        db.add(admin_user)
        db.commit()
        print("Default admin created: admin@school.com / Admin@123")

    db.close()

@app.get("/")
def read_root():
    return {"message": "School Attendance System Running"}
# REGISTER STAFF ONLY

@app.post("/register")
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):

    # Block admin registration publicly
    if user.role.lower() == "admin":
        raise HTTPException(status_code=403, detail="Admin registration not allowed")

    existing_user = db.query(models.User).filter(models.User.email == user.email).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = models.User(
        full_name=user.full_name,
        email=user.email,
        password=hash_password(user.password),
        role="staff"
    )

    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    return {"message": "Staff registered successfully"}

# LOGIN

@app.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):

    db_user = db.query(models.User).filter(
        models.User.email == form_data.username
    ).first()

    if not db_user:
        raise HTTPException(status_code=400, detail="Invalid credentials")

    if not verify_password(form_data.password, db_user.password):
        raise HTTPException(status_code=400, detail="Invalid credentials")

    access_token = create_access_token(
        data={"sub": db_user.email, "role": db_user.role}
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": db_user.role   
    }

# GET CURRENT USER

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):

    token = credentials.credentials

    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials"
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")

        if email is None:
            raise credentials_exception

    except JWTError:
        raise credentials_exception

    user = db.query(models.User).filter(models.User.email == email).first()

    if user is None:
        raise credentials_exception

    return user


@app.get("/me")
def get_me(current_user: models.User = Depends(get_current_user)):
    return {
        "full_name": current_user.full_name,
        "role": current_user.role
    }

# MARK ATTENDANCE (SECURED)

@app.post("/mark-attendance")
def mark_attendance(
    request: Request,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    # LOCATION RESTRICTION
    allowed_network = ipaddress.ip_network("127.0.0.1/32")

    client_ip = ipaddress.ip_address(request.client.host)

    if client_ip not in allowed_network:
        raise HTTPException(
            status_code=403,
            detail="Attendance allowed only on school network"
        )

    if current_user.role != "staff":
        raise HTTPException(status_code=403, detail="Only staff can mark attendance")

    now = datetime.now()
    today = now.date()

    existing = db.query(models.Attendance).filter(
        models.Attendance.user_id == current_user.id,
        models.Attendance.date == today
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Already marked today")

    late_time = time(7, 30)
    close_time = time(8, 00)

    if now.time() > close_time:
        raise HTTPException(status_code=403, detail="Attendance closed for today")

    status = "Present"
    if now.time() > late_time:
        status = "Late"

    attendance = models.Attendance(
        user_id=current_user.id,
        date=today,
        check_in=now,
        status=status
    )

    db.add(attendance)
    db.commit()

    return {"message": "Attendance marked", "status": status}

# ABSENTEES (ADMIN ONLY)

@app.get("/absentees")
def get_absentees(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    today = datetime.now().date()

    staff_users = db.query(models.User).filter(models.User.role == "staff").all()
    attended = db.query(models.Attendance).filter(models.Attendance.date == today).all()

    attended_ids = [a.user_id for a in attended]

    absentees = [
        {"id": u.id, "full_name": u.full_name, "email": u.email}
        for u in staff_users if u.id not in attended_ids
    ]

    return {"date": str(today), "absentees": absentees}

@app.get("/my-attendance")
def my_attendance(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if current_user.role != "staff":
        raise HTTPException(status_code=403, detail="Staff access required")

    records = db.query(models.Attendance).filter(
        models.Attendance.user_id == current_user.id
    ).order_by(models.Attendance.date.desc()).all()

    return [
        {
            "date": str(record.date),
            "check_in": record.check_in.strftime("%H:%M:%S"),
            "status": record.status
        }
        for record in records
    ]


@app.post("/forgot-password")
def forgot_password(email: str, db: Session = Depends(get_db)):

    user = db.query(models.User).filter(models.User.email == email).first()

    if not user:
        raise HTTPException(status_code=404, detail="Email not found")

    token = secrets.token_urlsafe(32)
    user.reset_token = token
    db.commit()

    return {
        "message": "Reset token generated",
        "reset_token": token   
    }

@app.post("/reset-password")
def reset_password(token: str, new_password: str, db: Session = Depends(get_db)):

    user = db.query(models.User).filter(models.User.reset_token == token).first()

    if not user:
        raise HTTPException(status_code=400, detail="Invalid token")

    user.password = hash_password(new_password)
    user.reset_token = None
    db.commit()

    return {"message": "Password reset successful"}

@app.get("/daily-summary")
def daily_summary(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admins-only")

    today = date.today()

    staff_users = db.query(models.User).filter(models.User.role == "staff").all()

    present = db.query(models.Attendance).filter(
        models.Attendance.date == today,
        models.Attendance.status == "Present"
    ).all()

    late = db.query(models.Attendance).filter(
        models.Attendance.date == today,
        models.Attendance.status == "Late"
    ).all()

    absent_count = len(staff_users) - len(present) - len(late)

    return {
        "total_staff": len(staff_users),
        "present_count": len(present),
        "late_count": len(late),
        "absent_count": absent_count
    }

@app.get("/all-staff")
def get_all_staff(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admins-only")

    staff = db.query(models.User).filter(
        models.User.role == "staff"
    ).all()

    return [
        {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email
        }
        for user in staff
    ]
@app.delete("/delete-staff/{staff_id}")
def delete_staff(
    staff_id: int,
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    staff = db.query(models.User).filter(
        models.User.id == staff_id,
        models.User.role == "staff"
    ).first()

    if not staff:
        raise HTTPException(status_code=404, detail="Staff not found")

    # Delete attendance records first
    db.query(models.Attendance).filter(
        models.Attendance.user_id == staff_id
    ).delete()

    # Now delete staff
    db.delete(staff)
    db.commit()

    return {"message": "Staff deleted successfully"}

@app.get("/attendance-percentage")
def attendance_percentage(
    current_user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db)
):

    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admins only")

    staff_users = db.query(models.User).filter(
        models.User.role == "staff"
    ).all()

    results = []

    for user in staff_users:

        records = db.query(models.Attendance).filter(
            models.Attendance.user_id == user.id
        ).all()

        total_days = len(records)

        present_days = len([r for r in records if r.status == "Present"])
        late_days = len([r for r in records if r.status == "Late"])

        attended_days = present_days + late_days

        percentage = 0
        if total_days > 0:
            percentage = round((attended_days / total_days) * 100, 2)

        results.append({
            "full_name": user.full_name,
            "present_days": present_days,
            "late_days": late_days,
            "attendance_percentage": percentage
        })

    return results