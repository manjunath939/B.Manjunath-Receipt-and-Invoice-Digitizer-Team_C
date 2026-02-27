from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, make_response, send_from_directory, Response
)
from dotenv import load_dotenv
load_dotenv()
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_dance.contrib.google import make_google_blueprint, google
import sqlite3, jwt, datetime, os, hashlib, csv
from io import StringIO

import cv2
import numpy as np
from PIL import Image
import pytesseract
from fpdf import FPDF
import smtplib

# ================== CONFIG ==================
app = Flask(__name__)
app.secret_key = os.getenv("flask-secret-key","dev")
JWT_SECRET = os.getenv("jwt-secret-key","dev")
JWT_ALGO = "HS256"

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
            is_verified INTEGER DEFAULT 1,
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
            items TEXT
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

    # ===================== VENDOR =====================
    for line in lines[:8]:
        if not re.search(r"\d{3,}", line) and len(line) > 3:
            data["vendor"] = line[:80]
            break

    # ===================== INVOICE =====================
    invoice_patterns = [
        r"(invoice\s*(no|number)?[:\-]?\s*)([A-Za-z0-9\-\/]+)",
        r"(bill\s*(no|number)?[:\-]?\s*)([A-Za-z0-9\-\/]+)",
        r"(inv\s*#?[:\-]?\s*)([A-Za-z0-9\-\/]+)"
    ]

    for pattern in invoice_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            data["invoice_no"] = match.groups()[-1]
            break

    # ===================== DATE =====================
    date_patterns = [
        r"\b\d{2}[/-]\d{2}[/-]\d{4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\b\d{2}\s+[A-Za-z]{3,9}\s+\d{4}\b"
    ]

    for pattern in date_patterns:
        match = re.search(pattern, text)
        if match:
            data["bill_date"] = match.group()
            break

    # ===================== TOTAL =====================
    total_patterns = [
        r"(grand\s*total[:\-]?\s*₹?\s*)([\d,]+\.\d{2})",
        r"(total\s*amount[:\-]?\s*₹?\s*)([\d,]+\.\d{2})",
        r"(total[:\-]?\s*₹?\s*)([\d,]+\.\d{2})"
    ]

    for pattern in total_patterns:
        matches = re.findall(pattern, text, re.I)
        if matches:
            data["total_amount"] = matches[-1][1].replace(",", "")
            break


    # ===================== ITEMS =====================
    item_pattern = re.compile(
        r"([A-Za-z\s]+)\s+\d+\s*x\s*\d+\.\d{2}\s+(\d+\.\d{2})"
    )

    for line in lines:
        match = item_pattern.search(line)
        if match:
            name = match.group(1).strip()
            price = match.group(2)
            data["items"].append({
                "name": name,
                "price": price
            })

    return data

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = os.getenv("SMTP_USERNAME")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

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

def preprocess_image(path):
    img = cv2.imread(path)

    # Convert to grayscale
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Increase contrast
    gray = cv2.normalize(gray, None, alpha=0, beta=255,
                          norm_type=cv2.NORM_MINMAX)

    # Remove noise
    gray = cv2.GaussianBlur(gray, (5,5), 0)

    # Adaptive threshold (best for receipts)
    thresh = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31, 2
    )

    return thresh

def run_ocr(path):
    img = cv2.imread(path)

    # Convert to grayscale ONLY
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Do NOT blur
    # Do NOT adaptive threshold
    # Do NOT normalize

    custom_config = r'''
        --oem 3
        --psm 6
    '''
    processed=preprocess_image(path)
    text = pytesseract.image_to_string(
        gray,
        config=custom_config
    )

    return text.strip()

from spellchecker import SpellChecker

spell = SpellChecker()

def correct_spelling(text):
    corrected_lines = []

    for line in text.split("\n"):
        words = line.split()
        corrected_words = []

        for word in words:
            # Skip numbers, invoices, amounts
            if any(c.isdigit() for c in word) or len(word)<=3:
                corrected_words.append(word)
            else:
                corrected_words.append(spell.correction(word) or word)

        corrected_lines.append(" ".join(corrected_words))

    return "\n".join(corrected_lines)
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

    conn = get_db()
    total_docs = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE user_email=?", (email,)
    ).fetchone()[0]

    month = datetime.datetime.utcnow().strftime("%Y-%m")
    month_docs = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE user_email=? AND substr(uploaded_at,1,7)=?",
        (email, month)
    ).fetchone()[0]
    conn.close()

    return render_template(
        "dashboard.html",
        total_docs=total_docs,
        month_docs=month_docs,
        profile_pic=profile_pic   # ✅ pass to template
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

        if is_blurry(path) or is_too_dark_or_bright(path) or is_low_resolution(path):
            os.remove(path)
            flash("Image quality issue", "error")
            return redirect(url_for("upload"))

        with open(path,"rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()

        conn = get_db()
        if conn.execute(
            "SELECT id FROM documents WHERE file_hash=? AND user_email=?",
            (file_hash,email)
        ).fetchone():
            conn.close()
            os.remove(path)
            flash("Duplicate document detected", "error")
            return redirect(url_for("upload"))

        conn.execute("""
            INSERT INTO documents
            (user_email,stored_filename,original_filename,doc_type,uploaded_at,file_hash)
            VALUES (?,?,?,?,?,?)
        """,(email,stored,file.filename,doc_type,
             datetime.datetime.utcnow().isoformat(),file_hash))
        conn.commit()
        conn.close()

        return redirect(url_for("process_ocr", filename=stored))

    return render_template("upload.html")

# ================== OCR ==================
@app.route("/process-ocr/<filename>")
def process_ocr(filename):
    email = verify_jwt(request.cookies.get("token"))
    if not email:
        return redirect(url_for("login"))

    file_path = os.path.join(UPLOAD_FOLDER, filename)
    if not os.path.exists(file_path):
        flash("File not found", "error")
        return redirect(url_for("dashboard"))

    # 1️⃣ OCR
    raw_text = run_ocr(file_path)
    if not raw_text.strip():
        flash("OCR failed", "error")
        return redirect(url_for("dashboard"))

    parsed = extract_receipt_fields(raw_text)
    clean_text = raw_text

    # 4️⃣ SAVE EVERYTHING
    conn = get_db()
    conn.execute("""
        UPDATE documents
        SET
            ocr_text = ?,
            vendor = ?,
            invoice_no = ?,
            bill_date = ?,
            total_amount = ?,
            items = ?
        WHERE stored_filename = ? AND user_email = ?
    """, (
        clean_text,
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
            ocr_text
        FROM documents
        WHERE stored_filename=? AND user_email=?
    """, (filename, email)).fetchone()
    conn.close()

    if not row:
        flash("OCR data not found", "error")
        return redirect(url_for("dashboard"))

    vendor, invoice_no, bill_date, total_amount, items_json, ocr_text = row

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
        ocr_text=ocr_text
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

@app.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]

        hashed = generate_password_hash(password)

        try:
            conn = get_db()
            conn.execute(
                "INSERT INTO users (email,password,is_verified) VALUES (?,?,1)",
                (email, hashed)
            )
            conn.commit()
            conn.close()
            flash("Registration successful. Please login.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("Email already registered")

    return render_template("register.html")

import random

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
# ================== RUN ==================
if __name__ == "__main__":
    app.run(debug=True, use_reloader=False)