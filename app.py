from flask import Flask, render_template, request, redirect, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import os
from apscheduler.schedulers.background import BackgroundScheduler
from werkzeug.security import generate_password_hash, check_password_hash
import urllib.parse

app = Flask(__name__)

# ---------- JINJA FILTER ----------
@app.template_filter("month_name")
def month_name_filter(month_str):
    return datetime.strptime(month_str, "%Y-%m").strftime("%B %Y")
# ---------------------------------

# ------------ BASIC CONFIG ------------

# Use /tmp on Render (always writable)
DATA_DIR = os.environ.get("DATA_DIR", "/tmp")
DB_PATH = os.path.join(DATA_DIR, "rent_management.db")

app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# SECRET KEY (use env in production)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

db = SQLAlchemy(app)



# ------------ MODELS ------------







class Admin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)




class Tenant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20), nullable=False)
    room_no = db.Column(db.String(20), nullable=False)
    monthly_rent = db.Column(db.Float, nullable=False)

    dues = db.relationship(
        "RentDue",
        backref="tenant",
        cascade="all, delete-orphan"
    )



class RentDue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenant.id"), nullable=False)
    month = db.Column(db.String(7), nullable=False)  # format: YYYY-MM
    amount = db.Column(db.Float, nullable=False)
    status = db.Column(db.String(20), default="unpaid")  # unpaid / paid
    paid_date = db.Column(db.DateTime, nullable=True)

    


# ------------ HELPERS ------------



def build_whatsapp_link(phone, message):
    """
    Creates WhatsApp deep link with pre-filled message
    """
    phone = phone.strip()
    if not phone.startswith("91"):
        phone = "91" + phone

    encoded_msg = urllib.parse.quote(message)
    return f"https://wa.me/{phone}?text={encoded_msg}"


def get_whatsapp_reminder_link(tenant):
    months_due, total_due, _ = calculate_due_for_tenant(tenant.id)

    if months_due == 0:
        return None

    message = (
        f"Hello {tenant.name},\n\n"
        f"Your rent is overdue.\n"
        f"Room No: {tenant.room_no}\n"
        f"Pending Months: {months_due}\n"
        f"Total Due Amount: ₹{total_due}\n\n"
        "Please pay as soon as possible.\n"
        "- Rent Management"
    )

    return build_whatsapp_link(tenant.phone, message)




def current_month_str():
    """Return current month in 'YYYY-MM' format."""
    return datetime.today().strftime("%Y-%m")


def generate_current_month_rent():
    """
    For ALL tenants:
    - If there is NO RentDue for current month -> create one with status = 'unpaid'
    - If already exists -> ignore
    """
    this_month = current_month_str()
    tenants = Tenant.query.all()

    for t in tenants:
        exists = RentDue.query.filter_by(tenant_id=t.id, month=this_month).first()
        if exists:
            continue

        due = RentDue(
            tenant_id=t.id,
            month=this_month,
            amount=t.monthly_rent,
            status="unpaid"
        )
        db.session.add(due)

    db.session.commit()



def start_scheduler():
    scheduler = BackgroundScheduler()

    # Har mahine ke 1 tareekh ko subah 00:10 baje rent generate karega
    scheduler.add_job(generate_current_month_rent, 'cron', day=1, hour=0, minute=10)

    # Agar aap chaho roz subah check ho (safe hai kyunki function duplicate nahi banata):
    # scheduler.add_job(generate_current_month_rent, 'cron', hour=0, minute=10)

    scheduler.start()



def admin_required():
    if "admin_id" not in session:
        return redirect("/login")





def calculate_due_for_tenant(tenant_id):
    """
    Return (months_due, total_due) for given tenant
    """
    dues = RentDue.query.filter_by(tenant_id=tenant_id, status="unpaid").all()
    months_due = len(dues)
    total_due = sum(d.amount for d in dues)
    return months_due, total_due, dues


def format_month(month_str):
    """
    Convert 'YYYY-MM' → 'Month YYYY'
    Example: '2025-01' → 'January 2025'
    """
    return datetime.strptime(month_str, "%Y-%m").strftime("%B %Y")


# ------------ ROUTES ------------





@app.route("/")
def home():
    return redirect("/dashboard")



