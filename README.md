Features

• User Authentication
– Register / Login
– Google OAuth Login
– Forgot Password with OTP (Email)

• Document Upload
– Supports JPG, PNG, JPEG, PDF
– Image quality checks (blur, brightness, resolution)
– Duplicate file detection using hash

• OCR Processing
– OpenCV image preprocessing
– Tesseract OCR
– Spell correction for better accuracy
– Extracts:
- Vendor
- Invoice / Bill Number
- Date
- Total Amount
- Items (if present)

• Preview & Download
– Original document preview
– Extracted information preview
– Download original file
– Download extracted PDF

• Dashboard
– Total uploaded documents
– Monthly uploads count

🛠 Tech Stack

Backend: Python, Flask
Database: SQLite
OCR: Tesseract, pytesseract
Image Processing: OpenCV, Pillow
PDF: FPDF
Auth: JWT, Google OAuth
Email OTP: SMTP (Gmail)
Spell Correction: pyspellchecker

📂 Project Structure

document-digitizer/
├── app.py
├── database.db
├── uploads/
├── templates/
│ ├── landing.html
│ ├── login.html
│ ├── register.html
│ ├── forgot_password.html
│ ├── reset_password.html
│ ├── dashboard.html
│ ├── upload.html
│ └── ocr_result.html
├── static/
│ └── css/
│ └── auth.css
├── requirements.txt
└── README.md

⚙️ Requirements

• Python 3.9 – 3.12
• Tesseract OCR installed on system

🔧 Installation & Run
1. Create Virtual Environment

python -m venv .venv
source .venv/bin/activate (macOS/Linux)
.venv\Scripts\activate (Windows)

2. Install Dependencies

pip install -r requirements.txt

3. Install Tesseract OCR

macOS:
brew install tesseract

Ubuntu/Linux:
sudo apt update
sudo apt install tesseract-ocr

Windows:
Download from
https://github.com/UB-Mannheim/tesseract/wiki

Add Tesseract to PATH

Verify:
tesseract --version

4. Run the Application

python app.py

5. Open in Browser

http://127.0.0.1:5000

📧 Email OTP Setup

Edit in app.py:

SMTP_USERNAME = "your_email@gmail.com
"
SMTP_PASSWORD = "your_gmail_app_password"

Notes:
• Enable 2-Step Verification
• Generate Gmail App Password
• Do NOT use normal Gmail password

🔑 Google Login Setup

Go to Google Cloud Console

Create OAuth 2.0 Client ID

Add Redirect URI:

http://127.0.0.1:5000/login/google/authorized

Update in app.py:

client_id = "YOUR_CLIENT_ID"
client_secret = "YOUR_CLIENT_SECRET"
