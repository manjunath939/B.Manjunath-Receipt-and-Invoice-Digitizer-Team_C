from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, make_response, send_from_directory, Response
)
from dotenv import load_dotenv
load_dotenv()
from groq import Groq
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_dance.contrib.google import make_google_blueprint, google
import sqlite3, jwt, datetime, os, hashlib, csv
from io import StringIO

import easyocr

# ================== EASY OCR INIT ==================
ocr_engine = easyocr.Reader(['en'], gpu=False)

import cv2
import numpy as np
from PIL import Image
from fpdf import FPDF
import smtplib

# ================== CONFIG ==================
app = Flask(__name__)
app.secret_key = os.getenv("flask-secret-key","dev")
JWT_SECRET = os.getenv("jwt-secret-key","dev")
JWT_ALGO = "HS256"


GROQ_API_KEY = os.getenv("GROQ_API_KEY")

groq_client = Groq(api_key=GROQ_API_KEY)

DB_NAME = "database.db"
UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf"}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"


# ================== GOOGLE OAUTH ==================
from flask_dance.contrib.google import make_google_blueprint, google
import os

google_bp = make_google_blueprint(
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    scope=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile"
    ],
    redirect_to="google_login"
)

app.register_blueprint(google_bp)


# ================== DATABASE ==================
def get_db():
    return sqlite3.connect(DB_NAME, timeout=10)
def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE,
            password TEXT,
            is_verified INTEGER DEFAULT 0,
            reset_otp TEXT,
            otp_expiry TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT,
            stored_filename TEXT,
            original_filename TEXT,
            doc_type TEXT,
            uploaded_at TEXT,
            file_hash TEXT,
            ocr_text TEXT,

            vendor TEXT,
            invoice_no TEXT,
            bill_date TEXT,
            total_amount TEXT,
            items TEXT,
            is_edited INTEGER DEFAULT 0,
            latitude TEXT,
            longitude TEXT
        )
    """)

    conn.commit()
    conn.close()

init_db()

# ================== HELPERS ==================
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def create_jwt(email):
    return jwt.encode(
        {"email": email, "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1)},
        JWT_SECRET,
        algorithm=JWT_ALGO
    )

def verify_jwt(token):
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
        return payload.get("email")
    except jwt.ExpiredSignatureError:
        print("JWT expired")
        return None
    except jwt.DecodeError:
        print("JWT invalid")
        return None
import re
import json

def extract_receipt_fields(text):

    data = {
        "vendor": None,
        "invoice_no": None,
        "bill_date": None,
        "total_amount": None,
        "items": []
    }

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    lower_lines = [l.lower() for l in lines]

    # ==============================
# 1️⃣ STRONG VENDOR DETECTION
# ==============================

    for line in lines[:5]:
        if line.isupper() and len(line) > 5:
            data["vendor"] = line
            break

    if not data["vendor"]:
        for line in lines[:8]:
            if "hotel" in line.lower():
                data["vendor"] = line
                break

    # ==============================
# 2️⃣ ADVANCED INVOICE DETECTION
# ==============================

    invoice_patterns = [
        r"invoice\s*(no|number)?\s*[:\-]?\s*([A-Za-z0-9\-\/]+)",
        r"bill\s*(no|number)?\s*[:\-]?\s*([A-Za-z0-9\-\/]+)",
        r"receipt\s*(no|number)?\s*[:\-]?\s*([A-Za-z0-9\-\/]+)",
        r"ref\s*(no|number)?\s*[:\-]?\s*([A-Za-z0-9\-\/]+)",
        r"\bINV[-\/]?[A-Za-z0-9\-\/]+\b",
        r"\b[A-Z]{2,5}[-]?\d{3,}\b"   # TI-2024, AB1234, etc.
    ]

    for pattern in invoice_patterns:
        matches = re.findall(pattern, text, re.I)

        if matches:
            for match in matches:
                if isinstance(match, tuple):
                    candidate = match[-1]
                else:
                    candidate = match

            # Avoid capturing GST numbers
                if "gst" in candidate.lower():
                    continue

                if len(candidate) >= 3:
                    data["invoice_no"] = candidate.strip()
                    break

        if data["invoice_no"]:
            break
        # Fallback: choose alphanumeric code near top
        if not data["invoice_no"]:
            for line in lines[:8]:
                if any(word in line.lower() for word in ["invoice", "bill", "receipt"]):
                    possible = re.findall(r"[A-Za-z0-9\-\/]{4,}", line)
                    if possible:
                        data["invoice_no"] = possible[-1]
                        break

    # ==============================
   # ==============================
# 3️⃣ STRONG DATE DETECTION
# ==============================

    date_patterns = [
        r"\b\d{2}\s+[A-Za-z]{3,9}\s+\d{4}\b",
        r"\b[A-Za-z]{3,9}\s+\d{1,2},\s*\d{4}\b",
        r"\b\d{2}[/-]\d{2}[/-]\d{4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b"
    ]

    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            data["bill_date"] = match.group()
            break
    
   # ==============================
# 4️⃣ STABLE HYBRID TOTAL LOGIC
# ==============================

    def clean_amount(value):
        value = value.replace(",", "")
        value = value.replace("O", "0").replace("o", "0")
        return value

    total_value = None

# 1️⃣ Priority: Look for GRAND TOTAL keyword
    for line in lines:
        if "grand" in line.lower() and "total" in line.lower():
            match = re.search(r"[\d,]+\.\d{2}", line)
            if match:
                total_value = clean_amount(match.group())
                break

# 2️⃣ If not found, look for TOTAL but NOT subtotal
    if not total_value:
        for line in lines:
            l = line.lower()
            if "total" in l and "subtotal" not in l:
                match = re.search(r"[\d,]+\.\d{2}", line)
                if match:
                    total_value = clean_amount(match.group())
                    break

# 3️⃣ If still not found, fallback to largest value
    if not total_value:
        amounts = []
        for line in lines:
            matches = re.findall(r"[\d,]+\.\d{2}", line)
            for match in matches:
                try:
                    amounts.append(float(clean_amount(match)))
                except:
                    continue

        if amounts:
            total_value = str(max(amounts))

    data["total_amount"] = total_value

    # ==============================
    # 5️⃣ ITEMS (Detect price pairs)
    # ==============================

    for line in lines:
        match = re.search(r"(.+?)\s+(\d+\.\d{2})$", line)

        if match:
            name = match.group(1).strip()
            price = match.group(2)

            if not any(x in name.lower() for x in ["total", "tax", "gst"]):
                data["items"].append({
                    "name": name,
                    "price": price
                })

    # Remove duplicates
    seen = set()
    unique = []

    for item in data["items"]:
        key = (item["name"], item["price"])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    data["items"] = unique

    return data

def ai_extract_fields(ocr_text):

    prompt = f"""
