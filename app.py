import os
from datetime import datetime
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

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
    section = db.Column(db.String(32), nullable=False)   # 'insta' | 'articles' | 'gallery'
    title = db.Column(db.String(255), nullable=False)
    content = db.Column(db.Text, default="")
    image_url = db.Column(db.String(512))                # pentru galerie / cover
    external_url = db.Column(db.String(512))             # ex: link Instagram
    author = db.Column(db.String(128), default="Club BIO")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -------------------- INIT DB (compatibil Flask 3) --------------------
_tables_ready = False

def _init_db_once():
    """Creează tabelele și userul demo, o singură dată."""
    global _tables_ready
    if _tables_ready:
        return
    db.create_all()
    if not User.query.filter_by(email="admin@vianubio").first():
        db.session.add(User(email="admin@vianubio",
                            password_hash=generate_password_hash("parola123")))
        db.session.commit()
    _tables_ready = True

# 1) încercăm la pornire
with app.app_context():
    try:
        _init_db_once()
    except Exception as e:
        # dacă DB nu e gata, mai încercăm la primul request
        print("DB init will retry on first request:", e)

# 2) fallback: dacă a eșuat la start, mai încercăm la primul request
@app.before_request
def _maybe_init_db():
    global _tables_ready
    if not _tables_ready:
        try:
            _init_db_once()
        except Exception as e:
            # nu blocăm requestul; doar logăm și continuăm
            print("DB init retry failed:", e)

# -------------------- HELPERS --------------------
def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login_page"))
        return fn(*args, **kwargs)
    return wrapper

# -------------------- ROUTE-URI PUBLICE --------------------
@app.get("/")
def index():
    return render_template("index.html")

@app.get("/blog")
def blog():
    posts = Post.query.order_by(Post.created_at.desc()).limit(50).all()
    return render_template("blog.html", posts=posts)

@app.get("/api/posts")
def api_posts():
    posts = Post.query.order_by(Post.created_at.desc()).all()
    return jsonify([
        dict(
            id=p.id, section=p.section, title=p.title, content=p.content,
            image_url=p.image_url, external_url=p.external_url,
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
    return redirect(url_for("admin_new"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

# -------------------- ADMIN --------------------
@app.get("/admin/new")
@login_required
def admin_new():
    return render_template("admin_new.html")

@app.post("/admin/new")
@login_required
def admin_new_post():
    section = request.form.get("section", "articles")
    title = request.form.get("title", "Untitled")
    content = request.form.get("content", "")
    external_url = request.form.get("external_url", "").strip()
    author = session.get("user_email", "Admin")

    image_url = None
    f = request.files.get("image")
    if f and f.filename:
        fname = secure_filename(f.filename)
        path = os.path.join(app.config["UPLOAD_FOLDER"], fname)
        f.save(path)
        image_url = url_for("static", filename=f"uploads/{fname}")

    p = Post(section=section, title=title, content=content,
             image_url=image_url, external_url=external_url, author=author)
    db.session.add(p)
    db.session.commit()
    return redirect(url_for("blog"))

# -------------------- MAIN --------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