@app.route("/dashboard")
def dashboard():

    if "admin_id" not in session:
        return redirect("/login")


    tenants = Tenant.query.all()

    total_tenants = len(tenants)

    total_monthly_rent = sum(t.monthly_rent for t in tenants)

    total_due_amount = 0
    tenants_with_due = 0

    tenant_due_list = []

    for t in tenants:
        months_due, total_due, _ = calculate_due_for_tenant(t.id)

        total_due_amount += total_due

        if months_due > 0:
            tenants_with_due += 1
            tenant_due_list.append({
                "tenant": t,
                "months_due": months_due,
                "total_due": total_due,
                "whatsapp_link": get_whatsapp_reminder_link(t)

            })

    # Highest due first
    tenant_due_list.sort(key=lambda x: x["total_due"], reverse=True)

    return render_template(
        "dashboard.html",
        total_tenants=total_tenants,
        total_monthly_rent=total_monthly_rent,
        total_due_amount=total_due_amount,
        tenants_with_due=tenants_with_due,
        tenant_due_list=tenant_due_list[:5]  # top 5
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    try:
        if request.method == "POST":
            username = request.form["username"]
            password = request.form["password"]

            admin = Admin.query.filter_by(username=username).first()
            print("LOGIN DEBUG → admin:", admin)

            if admin and admin.check_password(password):
                session["admin_id"] = admin.id
                return redirect("/dashboard")
            else:
                return render_template("login.html", error="Invalid username or password")

        return render_template("login.html")

    except Exception as e:
        print("LOGIN ERROR:", e)
        raise e


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")



@app.route("/change-password", methods=["GET", "POST"])
def change_password():
    if "admin_id" not in session:
        return redirect("/login")

    admin = Admin.query.get(session["admin_id"])

    if request.method == "POST":
        old_password = request.form["old_password"]
        new_password = request.form["new_password"]

        if not admin.check_password(old_password):
            return render_template(
                "change_password.html",
                error="Old password is incorrect"
            )

        admin.set_password(new_password)
        db.session.commit()
        return redirect("/dashboard")

    return render_template("change_password.html")










# --- TENANT LIST + SUMMARY ---
@app.route("/tenants")
def tenants_page():

    if "admin_id" not in session:
        return redirect("/login")

    tenants = Tenant.query.all()
    tenant_data = []

    for t in tenants:
        months_due, total_due, _ = calculate_due_for_tenant(t.id)
        tenant_data.append({
            "tenant": t,
            "months_due": months_due,
            "total_due": total_due
        })

    return render_template("tenants.html", tenants=tenant_data)


# --- ADD TENANT ---
@app.route("/tenant/add", methods=["GET", "POST"])
def add_tenant():

    if "admin_id" not in session:
        return redirect("/login")

    if request.method == "POST":
        name = request.form["name"]
        phone = request.form["phone"]
        room_no = request.form["room_no"]
        monthly_rent = float(request.form["monthly_rent"])

        t = Tenant(
            name=name,
            phone=phone,
            room_no=room_no,
            monthly_rent=monthly_rent
        )
        db.session.add(t)
        db.session.commit()
        return redirect("/tenants")

    return render_template("add_tenant.html")


# --- EDIT TENANT ---
@app.route("/tenant/edit/<int:tenant_id>", methods=["GET", "POST"])
def edit_tenant(tenant_id):

    if "admin_id" not in session:
        return redirect("/login")

    tenant = Tenant.query.get_or_404(tenant_id)

    if request.method == "POST":
        tenant.name = request.form["name"]
        tenant.phone = request.form["phone"]
        tenant.room_no = request.form["room_no"]
        tenant.monthly_rent = float(request.form["monthly_rent"])
        db.session.commit()
        return redirect("/tenants")

    return render_template("edit_tenant.html", tenant=tenant)


# --- DELETE TENANT ---
@app.route("/tenant/delete/<int:tenant_id>")
def delete_tenant(tenant_id):

    if "admin_id" not in session:
        return redirect("/login")

    tenant = Tenant.query.get_or_404(tenant_id)
    db.session.delete(tenant)
    db.session.commit()
    return redirect("/tenants")


# --- GENERATE CURRENT MONTH BILLS (1 TAREEKH KO CHALANA) ---
@app.route("/generate-current-month")
def generate_current_month():

    if "admin_id" not in session:
        return redirect("/login")

    generate_current_month_rent()
    return redirect("/tenants")


# --- VIEW DUES FOR ONE TENANT ---
@app.route("/tenant/<int:tenant_id>/dues")
def tenant_dues(tenant_id):

    if "admin_id" not in session:
        return redirect("/login")

    tenant = Tenant.query.get_or_404(tenant_id)
    months_due, total_due, dues = calculate_due_for_tenant(tenant_id)
    all_dues = RentDue.query.filter_by(tenant_id=tenant_id).order_by(RentDue.month.desc()).all()
    return render_template(
    "tenant_dues.html",
    tenant=tenant,
    months_due=months_due,
    total_due=total_due,
    dues=all_dues,
    format_month=format_month
)



# --- MARK A PARTICULAR MONTH AS PAID (MANUAL) ---
@app.route("/rent/mark-paid/<int:due_id>")
def mark_rent_paid(due_id):

    if "admin_id" not in session:
        return redirect("/login")

    due = RentDue.query.get_or_404(due_id)
    if due.status == "unpaid":
        due.status = "paid"
        due.paid_date = datetime.utcnow()
        db.session.commit()
    return redirect(f"/tenant/{due.tenant_id}/dues")


@app.route("/rent/mark-unpaid/<int:due_id>")
def mark_rent_unpaid(due_id):

    if "admin_id" not in session:
        return redirect("/login")

    due = RentDue.query.get_or_404(due_id)

    if due.status == "paid":
        due.status = "unpaid"
        due.paid_date = None

    db.session.commit()
    return redirect(f"/tenant/{due.tenant_id}/dues")



@app.route("/generate-fake-3-months/<int:tenant_id>")
def generate_fake_3_months(tenant_id):
    tenant = Tenant.query.get_or_404(tenant_id)

    # Last 3 months ke month strings generate karo
    today = datetime.today()
    fake_months = []

    for i in range(3):
        month_date = (today.replace(day=1) - timedelta(days=30 * (i + 1)))
        month_str = month_date.strftime("%Y-%m")
        fake_months.append(month_str)

    for m in fake_months:
        exists = RentDue.query.filter_by(tenant_id=tenant_id, month=m).first()
        if not exists:
            fake_due = RentDue(
                tenant_id=tenant_id,
                month=m,
                amount=tenant.monthly_rent,
                status="unpaid"
            )
            db.session.add(fake_due)

    db.session.commit()

    return f"Fake 3 months due added for tenant {tenant.name}"



# ------------ INIT DB & RUN ------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()

        if not Admin.query.first():
            admin = Admin(username="admin")
            admin.set_password("admin123")
            db.session.add(admin)
            db.session.commit()
            print("Default admin created → username: admin | password: admin123")

        # ❌ Scheduler disabled for Railway free tier (enable later)
        # start_scheduler()

    # ❌ DO NOT run app.run() in production