You are a receipt parser AI.

Extract the following fields from the receipt text:

vendor
invoice_no
bill_date
total_amount

Return STRICT JSON only.
If a field does not exist, return null.

Receipt Text:
{ocr_text}
"""

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": "You extract structured JSON from receipts."},
                {"role": "user", "content": prompt}
            ],
            temperature=0,
        )

        content = response.choices[0].message.content.strip()

        # Remove markdown if present
        content = content.replace("```json", "").replace("```", "").strip()

        # Extract JSON safely
        start = content.find("{")
        end = content.rfind("}") + 1

        if start != -1 and end != -1:
            return json.loads(content[start:end])

        print("No valid JSON found in Groq response")
        return None

    except Exception as e:
        print("Groq AI Error:", e)
        return None

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = ""
SMTP_PASSWORD = ""

def send_otp_email(email, otp):
    msg = f"""Subject: Password Reset OTP

Your OTP for password reset is: {otp}
Valid for 10 minutes.
"""
    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
    server.starttls()
    server.login(SMTP_USERNAME, SMTP_PASSWORD)
    server.sendmail(SMTP_USERNAME, email, msg)
    server.quit()
# ================== IMAGE QUALITY ==================
def is_blurry(path):
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    return cv2.Laplacian(img, cv2.CV_64F).var() < 100

def is_too_dark_or_bright(path):
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    m = np.mean(img)
    return m < 40 or m > 220

def is_low_resolution(path):
    w, h = Image.open(path).size
    return w < 600 or h < 600


def run_ocr(path):
    try:
        results = ocr_engine.readtext(path)

        rows = {}

        for (bbox, text, confidence) in results:
            if confidence < 0.35:
                continue

            # Use Y position rounded to group same row text
            y = int(bbox[0][1] // 10)  # group by 10px blocks

            if y not in rows:
                rows[y] = []

            rows[y].append((bbox[0][0], text))  # store x position too

        # Sort rows by Y
        sorted_rows = sorted(rows.items())

        reconstructed_lines = []

        for _, row in sorted_rows:
            # sort words left to right
            row = sorted(row, key=lambda x: x[0])
            line = " ".join([word for _, word in row])
            reconstructed_lines.append(line)

        return "\n".join(reconstructed_lines)

    except Exception as e:
        print("OCR Error:", e)
        return ""


# ================== FILE SERVE ==================
@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ================== AUTH ==================
@app.route("/")
def landing():
    return render_template("landing.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        conn = get_db()
        user = conn.execute(
            "SELECT password,is_verified FROM users WHERE email=?", (email,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user[0], password):
            if user[1] == 0:
                flash("Email not verified", "error")
            else:
                token = create_jwt(email)
                resp = make_response(render_template(
                    "login.html",
                    login_success=True   # ✅ GREEN SIGNAL PRESERVED
                ))
                resp.set_cookie("token", token, httponly=True)
                return resp
        else:
            flash("Invalid login", "error")  # ✅ RED SIGNAL PRESERVED

    return render_template("login.html")

@app.route("/google-login")
def google_login():
    if not google.authorized:
        return redirect(url_for("google.login"))

    resp = google.get("/oauth2/v2/userinfo")
    if not resp.ok:
        flash("Google login failed", "error")
        return redirect(url_for("login"))

    user_info = resp.json()

    email = user_info.get("email")
    name = user_info.get("name")
    picture = user_info.get("picture")  # ✅ profile image URL

    if not email:
        flash("Could not fetch email", "error")
        return redirect(url_for("login"))

    conn = get_db()
    conn.execute(
        "INSERT OR IGNORE INTO users (email,is_verified) VALUES (?,1)",
        (email,)
    )
    conn.commit()
    conn.close()

    resp = make_response(redirect(url_for("dashboard")))

    # Store profile picture in cookie (simple way)
    resp.set_cookie("token", create_jwt(email), httponly=True, samesite="Lax")
    resp.set_cookie("profile_pic", picture or "", samesite="Lax")

    return resp

@app.route("/logout")
def logout():
    resp = make_response(redirect(url_for("login")))
    resp.delete_cookie("token")
    return resp

# ================== DASHBOARD ==================
@app.route("/dashboard")
def dashboard():
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    profile_pic = request.cookies.get("profile_pic")
    user_name = email.split("@")[0].capitalize()
    current_year = datetime.datetime.utcnow().year

    conn = get_db()

    # Existing stats
    total_docs = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE user_email=?", (email,)
    ).fetchone()[0]

    month = datetime.datetime.utcnow().strftime("%Y-%m")
    month_docs = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE user_email=? AND substr(uploaded_at,1,7)=?",
        (email, month)
    ).fetchone()[0]

    # ================= AI SECTION =================

    # Total Spending This Year
    total_spend = conn.execute("""
        SELECT SUM(CAST(total_amount AS REAL))
        FROM documents
        WHERE user_email=?
        AND substr(uploaded_at,1,4)=?
    """,(email,str(current_year))).fetchone()[0] or 0

    # Monthly Spending
    monthly_data = conn.execute("""
    SELECT substr(uploaded_at,6,2),
           COALESCE(SUM(CAST(total_amount AS REAL)), 0)
    FROM documents
    WHERE user_email=? 
    AND substr(uploaded_at,1,4)=?
    GROUP BY substr(uploaded_at,6,2)
