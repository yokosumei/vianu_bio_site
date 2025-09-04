import os
import uuid
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, jsonify, flash
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# --------- CONTURI CU ACCES LA "LECTII" ---------
ALLOWED_LESSONS = {"admin@vianubio", "membriiaccount"}

# -------------------- APP & CONFIG --------------------
app = Flask(__name__, static_url_path="/static", static_folder="static")
app.secret_key = os.getenv("SECRET_KEY", "dev-secret")  # schimbă în producție

# DB: Postgres (DATABASE_URL) sau fallback la SQLite pt. dev
db_url = os.getenv("DATABASE_URL", "sqlite:///local.db").replace("postgres://", "postgresql://")
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# CĂI ABSOLUTE (EVITĂ PROBLEME DE CWD)
STATIC_DIR  = os.path.join(app.root_path, "static")
IMAGES_DIR  = os.path.join(STATIC_DIR, "images")   # pentru About + assets în repo
UPLOADS_DIR = os.path.join(STATIC_DIR, "uploads")  # pentru uploaduri din admin
DATA_DIR    = os.path.join(STATIC_DIR, "data")     # team.json

os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOADS_DIR

# Extensii acceptate
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "svg"}

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
    image_url = db.Column(db.String(512))                # stocăm numele fișierului (uploads) sau URL extern
    external_url = db.Column(db.String(512))             # ex: link Instagram / extern
    ppt_url = db.Column(db.String(512))                  # link prezentare (Google Slides / PDF)
    author = db.Column(db.String(128), default="Club BIO")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# -------------------- INIT DB + MIGRĂRI LITE --------------------
_tables_ready = False

def _ensure_users():
    """Creează/actualizează cele 2 conturi cerute."""
    wanted = {"admin@vianubio": "parola123", "membriiaccount": "weluvbio"}
    for email, pwd in wanted.items():
        u = User.query.filter_by(email=email).first()
        if not u:
            db.session.add(User(email=email, password_hash=generate_password_hash(pwd)))
    db.session.commit()

def _ensure_columns():
    """Adaugă coloana 'ppt_url' în Post dacă lipsește (ignora eroarea dacă există deja)."""
    try:
        with db.engine.begin() as con:
            con.execute(text("ALTER TABLE post ADD COLUMN ppt_url VARCHAR(512);"))
    except Exception:
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
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper

def can_view_lessons():
    user = session.get("user_email")
    return user in ALLOWED_LESSONS

def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def _unique_filename(original_name: str) -> str:
    """Generează un nume unic prietenos: <uuid8>-<secure_basename>.<ext>"""
    base = secure_filename(original_name)
    name, ext = os.path.splitext(base)
    uid = uuid.uuid4().hex[:8]
    return f"{uid}-{name}{ext.lower()}"

def _url_for_static(subpath: str) -> str:
    return url_for("static", filename=subpath)

def resolve_about_photo(name_or_url: str) -> str:
    """
    Regula de rezolvare pentru ABOUT (membri) — FOTO din team.json (static/data/team.json):
      - dacă e http(s) -> URL extern
      - dacă începe cu /static/ -> folosește-l ca atare
      - altfel tratează ca nume fișier: caută în static/images/, apoi fallback static/uploads/
      - dacă nu îl găsește -> static/images/placeholder.jpg
    """
    placeholder_rel = "images/placeholder.jpg"
    if not name_or_url:
        return _url_for_static(placeholder_rel)

    s = name_or_url.strip()
    if s.startswith("http://") or s.startswith("https://"):
        return s
    if s.startswith("/static/"):
        return s

    img_path = os.path.join(IMAGES_DIR, s)
    if os.path.isfile(img_path):
        return _url_for_static(f"images/{s}")

    up_path = os.path.join(UPLOADS_DIR, s)
    if os.path.isfile(up_path):
        return _url_for_static(f"uploads/{s}")

    return _url_for_static(placeholder_rel)

