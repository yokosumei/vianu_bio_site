import os
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# --------- CONTURI CU ACCES LA "LECTII" ---------
ALLOWED_LESSONS = {"admin@vianubio", "membriiaccount"}

# -------------------- APP & CONFIG --------------------
app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")  # schimbă în producție

# DB: Postgres (DATABASE_URL) sau fallback la SQLite pt. dev
db_url = os.getenv("DATABASE_URL", "sqlite:///local.db").replace("postgres://", "postgresql://")
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

UPLOAD_FOLDER = os.path.join("static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

db = SQLAlchemy(app)

# -------------------- MODELE --------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    section = db.Column(db.String(32), nullable=False)   # 'insta' | 'articles' | 'gallery' | 'lectii'
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, default="")
    image_url = db.Column(db.String(512))                # cover opțional
    external_url = db.Column(db.String(512))             # ex: link Instagram / extern
    ppt_url = db.Column(db.String(512))                  # link prezentare (Google Slides / PPT public)
    author = db.Column(db.String(128), default="Club BIO")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -------------------- INIT DB + MIGRĂRI LITE --------------------
_tables_ready = False

def _ensure_users():
    """Creează/actualizează cele 2 conturi cerute."""
    wanted = {
        "admin@vianubio": "parola123",
        "membriiaccount": "weluvbio",
    }
    for email, pwd in wanted.items():
        u = User.query.filter_by(email=email).first()
        if not u:
            db.session.add(User(email=email, password_hash=generate_password_hash(pwd)))
    db.session.commit()

def _ensure_columns():
    """
    Adaugă coloana 'ppt_url' în Post dacă lipsește.
    Funcționează pentru SQLite și Postgres (ignoră eroarea dacă deja există).
    """
    try:
        # SQLite ACCEPTĂ ALTER simplu; Postgres la fel, dacă nu există deja.
        with db.engine.begin() as con:
            con.execute(text("ALTER TABLE post ADD COLUMN ppt_url VARCHAR(512);"))
    except Exception:
        # Presupunem că există deja coloana sau altă eroare non-critică.
        pass

def _init_db_once():
    """Creează tabelele + utilizatorii + migrează coloanele o singură dată."""
    global _tables_ready
    if _tables_ready:
        return
    db.create_all()
    _ensure_columns()
    _ensure_users()
    _tables_ready = True

# pornire
with app.app_context():
    try:
        _init_db_once()
    except Exception as e:
        print("DB init will retry on first request:", e)

@app.before_request
def _maybe_init_db():
    global _tables_ready
    if not _tables_ready:
        try:
            _init_db_once()
        except Exception as e:
            print("DB init retry failed:", e)

# -------------------- HELPERS --------------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login_page"))
        return fn(*args, **kwargs)
    return wrapper

def can_view_lessons():
    user = session.get("user_email")
    return user in ALLOWED_LESSONS

# -------------------- ROUTE-URI PUBLICE --------------------
@app.get("/")
def index():
    return render_template("index.html")

@app.get("/blog")
def blog():
    # Publicul NU vede secțiunea 'lectii'
    if not can_view_lessons():
        posts = (
            Post.query
            .filter(Post.section != "lectii")
            .order_by(Post.created_at.desc())
            .limit(100)
            .all()
        )
    else:
        posts = Post.query.order_by(Post.created_at.desc()).limit(100).all()
    return render_template("blog.html", posts=posts, can_view_lessons=can_view_lessons())

@app.get("/api/posts")
def api_posts():
    q = Post.query
    if not can_view_lessons():
        q = q.filter(Post.section != "lectii")
    posts = q.order_by(Post.created_at.desc()).all()
    return jsonify([
        dict(
            id=p.id, section=p.section, title=p.title, content=p.content,
            image_url=p.image_url, external_url=p.external_url, ppt_url=p.ppt_url,
            author=p.author, created_at=p.created_at.isoformat()
        ) for p in posts
    ])

@app.get("/health")
def health():
    return {"ok": True}

# -------------------- AUTH --------------------
@app.get("/login")
def login_page():
    return render_template("login.html")

@app.post("/login")
def login_action():
    email = request.form.get("email", "").strip()
    pwd = request.form.get("password", "")
    u = User.query.filter_by(email=email).first()
    if not u or not check_password_hash(u.password_hash, pwd):
        flash("Credențiale invalide", "error")
        return redirect(url_for("login_page"))
    session["logged_in"] = True
    session["user_email"] = u.email
    flash("Autentificat cu succes.", "success")
    return redirect(url_for("admin_new"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# -------------------- ADMIN --------------------
@app.get("/admin/new")
@login_required
def admin_new():
    # trimitem și un flag ca să afișezi opțiunea 'lectii' doar pentru userii cu drepturi
    return render_template("admin_new.html", can_view_lessons=can_view_lessons())

@app.post("/admin/new")
@login_required
def admin_new_post():
    section = request.form.get("section", "articles").strip().lower()
    title = request.form.get("title", "Untitled").strip()
    content = request.form.get("content", "").strip()
    external_url = request.form.get("external_url", "").strip()
    ppt_url = request.form.get("ppt_url", "").strip()
    author = session.get("user_email", "Admin")

    # Dacă utilizatorul NU are drept de 'lectii', forțăm secțiunea să nu fie 'lectii'
    if section == "lectii" and not can_view_lessons():
        flash("Nu ai drepturi să publici în 'Lecții'.", "error")
        return redirect(url_for("admin_new"))

    image_url = None
    f = request.files.get("image")
    if f and f.filename:
        fname = secure_filename(f.filename)
        path = os.path.join(app.config["UPLOAD_FOLDER"], fname)
        f.save(path)
        image_url = url_for("static", filename=f"uploads/{fname}")

    p = Post(
        section=section, title=title, content=content,
        image_url=image_url, external_url=external_url,
        ppt_url=ppt_url,
        author=author
    )
    db.session.add(p)
    db.session.commit()

    flash("Postare publicată.", "success")
    return redirect(url_for("blog"))

# -------------------- MAIN --------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