""",(email,str(current_year))).fetchall()

    monthly_values = []

    for row in monthly_data:
        value = row[1]
        if value is not None:
            monthly_values.append(float(value))

    highest_month_value = max(monthly_values) if monthly_values else 0.0

    # Top Vendor
    top_vendor_data = conn.execute("""
        SELECT vendor, SUM(CAST(total_amount AS REAL)) as total
        FROM documents
        WHERE user_email=?
        GROUP BY vendor
        ORDER BY total DESC
        LIMIT 1
    """,(email,)).fetchone()

    top_vendor = top_vendor_data[0] if top_vendor_data else "N/A"

    # Average Monthly Spending
    avg_monthly = round(total_spend / 12, 2) if total_spend else 0

    # Simple Prediction (5% increase assumption)
    predicted_next = round(avg_monthly * 1.05, 2)

    conn.close()

    return render_template(
        "dashboard.html",
        total_docs=total_docs,
        month_docs=month_docs,
        profile_pic=profile_pic,
        total_spend=round(total_spend,2),
        avg_monthly=avg_monthly,
        highest_month_value=round(highest_month_value,2),
        top_vendor=top_vendor,
        predicted_next=predicted_next,
        user_name=user_name
    )

@app.route("/recent")
def recent():
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    conn = get_db()

    rows = conn.execute("""
        SELECT 
            stored_filename,
            vendor,
            uploaded_at,
            is_edited,
            latitude,
            longitude
        FROM documents
        WHERE user_email=?
        ORDER BY uploaded_at DESC
        LIMIT 20
    """, (email,)).fetchall()

    conn.close()

    return render_template("recent.html", documents=rows)

@app.route("/search", methods=["GET", "POST"])
def search():
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    if request.method == "POST":
        search_date = request.form.get("date")
    else:
        search_date = request.args.get("date")

    conn = get_db()

    if search_date:
        rows = conn.execute("""
            SELECT *
            FROM documents
            WHERE user_email=?
            AND substr(uploaded_at, 1, 10)=?
        """, (email, search_date)).fetchall()
    else:
        rows = conn.execute("""
            SELECT *
            FROM documents
            WHERE user_email=?
        """, (email,)).fetchall()

    conn.close()

    return render_template("search.html", rows=rows)

# ================== TRENDS ==================
@app.route("/trends")
def trends():
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    # Get selected year (default = current year)
    selected_year = request.args.get("year")
    current_year = datetime.datetime.utcnow().year

    try:
        year = int(selected_year) if selected_year else current_year
    except:
        year = current_year

    # Only allow current year and past 2 years
    allowed_years = [current_year, current_year - 1, current_year - 2]
    if year not in allowed_years:
        year = current_year

    conn = get_db()

    # Fetch monthly document counts
    rows = conn.execute("""
        SELECT 
            substr(uploaded_at, 6, 2) as month,
            COUNT(*)
        FROM documents
        WHERE user_email = ?
        AND substr(uploaded_at,1,4) = ?
        GROUP BY month
    """, (email, str(year))).fetchall()

    conn.close()

    # Prepare 12 months default = 0
    month_counts = {f"{i:02d}": 0 for i in range(1, 13)}

    for month, count in rows:
        month_counts[month] = count

    labels = [
        "Jan","Feb","Mar","Apr","May","Jun",
        "Jul","Aug","Sep","Oct","Nov","Dec"
    ]

    values = list(month_counts.values())

    return render_template(
        "trends.html",
        labels=labels,
        values=values,
        year=year,
        allowed_years=allowed_years
    )

# ================== DOCUMENTS BY VENDOR ==================
@app.route("/documents-by-category")
def documents_by_category():
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    conn = get_db()

    rows = conn.execute("""
        SELECT vendor, COUNT(*)
        FROM documents
        WHERE user_email=? AND vendor IS NOT NULL
        GROUP BY vendor
        ORDER BY COUNT(*) DESC
    """, (email,)).fetchall()

    conn.close()

    labels = [row[0] for row in rows]
    values = [row[1] for row in rows]

    return render_template(
        "documents_by_category.html",
        labels=labels,
        values=values
    )

# ================== UPLOAD ==================
@app.route("/upload", methods=["GET","POST"])
def upload():
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    if request.method == "POST":
        file = request.files.get("file")
        doc_type = request.form.get("doc_type","Unknown")

        if not file or not allowed_file(file.filename):
            flash("Invalid file", "error")
            return redirect(url_for("upload"))

        stored = secure_filename(
            f"{email}_{int(datetime.datetime.utcnow().timestamp())}_{file.filename}"
        )
        path = os.path.join(UPLOAD_FOLDER, stored)
        file.save(path)

        latitude = request.form.get("latitude")
        longitude = request.form.get("longitude")
        print("FORM LAT:", request.form.get("latitude"))
        print("FORM LNG:", request.form.get("longitude"))

        if is_blurry(path) or is_too_dark_or_bright(path) or is_low_resolution(path):
            os.remove(path)
            flash("Image quality issue", "error")
            return redirect(url_for("upload"))

        with open(path,"rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()

        conn = get_db()
        duplicate = conn.execute(
            "SELECT id FROM documents WHERE file_hash=? AND user_email=?",
            (file_hash,email)
        ).fetchone()

        if duplicate:
            conn.close()
            return render_template(
                "confirm_duplicate.html",
                filename=stored,
                original_filename=file.filename,
                doc_type=doc_type
            )

        conn.execute("""
            INSERT INTO documents
            (user_email,stored_filename,original_filename,doc_type,uploaded_at,file_hash,latitude,longitude)
            VALUES (?,?,?,?,?,?,?,?)
        """,(email,stored,file.filename,doc_type,
            datetime.datetime.utcnow().isoformat(),
            file_hash,
            latitude,
            longitude))
        conn.commit()
        conn.close()

        return redirect(url_for("process_ocr", filename=stored))

    return render_template("upload.html")

@app.route("/confirm-duplicate", methods=["POST"])
def confirm_duplicate():
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    filename = request.form["filename"]
    doc_type = request.form["doc_type"]
    action = request.form["action"]

    path = os.path.join(UPLOAD_FOLDER, filename)

    if action == "cancel":
        if os.path.exists(path):
            os.remove(path)
        flash("Upload cancelled", "info")
        return redirect(url_for("upload"))

    # If user chooses continue
    with open(path,"rb") as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()

    conn = get_db()
    conn.execute("""
        INSERT INTO documents
        (user_email,stored_filename,original_filename,doc_type,uploaded_at,file_hash)
        VALUES (?,?,?,?,?,?)
    """,(email,filename,filename,doc_type,
         datetime.datetime.utcnow().isoformat(),file_hash))

    conn.commit()
    conn.close()

    return redirect(url_for("process_ocr", filename=filename))

# ================== OCR ==================
@app.route("/process-ocr/<filename>")
def process_ocr(filename):
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    file_path = os.path.join(UPLOAD_FOLDER, filename)
    raw_text = run_ocr(file_path)

    if not raw_text.strip():
        flash("OCR failed", "error")
        return redirect(url_for("dashboard"))

    parsed = extract_receipt_fields(raw_text)

    if not parsed["vendor"] or not parsed["total_amount"]:
        ai_data = ai_extract_fields(raw_text)
        if ai_data:
            parsed["vendor"] = parsed["vendor"] or ai_data.get("vendor")
            parsed["invoice_no"] = parsed["invoice_no"] or ai_data.get("invoice_no")
            parsed["bill_date"] = parsed["bill_date"] or ai_data.get("bill_date")
            parsed["total_amount"] = parsed["total_amount"] or ai_data.get("total_amount")

    # ✅ SAVE TO DATABASE
    conn = get_db()
    conn.execute("""
        UPDATE documents
        SET
            ocr_text=?,
            vendor=?,
            invoice_no=?,
            bill_date=?,
            total_amount=?,
            items=?
        WHERE stored_filename=? AND user_email=?
    """, (
        raw_text,
        parsed["vendor"],
        parsed["invoice_no"],
        parsed["bill_date"],
        parsed["total_amount"],
        json.dumps(parsed["items"]),
        filename,
        email
    ))
    conn.commit()
    conn.close()

    # ✅ REDIRECT TO RESULT PAGE
    return redirect(url_for("ocr_result", filename=filename))

@app.route("/save-ocr/<filename>", methods=["POST"])
def save_ocr(filename):
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    vendor = request.form.get("vendor")
    invoice_no = request.form.get("invoice_no")
    bill_date = request.form.get("bill_date")
    total_amount = request.form.get("total_amount")

    # Items (simple comma-separated for now)
    items_raw = request.form.get("items")

    items = []
    if items_raw:
        lines = items_raw.split("\n")
        for line in lines:
            parts = line.split(",")
            if len(parts) == 2:
                items.append({
                    "name": parts[0].strip(),
                    "price": parts[1].strip()
                })

    conn = get_db()
    conn.execute("""
        UPDATE documents
        SET
            vendor=?,
            invoice_no=?,
            bill_date=?,
            total_amount=?,
            items=?,
            is_edited=1
        WHERE stored_filename=? AND user_email=?
    """, (
        vendor,
        invoice_no,
        bill_date,
        total_amount,
        json.dumps(items),
        filename,
        email
    ))
    conn.commit()
    conn.close()

    flash("OCR data saved successfully", "success")
    return redirect(url_for("ocr_result", filename=filename))

@app.route("/ocr-result/<filename>")
def ocr_result(filename):
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    conn = get_db()
    row = conn.execute("""
        SELECT 
            vendor,
            invoice_no,
            bill_date,
            total_amount,
            items,
            ocr_text,
            is_edited,
            uploaded_at,
            latitude,
            longitude
        FROM documents
        WHERE stored_filename=? AND user_email=?
    """, (filename, email)).fetchone()
    conn.close()

    if not row:
        flash("OCR data not found", "error")
        return redirect(url_for("dashboard"))

    vendor, invoice_no, bill_date, total_amount, items_json, ocr_text, is_edited, uploaded_at, latitude, longitude = row

    import json
    items = json.loads(items_json) if items_json else []

    return render_template(
        "ocr_result.html",
        filename=filename,
        vendor=vendor,
        invoice_no=invoice_no,
        bill_date=bill_date,
        total_amount=total_amount,
        items=items,
        ocr_text=ocr_text,
        is_edited=is_edited,
        uploaded_at=uploaded_at,
        latitude=latitude,
        longitude=longitude
    )
# ================== DOWNLOAD ==================
@app.route("/download/<filename>")
def download(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


def clean_text_for_pdf(text):
    replacements = {
        "’": "'",
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "₹": "Rs."
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


@app.route("/download-ocr/pdf/<filename>")
def download_ocr_pdf(filename):
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    conn = get_db()
    row = conn.execute("""
        SELECT vendor, invoice_no, bill_date, total_amount, items
        FROM documents
        WHERE stored_filename=? AND user_email=?
    """, (filename, email)).fetchone()
    conn.close()

    if not row:
        flash("OCR data not found", "error")
        return redirect(url_for("dashboard"))

    vendor, invoice_no, bill_date, total_amount, items_json = row
    import json
    items = json.loads(items_json) if items_json else []

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Arial", size=12)

    pdf.cell(0, 10, "Extracted Receipt Data", ln=True)
    pdf.ln(4)

    pdf.cell(0, 8, f"Vendor: {vendor or '-'}", ln=True)
    pdf.cell(0, 8, f"Invoice No: {invoice_no or '-'}", ln=True)
    pdf.cell(0, 8, f"Date: {bill_date or '-'}", ln=True)
    pdf.cell(0, 8, f"Total: Rs. {total_amount or '-'}", ln=True)

    if items:
        pdf.ln(5)
        pdf.cell(0, 8, "Items:", ln=True)
        for item in items:
            pdf.cell(0, 8, f"- {item['name']} : Rs. {item['price']}", ln=True)

    return Response(
        pdf.output(dest="S").encode("latin-1", errors="ignore"),
        mimetype="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=extracted_{filename}.pdf"
        }
    )

import random

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        hashed = generate_password_hash(password)

        otp = str(random.randint(100000, 999999))
        expiry = (datetime.datetime.utcnow() + datetime.timedelta(minutes=10)).isoformat()

        try:
            conn = get_db()
            conn.execute("""
                INSERT INTO users (email,password,is_verified,reset_otp,otp_expiry)
                VALUES (?,?,?,?,?)
            """, (email, hashed, 0, otp, expiry))

            conn.commit()
            conn.close()

            send_otp_email(email, otp)

            flash("Verification OTP sent to your email", "success")
            return redirect(url_for("verify_email", email=email))

        except sqlite3.IntegrityError:
            flash("Email already registered", "error")

    return render_template("register.html")

@app.route("/verify-email", methods=["GET","POST"])
def verify_email():
    email = request.args.get("email")
    if not email:
        return redirect(url_for("login"))

    if request.method == "POST":
        entered_otp = request.form["otp"]

        conn = get_db()
        row = conn.execute("""
            SELECT reset_otp, otp_expiry
            FROM users WHERE email=?
        """, (email,)).fetchone()

        if not row:
            conn.close()
            flash("Invalid request", "error")
            return redirect(url_for("login"))

        stored_otp, expiry = row

        # 🔥 CRITICAL FIX
        if not stored_otp or not expiry:
            conn.close()
            flash("OTP not found. Please register again.", "error")
            return redirect(url_for("register"))

        if stored_otp != entered_otp:
            conn.close()
            flash("Invalid OTP", "error")
            return redirect(url_for("verify_email", email=email))

        try:
            expiry_time = datetime.datetime.fromisoformat(expiry)
        except Exception:
            conn.close()
            flash("Invalid expiry format. Please register again.", "error")
            return redirect(url_for("register"))

        if datetime.datetime.utcnow() > expiry_time:
            conn.close()
            flash("OTP expired", "error")
            return redirect(url_for("register"))

        # Mark verified
        conn.execute("""
            UPDATE users
            SET is_verified=1, reset_otp=NULL, otp_expiry=NULL
            WHERE email=?
        """, (email,))
        conn.commit()
        conn.close()

        flash("Email verified successfully. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("verify_email.html", email=email)

@app.route("/forgot-password", methods=["GET","POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form["email"]

        conn = get_db()
        user = conn.execute(
            "SELECT id FROM users WHERE email=?",
            (email,)
        ).fetchone()

        if not user:
            conn.close()
            flash("Email not registered", "error")
            return redirect(url_for("forgot_password"))

        otp = str(random.randint(100000, 999999))
        expiry = (datetime.datetime.utcnow() + datetime.timedelta(minutes=10)).isoformat()

        conn.execute("""
            UPDATE users
            SET reset_otp=?, otp_expiry=?
            WHERE email=?
        """, (otp, expiry, email))
        conn.commit()
        conn.close()

        send_otp_email(email, otp)
        flash("OTP sent to your email", "success")
        return redirect(url_for("reset_password", email=email))

    return render_template("forgot_password.html")

@app.route("/reset-password", methods=["GET","POST"])
def reset_password():
    email = request.args.get("email")
    if not email:
        return redirect(url_for("login"))

    if request.method == "POST":
        otp = request.form["otp"]
        new_password = request.form["password"]

        conn = get_db()
        row = conn.execute("""
            SELECT reset_otp, otp_expiry
            FROM users WHERE email=?
        """, (email,)).fetchone()

        if not row:
            conn.close()
            flash("Invalid request", "error")
            return redirect(url_for("login"))

        stored_otp, expiry = row
        if stored_otp != otp:
            conn.close()
            flash("Invalid OTP", "error")
            return redirect(url_for("reset_password", email=email))

        if datetime.datetime.utcnow() > datetime.datetime.fromisoformat(expiry):
            conn.close()
            flash("OTP expired", "error")
            return redirect(url_for("forgot_password"))

        hashed = generate_password_hash(new_password)

        conn.execute("""
            UPDATE users
            SET password=?, reset_otp=NULL, otp_expiry=NULL
            WHERE email=?
        """, (hashed, email))
        conn.commit()
        conn.close()

        flash("Password reset successful. Please login.", "success")
        return redirect(url_for("login"))

    return render_template("reset_password.html", email=email)

@app.route("/resend-otp")
def resend_otp():
    email = request.args.get("email")
    if not email:
        return redirect(url_for("login"))

    conn = get_db()
    user = conn.execute(
        "SELECT id FROM users WHERE email=?", (email,)
    ).fetchone()

    if not user:
        conn.close()
        flash("User not found", "error")
        return redirect(url_for("register"))

    # Generate new OTP
    import random
    otp = str(random.randint(100000, 999999))
    expiry = (datetime.datetime.utcnow() + datetime.timedelta(minutes=10)).isoformat()

    conn.execute("""
        UPDATE users
        SET reset_otp=?, otp_expiry=?
        WHERE email=?
    """, (otp, expiry, email))
    conn.commit()
    conn.close()

    send_otp_email(email, otp)

    flash("New OTP sent to your email", "success")
    return redirect(url_for("verify_email", email=email))

@app.route("/edit-ocr/<filename>")
def edit_ocr(filename):
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    conn = get_db()
    row = conn.execute("""
        SELECT vendor, invoice_no, bill_date, total_amount, items
        FROM documents
        WHERE stored_filename=? AND user_email=?
    """, (filename, email)).fetchone()
    conn.close()

    if not row:
        flash("Data not found", "error")
        return redirect(url_for("dashboard"))

    vendor, invoice_no, bill_date, total_amount, items_json = row

    import json
    items = json.loads(items_json) if items_json else []

    return render_template(
        "edit_ocr.html",
        filename=filename,
        vendor=vendor,
        invoice_no=invoice_no,
        bill_date=bill_date,
        total_amount=total_amount,
        items=items
    )

# ================== RUN ==================
if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)