def resolve_post_cover(image_url: str) -> str:
    """
    Regula pentru cover-ul postării (Blog):
      - http(s) sau /static/... -> direct
      - altfel, dacă e nume de fișier în uploads -> /static/uploads/<nume>
      - altfel -> placeholder
    """
    placeholder_rel = "images/placeholder.jpg"
    if image_url:
        s = image_url.strip()
        if s.startswith("http://") or s.startswith("https://") or s.startswith("/static/"):
            return s
        up = os.path.join(UPLOADS_DIR, s)
        if os.path.isfile(up):
            return _url_for_static(f"uploads/{s}")
    return _url_for_static(placeholder_rel)

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

    # atașează cover_src pentru template (imaginea postării)
    for p in posts:
        p.cover_src = resolve_post_cover(p.image_url)

    return render_template("blog.html", posts=posts, can_view_lessons=can_view_lessons())

# -------------------- ABOUT --------------------
@app.get("/about", endpoint="about")
def about():
    """
    Citește echipa din static/data/tea m.json (conform cerinței).
    Pentru fiecare membru setează m['photo_src'] cu regulile robuste.
    """
    import json

    team = []
    data_path = os.path.join(DATA_DIR, "team.json")  # <<< static/data/team.json
    if os.path.exists(data_path):
        try:
            with open(data_path, "r", encoding="utf-8") as f:
                team = json.load(f)
        except Exception as e:
            print("Eroare la citirea team.json:", e)

    for m in team:
        photo = (m.get("photo") or "").strip()
        m["photo_src"] = resolve_about_photo(photo)

    return render_template("about.html", team=team)

# -------------------- API PUBLICE --------------------
@app.get("/api/posts")
def api_posts():
    q = Post.query
    if not can_view_lessons():
        q = q.filter(Post.section != "lectii")
    posts = q.order_by(Post.created_at.desc()).all()

    payload = []
    for p in posts:
        payload.append(dict(
            id=p.id, section=p.section, title=p.title, content=p.content,
            image_url=p.image_url, external_url=p.external_url, ppt_url=p.ppt_url,
            author=p.author, created_at=p.created_at.isoformat(),
            cover_src=resolve_post_cover(p.image_url),
        ))
    return jsonify(payload)

@app.get("/health")
def health():
    return {
        "ok": True,
        "root": app.root_path,
        "static_dir": STATIC_DIR,
        "images_dir": IMAGES_DIR,
        "uploads_dir": UPLOADS_DIR,
        "data_dir": DATA_DIR,
        "images_present": sorted(os.listdir(IMAGES_DIR)) if os.path.isdir(IMAGES_DIR) else [],
        "uploads_present": sorted(os.listdir(UPLOADS_DIR)) if os.path.isdir(UPLOADS_DIR) else [],
        "team_json_exists": os.path.isfile(os.path.join(DATA_DIR, "team.json")),
        "static_placeholder_exists": os.path.isfile(os.path.join(IMAGES_DIR, "placeholder.jpg")),
        "static_route_example": url_for("static", filename="images/placeholder.jpg")
    }

# -------------------- AUTH --------------------
@app.get("/login", endpoint="login")
def login_page():
    return render_template("login.html")

@app.post("/login")
def login_action():
    email = request.form.get("email", "").strip()
    pwd = request.form.get("password", "")
    u = User.query.filter_by(email=email).first()
    if not u or not check_password_hash(u.password_hash, pwd):
        flash("Credențiale invalide", "error")
        return redirect(url_for("login"))
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

    # Dacă utilizatorul NU are drept de 'lectii', blochează
    if section == "lectii" and not can_view_lessons():
        flash("Nu ai drepturi să publici în 'Lecții'.", "error")
        return redirect(url_for("admin_new"))

    # ---- Upload imagine cover (opțional) ----
    image_url = None
    f = request.files.get("image")
    if f and f.filename:
        if allowed_file(f.filename):
            fname = _unique_filename(f.filename)
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], fname)
            f.save(save_path)
            # stocăm DOAR numele (mai flexibil); pentru afișare folosim resolve_post_cover()
            image_url = fname
        else:
            flash("Format imagine neacceptat (folosește png/jpg/jpeg/gif/webp/svg).", "error")

    p = Post(
        section=section, title=title, content=content,
        image_url=image_url, external_url=external_url,
        ppt_url=ppt_url, author=author
    )
    db.session.add(p)
    db.session.commit()

    flash("Postare publicată.", "success")
    return redirect(url_for("blog"))

# -------------------- MAIN --------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
