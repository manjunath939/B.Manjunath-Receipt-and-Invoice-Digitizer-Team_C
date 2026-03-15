
# Receipt Digitizer

AI-powered receipt & invoice digitization with OCR, structured data extraction via Gemini LLM, and an interactive analytics dashboard.

## Prerequisites

- Python 3.8+
- Tesseract OCR (system binary)
- Google Gemini API key

## Setup Instructions

### 1. Install Tesseract OCR

**Windows:**
1. Download the Tesseract installer from [GitHub - UB Mannheim](https://github.com/UB-Mannheim/tesseract/wiki)
2. Run the installer (e.g., `tesseract-ocr-w64-setup-v5.x.x.exe`)
3. During installation, note the install path (default: `C:\Program Files\Tesseract-OCR`)
4. Add to Windows PATH or configure in `app.py`

**macOS (Homebrew):**
```bash
brew install tesseract
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt-get install tesseract-ocr
```

### 2. Verify Tesseract Installation

```powershell
tesseract --version
```

If not in PATH, add to `app.py` after imports:
```python
import pytesseract
pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
```

### 3. Install Python Dependencies

```powershell
# Activate virtual environment
& .\venv\Scripts\Activate.ps1

# Install packages
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Set these in PowerShell or a `.env` file:

```powershell
$env:GEMINI_API_KEY = "your-gemini-api-key-here"
$env:FLASK_SECRET = "your-random-secret-key"
$env:ADMIN_CODE = "a-secret-code-optional"
```

Get your Gemini API key from [Google AI Studio](https://makersuite.google.com/app/apikey). The `ADMIN_CODE` value is what a person must enter on the registration form to become an administrator; it can be left blank if you don't need self-service admin creation.

### 5. Run the Application

```powershell
& .\venv\Scripts\Activate.ps1
python app.py
```

Visit: `http://localhost:5000`

## Usage

### User Flow
1. **Register**: Create a new account with email & password. (Optional `Admin Code` field exists if you have a special code.)
2. **Login**: Sign in with credentials or use Google OAuth.
3. **Upload**: Drag & drop or click to upload receipt/invoice image.
4. **Extract**: OCR reads text, Gemini extracts structured data (or fallback regex when API is unavailable).
5. **View**: Dashboard shows stats, charts, and receipt details.

### Admin Flow
- Access the **Admin Login** page at `/admin_login` or click the button on the login screen.
- Only users marked as administrators (via the admin code during registration or manually added in the DB) can log in.
- The admin dashboard displays:
  - Total registered users and number of admins
  - Count of currently active (logged-in) users
  - A table with detailed information for each user (ID, email, admin status, last login, receipt count)
  - Active rows are highlighted for users who are presently logged in.
- Admins can log out using the logout button, which ends their session just like a normal user.

## Features

- **OCR**: Tesseract extracts text from receipt images
- **LLM Processing**: Google Gemini extracts structured JSON (merchant, amount, items, tax, etc.)
- **Dashboard**: Real-time stats, top merchants, spending by category
- **Receipt Details**: Full breakdown with items, tax, discount, payment method
- **Modern UI**: Glassmorphism design with dark professional theme

## Database

SQLite (`database.db`) with three tables:
- `users` — user accounts
- `receipts` — receipt metadata & extracted data
- `receipt_items` — individual items from each receipt

## Troubleshooting

**"OCR Error: tesseract is not installed"**
- Ensure Tesseract binary is installed and in PATH
- Or set `pytesseract.pytesseract.tesseract_cmd` in `app.py`

**"Gemini API key not configured"**
- Set `GEMINI_API_KEY` environment variable before running
- Get free API key from [Google AI Studio](https://makersuite.google.com/app/apikey)

## Project Structure

```
reciept/
├── app.py                 # Flask backend with OCR, Gemini, DB
├── database.db           # SQLite database
├── requirements.txt      # Python dependencies
├── static/
│   └── receipts/        # Uploaded receipt images
└── templates/
    ├── login.html       # Login page
    ├── register.html    # Registration page
    └── dashboard.html   # Analytics dashboard
```

## License

MIT
=======
# Pranavi_Reciept_and_Invoice_Generator_Team-C

