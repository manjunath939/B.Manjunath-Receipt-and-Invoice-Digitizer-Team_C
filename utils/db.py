import sqlite3

def get_db():
    return sqlite3.connect("database.db")

def init_db():
    with get_db() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT, "
            "email TEXT UNIQUE, "
            "password TEXT, "
            "language TEXT, "
            "otp TEXT, "
            "is_verified INTEGER DEFAULT 0"
            ")"
        )


    conn.commit()
    conn.close()
