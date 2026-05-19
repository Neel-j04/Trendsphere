"""
app.py  ─  TrendSphere v3  (Complete with ML Trend Prediction)
Full feature upgrade with working trend predictions
"""

import os, uuid, random, string, smtplib, httpx, json, csv, io
from datetime import datetime, timedelta
from functools import wraps
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from werkzeug.utils import secure_filename

from flask import (Flask, render_template, request, redirect,
                   url_for, session, jsonify, flash, abort, make_response, send_file)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

from db.database import fetchall, fetchone, execute, execute_returning, init_pool

# Import ML predictor
import sys
sys.path.insert(0, os.path.dirname(__file__))
import ml.predictor as ml_predictor

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "trendsphere_secret_2024_change_me")

# Upload folders
UPLOAD_PRODUCTS = os.path.join(os.path.dirname(__file__), "static", "uploads", "products")
UPLOAD_AVATARS = os.path.join(os.path.dirname(__file__), "static", "uploads", "avatars")
UPLOAD_RETURNS = os.path.join(os.path.dirname(__file__), "static", "uploads", "returns")
UPLOAD_INVOICES = os.path.join(os.path.dirname(__file__), "static", "invoices")

for folder in [UPLOAD_PRODUCTS, UPLOAD_AVATARS, UPLOAD_RETURNS, UPLOAD_INVOICES]:
    os.makedirs(folder, exist_ok=True)

ALLOWED_IMG = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_IMGS = 5

def _allowed(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMG

def _save_file(file_obj, folder):
    if not file_obj or not file_obj.filename:
        return None
    if not _allowed(file_obj.filename):
        return None
    ext = file_obj.filename.rsplit(".", 1)[1].lower()
    fn = f"{uuid.uuid4().hex}.{ext}"
    file_obj.save(os.path.join(folder, fn))
    rel = folder.split("static")[-1].replace("\\", "/")
    return f"/static{rel}/{fn}"

def _save_image(file_obj):
    return _save_file(file_obj, UPLOAD_PRODUCTS)

# Email config
MAIL_HOST = os.getenv("MAIL_HOST", "")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
MAIL_USER = os.getenv("MAIL_USER", "")
MAIL_PASS = os.getenv("MAIL_PASS", "")
MAIL_FROM = os.getenv("MAIL_FROM", "noreply@trendsphere.com")

def _send_email(to, subject, body, attachment_path=None):
    if not MAIL_HOST or not MAIL_USER:
        app.logger.info(f"[DEV EMAIL] To:{to} Sub:{subject}\n{body}")
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = MAIL_FROM
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "html"))
        if attachment_path and os.path.exists(attachment_path):
            with open(attachment_path, "rb") as f:
                part = MIMEBase("application", "octet-stream")
                part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(attachment_path)}"')
                msg.attach(part)
        with smtplib.SMTP(MAIL_HOST, MAIL_PORT) as s:
            s.starttls()
            s.login(MAIL_USER, MAIL_PASS)
            s.send_message(msg)
        return True
    except Exception as e:
        app.logger.error(f"Email error: {e}")
        return False

def _generate_otp():
    return "".join(random.choices(string.digits, k=6))

def _save_otp(email, otp, purpose="reset"):
    execute("DELETE FROM otp_tokens WHERE email=%s AND purpose=%s", (email, purpose))
    execute("INSERT INTO otp_tokens (email,otp,purpose,expires_at) VALUES (%s,%s,%s,NOW()+INTERVAL '10 minutes')",
            (email, otp, purpose))

def _verify_otp(email, otp, purpose="reset"):
    row = fetchone("SELECT id FROM otp_tokens WHERE email=%s AND otp=%s AND purpose=%s AND used=FALSE AND expires_at>NOW()",
                   (email, otp, purpose))
    if row:
        execute("UPDATE otp_tokens SET used=TRUE WHERE id=%s", (row["id"],))
        return True
    return False

def _otp_email_body(otp, purpose="verify"):
    action = {"verify":"verify your email","reset":"reset your password","login":"complete login"}.get(purpose,"continue")
    return f"""
<div style="font-family:Arial,sans-serif;max-width:500px;margin:0 auto;padding:24px;border:1px solid #eee;border-radius:12px">
  <div style="text-align:center;margin-bottom:24px">
    <h2 style="color:#FF6F00;margin:0">🔮 TrendSphere</h2>
  </div>
  <h3>Your OTP to {action}</h3>
  <div style="font-size:36px;font-weight:900;letter-spacing:10px;text-align:center;padding:20px;background:#FFF8F0;border-radius:8px;color:#FF6F00;margin:20px 0">{otp}</div>
  <p style="color:#666">This OTP is valid for <strong>10 minutes</strong>. Do not share it with anyone.</p>
  <hr style="border:none;border-top:1px solid #eee;margin:20px 0">
  <p style="color:#999;font-size:12px;text-align:center">— TrendSphere Team</p>
</div>"""

def _hash(p):
    return generate_password_hash(p, method="pbkdf2:sha256:600000")

def _verify(p, h):
    return check_password_hash(h, p)

# ML / Trend helpers
ML_API_URL = os.getenv("ML_API_URL", "http://localhost:8001")
ML_HEADERS = {"X-API-Key": os.getenv("ML_API_KEY","demo-key-123"),"Content-Type":"application/json"}

def _build_signals(product_ids):
    """Build product signals from behavior data for ML prediction"""
    if not product_ids:
        return []
    ids = ",".join(str(i) for i in product_ids)
    rows = fetchall(f"""
        SELECT product_id,
          SUM(CASE WHEN event_ts >= NOW()-INTERVAL '7 days' THEN views ELSE 0 END) vl,
          SUM(CASE WHEN event_ts < NOW()-INTERVAL '7 days' THEN views ELSE 0 END) vp,
          SUM(CASE WHEN event_ts >= NOW()-INTERVAL '7 days' THEN search ELSE 0 END) sl,
          SUM(CASE WHEN event_ts < NOW()-INTERVAL '7 days' THEN search ELSE 0 END) sp,
          SUM(CASE WHEN event_ts >= NOW()-INTERVAL '7 days' THEN wishlist ELSE 0 END) wl,
          SUM(CASE WHEN event_ts < NOW()-INTERVAL '7 days' THEN wishlist ELSE 0 END) wp,
          SUM(CASE WHEN event_ts >= NOW()-INTERVAL '7 days' THEN cart ELSE 0 END) cl,
          SUM(CASE WHEN event_ts < NOW()-INTERVAL '7 days' THEN cart ELSE 0 END) cp,
          SUM(purchase) pu
        FROM behavior_events WHERE product_id IN ({ids}) GROUP BY product_id
    """)
    meta = {r["id"]: r for r in fetchall(f"SELECT id,avg_rating,price FROM products WHERE id IN ({ids})")}
    return [{
        "product_id": str(r["product_id"]),
        "category": "general",
        "views_last_7d": int(r["vl"] or 0),
        "views_prev_7d": int(r["vp"] or 0),
        "searches_last_7d": int(r["sl"] or 0),
        "searches_prev_7d": int(r["sp"] or 0),
        "wishlist_last_7d": int(r["wl"] or 0),
        "wishlist_prev_7d": int(r["wp"] or 0),
        "cart_last_7d": int(r["cl"] or 0),
        "cart_prev_7d": int(r["cp"] or 0),
        "purchases_last_7d": int(r["pu"] or 0),
        "avg_rating": float((meta.get(r["product_id"]) or {}).get("avg_rating") or 0),
        "price": float((meta.get(r["product_id"]) or {}).get("price") or 0),
    } for r in rows]

def _save_predictions(preds):
    """Save ML predictions to database"""
    if not preds: 
        return
    for p in preds:
        try:
            execute("""
                INSERT INTO trend_predictions
                  (product_id, trend_score, trend_status, confidence, 
                   view_velocity, search_momentum, wishlist_signal, 
                   cart_intent, anomaly, forecast_7d, predicted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (product_id) DO UPDATE SET
                  trend_score = EXCLUDED.trend_score,
                  trend_status = EXCLUDED.trend_status,
                  confidence = EXCLUDED.confidence,
                  view_velocity = EXCLUDED.view_velocity,
                  search_momentum = EXCLUDED.search_momentum,
                  wishlist_signal = EXCLUDED.wishlist_signal,
                  cart_intent = EXCLUDED.cart_intent,
                  anomaly = EXCLUDED.anomaly,
                  forecast_7d = EXCLUDED.forecast_7d,
                  predicted_at = NOW()
            """, (
                int(p["product_id"]), p.get("trend_score", 0), p.get("trend_status", "stable"),
                p.get("confidence", 0), p.get("view_velocity", 0), p.get("search_momentum", 0),
                p.get("wishlist_signal", 0), p.get("cart_intent", 0),
                p.get("anomaly_detected", False), p.get("forecast_7d", 0)
            ))
        except Exception as e:
            app.logger.error(f"Error saving prediction: {e}")

DELIVERY_STAGES = [
    ("Order Placed", 0),
    ("Packing", 2),
    ("Shipped", 4),
    ("Out for Delivery", 6),
    ("Delivered", 8),
]

def _get_tracking_status(order):
    placed = order.get("placed_at")
    if not placed:
        return "Order Placed"
    hours = (datetime.now() - placed).total_seconds() / 3600
    status = "Order Placed"
    for stage, h in DELIVERY_STAGES:
        if hours >= h:
            status = stage
    return status

# Jinja helpers
def fmt_price(p):
    try:
        return f"₹{float(p):,.0f}"
    except:
        return "₹0"

def get_cart_count():
    return sum(i["qty"] for i in session.get("cart", []))

app.jinja_env.globals.update(
    get_cart_count=get_cart_count,
    fmt_price=fmt_price,
    session=session,
    now=datetime.now,
)

# Auth decorators
def login_required(f):
    @wraps(f)
    def d(*a, **kw):
        if "user_id" not in session:
            flash("Please log in.", "warning")
            return redirect(url_for("login"))
        return f(*a, **kw)
    return d

def admin_required(f):
    @wraps(f)
    def d(*a, **kw):
        if session.get("role") != "admin":
            flash("Admin access required.", "danger")
            return redirect(url_for("admin_login"))
        return f(*a, **kw)
    return d

# Startup
@app.before_request
def startup():
    if not hasattr(app, "_db_ready"):
        try:
            init_pool()
            app._db_ready = True
        except Exception as e:
            app.logger.error(f"DB init failed: {e}")
    _track_visit()

def _track_visit():
    skip = ("/static", "/api/", "/favicon")
    if any(request.path.startswith(s) for s in skip):
        return
    try:
        execute("""INSERT INTO page_visits (path, user_id, session_id, ip_address, referrer, user_agent, visited_at)
                   VALUES (%s, %s, %s, %s, %s, %s, NOW())""",
                (request.path[:255], session.get("user_id"), session.get("session_id"),
                 request.remote_addr,
                 request.referrer[:500] if request.referrer else None,
                 request.user_agent.string[:500] if request.user_agent else None))
    except Exception:
        pass

# ============================================================
# PUBLIC / CUSTOMER ROUTES
# ============================================================

@app.route("/")
def home():
    trending = fetchall("""
        SELECT p.id, p.name, p.brand, p.price, p.was_price, p.avg_rating,
               p.review_count, p.badge, p.emoji, p.stock, c.name AS category,
               (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
        FROM products p 
        JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = TRUE 
        ORDER BY p.review_count DESC 
        LIMIT 8
    """)
    
    deals = fetchall("""
        SELECT p.id, p.name, p.brand, p.price, p.was_price, p.avg_rating,
               p.review_count, p.badge, p.emoji, c.name AS category,
               (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
        FROM products p 
        JOIN categories c ON c.id = p.category_id
        WHERE p.badge = 'hot' AND p.is_active = TRUE 
        LIMIT 6
    """)
    
    # Get trend predictions
    trend_products = fetchall("""
        SELECT p.name, c.name AS category, tp.trend_score, tp.trend_status
        FROM trend_predictions tp
        JOIN products p ON p.id = tp.product_id
        JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = TRUE
        ORDER BY tp.trend_score DESC 
        LIMIT 5
    """)
    
    # Monthly purchase trend
    monthly_be = fetchall("""
        SELECT COALESCE(SUM(purchase), 0) AS purchases
        FROM behavior_events
        WHERE event_ts >= NOW() - INTERVAL '12 months'
        GROUP BY DATE_TRUNC('month', event_ts)
        ORDER BY DATE_TRUNC('month', event_ts)
    """)
    monthly_vals = [int(r["purchases"]) for r in monthly_be] or [0]*12
    
    # Category demand
    cat_demand = fetchall("""
        SELECT c.name AS cat, COALESCE(SUM(be.purchase), 0) AS pct
        FROM categories c
        LEFT JOIN products p ON p.category_id = c.id
        LEFT JOIN behavior_events be ON be.product_id = p.id
        GROUP BY c.name 
        ORDER BY pct DESC 
        LIMIT 5
    """)
    
    trend_data = {
        "trending": [{"rank": i+1, "name": r["name"], "category": r["category"],
                      "change": f"+{int(r['trend_score']*200)}%",
                      "forecast": "High" if r["trend_score"] > 0.6 else "Medium"}
                     for i, r in enumerate(trend_products)] or [
            {"rank": 1, "name": "Wireless Earbuds", "category": "Electronics", "change": "+247%", "forecast": "High"},
            {"rank": 2, "name": "Air Fryers", "category": "Kitchen", "change": "+189%", "forecast": "High"},
        ],
        "monthly_sales": monthly_vals,
        "category_share": [{"cat": r["cat"], "pct": int(r["pct"])} for r in cat_demand] or [
            {"cat": "Electronics", "pct": 38}, {"cat": "Fashion", "pct": 24},
        ],
    }
    
    return render_template("home.html", trending=trending, deals=deals, trend_data=trend_data)

@app.route("/products")
def products():
    cat = request.args.get("cat", "")
    search = request.args.get("q", "")
    sort = request.args.get("sort", "popular")
    min_price = request.args.get("min_price", "")
    max_price = request.args.get("max_price", "")
    min_rating = request.args.get("min_rating", "")
    avail = request.args.get("avail", "")

    where, params = ["p.is_active = TRUE"], []
    if cat:
        where.append("c.name ILIKE %s")
        params.append(cat)
    if search:
        where.append("(p.name ILIKE %s OR p.brand ILIKE %s)")
        params += [f"%{search}%", f"%{search}%"]
    if min_price:
        where.append("p.price >= %s")
        params.append(float(min_price))
    if max_price:
        where.append("p.price <= %s")
        params.append(float(max_price))
    if min_rating:
        where.append("p.avg_rating >= %s")
        params.append(float(min_rating))
    if avail == "instock":
        where.append("p.stock > 0")

    order_map = {
        "price_asc": "p.price ASC",
        "price_desc": "p.price DESC",
        "rating": "p.avg_rating DESC",
        "newest": "p.created_at DESC"
    }
    order = order_map.get(sort, "p.review_count DESC")

    prods = fetchall(f"""
        SELECT p.id, p.name, p.brand, p.price, p.was_price, p.avg_rating,
               p.review_count, p.badge, p.emoji, p.stock, c.name AS category,
               (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
        FROM products p 
        JOIN categories c ON c.id = p.category_id
        WHERE {" AND ".join(where)} 
        ORDER BY {order} 
        LIMIT 60
    """, params)

    categories = [r["name"] for r in fetchall("SELECT name FROM categories ORDER BY name")]
    price_range = fetchone("SELECT MIN(price) AS mn, MAX(price) AS mx FROM products WHERE is_active=TRUE")
    
    return render_template("products.html", products=prods, categories=categories,
                           active_cat=cat, search=search, sort=sort,
                           min_price=min_price, max_price=max_price,
                           min_rating=min_rating, avail=avail,
                           price_range=price_range)

@app.route("/product/<int:pid>")
def product_detail(pid):
    product = fetchone("""
        SELECT p.*, c.name AS category
        FROM products p 
        JOIN categories c ON c.id = p.category_id
        WHERE p.id = %s AND p.is_active = TRUE
    """, (pid,))
    if not product:
        return redirect(url_for("products"))
    
    related = fetchall("""
        SELECT p.id, p.name, p.price, p.avg_rating, p.badge, p.emoji,
               (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
        FROM products p
        WHERE p.category_id = (SELECT category_id FROM products WHERE id=%s)
          AND p.id != %s AND p.is_active = TRUE 
        LIMIT 4
    """, (pid, pid))
    
    prod_images = fetchall("""
        SELECT * FROM product_images WHERE product_id = %s
        ORDER BY is_primary DESC, sort_order ASC
    """, (pid,))
    
    # Get product reviews
    reviews = fetchall("""
        SELECT pr.*, u.name AS reviewer_name, u.profile_pic AS reviewer_photo
        FROM product_reviews pr 
        JOIN users u ON u.id = pr.user_id
        WHERE pr.product_id = %s AND pr.status = 'approved'
        ORDER BY pr.created_at DESC 
        LIMIT 20
    """, (pid,))
    
    rating_dist = fetchall("""
        SELECT rating, COUNT(*) AS cnt 
        FROM product_reviews 
        WHERE product_id = %s 
        GROUP BY rating 
        ORDER BY rating DESC
    """, (pid,))
    
    in_wishlist = False
    if session.get("user_id"):
        in_wishlist = bool(fetchone(
            "SELECT 1 FROM wishlists WHERE user_id = %s AND product_id = %s",
            (session["user_id"], pid)))
    
    # Log view
    try:
        execute("""
            INSERT INTO behavior_events
              (product_id, session_id, device_type, user_location, views, event_ts, hour, month, user_id)
            VALUES (%s, %s, 'web', 'unknown', 1, NOW(),
                    EXTRACT(HOUR FROM NOW())::INT, EXTRACT(MONTH FROM NOW())::INT, %s)
        """, (pid, session.get("session_id", str(uuid.uuid4())), session.get("user_id")))
    except Exception:
        pass
    
    return render_template("product_detail.html", product=product, related=related,
                           prod_images=prod_images, reviews=reviews,
                           rating_dist=rating_dist, in_wishlist=in_wishlist)

@app.route("/product/<int:pid>/review", methods=["POST"])
@login_required
def submit_review(pid):
    rating = int(request.form.get("rating", 5))
    title = request.form.get("title", "").strip()
    comment = request.form.get("comment", "").strip()
    
    execute("""
        INSERT INTO product_reviews (product_id, user_id, rating, title, comment, is_verified)
        VALUES (%s, %s, %s, %s, %s, TRUE)
        ON CONFLICT (product_id, user_id) DO UPDATE
        SET rating = EXCLUDED.rating, title = EXCLUDED.title, comment = EXCLUDED.comment
    """, (pid, session["user_id"], rating, title, comment))
    
    # Update product average rating
    avg = fetchone("SELECT AVG(rating) as avg FROM product_reviews WHERE product_id = %s", (pid,))
    if avg and avg["avg"]:
        execute("UPDATE products SET avg_rating = %s, review_count = (SELECT COUNT(*) FROM product_reviews WHERE product_id = %s) WHERE id = %s",
                (round(avg["avg"], 1), pid, pid))
    
    flash("Review submitted! Thank you for your feedback.", "success")
    return redirect(url_for("product_detail", pid=pid))

@app.route("/api/search-suggest")
def search_suggest():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])
    results = fetchall("""
        SELECT p.id, p.name, p.brand, p.price, c.name AS category,
               (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
        FROM products p 
        JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = TRUE AND (p.name ILIKE %s OR p.brand ILIKE %s)
        ORDER BY p.review_count DESC 
        LIMIT 8
    """, (f"%{q}%", f"%{q}%"))
    return jsonify([dict(r) for r in results])

@app.route("/cart")
def cart():
    items, subtotal = [], 0
    for it in session.get("cart", []):
        p = fetchone("SELECT id, name, price, emoji, stock FROM products WHERE id = %s", (it["id"],))
        if p:
            items.append({**dict(p), "qty": it["qty"]})
            subtotal += float(p["price"]) * it["qty"]
    return render_template("cart.html", cart=items, subtotal=subtotal)

@app.route("/add-to-cart/<int:pid>", methods=["POST"])
def add_to_cart(pid):
    cart = session.get("cart", [])
    ex = next((i for i in cart if i["id"] == pid), None)
    if ex:
        ex["qty"] += 1
    else:
        cart.append({"id": pid, "qty": 1})
    session["cart"] = cart
    session.modified = True
    
    try:
        execute("""
            INSERT INTO behavior_events (product_id, session_id, cart, event_ts, hour, month, user_id)
            VALUES (%s, %s, 1, NOW(), EXTRACT(HOUR FROM NOW())::INT, EXTRACT(MONTH FROM NOW())::INT, %s)
        """, (pid, session.get("session_id", str(uuid.uuid4())), session.get("user_id")))
    except Exception:
        pass
    
    return jsonify({"success": True, "count": get_cart_count()})

@app.route("/remove-from-cart/<int:pid>", methods=["POST"])
def remove_from_cart(pid):
    session["cart"] = [i for i in session.get("cart", []) if i["id"] != pid]
    session.modified = True
    return jsonify({"success": True})

@app.route("/update-cart/<int:pid>", methods=["POST"])
def update_cart(pid):
    qty = max(1, int(request.form.get("qty", 1)))
    for i in session.get("cart", []):
        if i["id"] == pid:
            i["qty"] = qty
    session.modified = True
    return redirect(url_for("cart"))

@app.route("/buy-now/<int:pid>", methods=["POST"])
@login_required
def buy_now(pid):
    session["cart"] = [{"id": pid, "qty": 1}]
    session.modified = True
    return redirect(url_for("checkout"))

@app.route("/checkout")
@login_required
def checkout():
    items = session.get("cart", [])
    if not items:
        return redirect(url_for("cart"))
    
    prods, subtotal = [], 0
    for it in items:
        p = fetchone("SELECT id, name, price, emoji FROM products WHERE id = %s", (it["id"],))
        if p:
            prods.append({**dict(p), "qty": it["qty"]})
            subtotal += float(p["price"]) * it["qty"]
    
    user = fetchone("SELECT * FROM users WHERE id = %s", (session["user_id"],))
    return render_template("checkout.html", cart=prods, subtotal=subtotal, user=user)

@app.route("/place-order", methods=["POST"])
@login_required
def place_order():
    items = session.get("cart", [])
    if not items:
        return redirect(url_for("cart"))
    
    total = 0
    order_code = f"ORD-{datetime.now().strftime('%Y%m%d%H%M%S')}-{session['user_id']}"
    shipping = request.form.get("address", "")
    payment = request.form.get("payment", "COD")
    
    order = execute_returning("""
        INSERT INTO orders (order_code, user_id, status, total_amount, shipping_address, payment_method, tracking_status)
        VALUES (%s, %s, 'Pending', 0, %s, %s, 'Order Placed') 
        RETURNING id
    """, (order_code, session["user_id"], shipping, payment))
    oid = order["id"]
    
    for item in items:
        prod = fetchone("SELECT price FROM products WHERE id = %s", (item["id"],))
        if prod:
            price = float(prod["price"])
            execute("INSERT INTO order_items (order_id, product_id, qty, unit_price) VALUES (%s, %s, %s, %s)",
                    (oid, item["id"], item["qty"], price))
            total += price * item["qty"]
            try:
                execute("""
                    INSERT INTO behavior_events (product_id, session_id, purchase, event_ts, hour, month, user_id)
                    VALUES (%s, %s, 1, NOW(), EXTRACT(HOUR FROM NOW())::INT, EXTRACT(MONTH FROM NOW())::INT, %s)
                """, (item["id"], session.get("session_id", str(uuid.uuid4())), session["user_id"]))
            except Exception:
                pass
    
    execute("UPDATE orders SET total_amount = %s WHERE id = %s", (round(total, 2), oid))
    session["cart"] = []
    session.modified = True
    session["last_order"] = order_code
    
    flash(f"Order placed successfully! Order ID: {order_code}", "success")
    return redirect(url_for("order_success"))

@app.route("/order-success")
@login_required
def order_success():
    order_code = session.pop("last_order", None)
    order = None
    if order_code:
        order = fetchone("SELECT * FROM orders WHERE order_code = %s", (order_code,))
    return render_template("order_success.html", order=order)

@app.route("/wishlist")
@login_required
def wishlist():
    items = fetchall("""
        SELECT p.id, p.name, p.price, p.was_price, p.avg_rating, p.emoji, p.badge, c.name AS category,
               (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
        FROM wishlists w
        JOIN products p ON p.id = w.product_id
        JOIN categories c ON c.id = p.category_id
        WHERE w.user_id = %s 
        ORDER BY w.added_at DESC
    """, (session["user_id"],))
    return render_template("wishlist.html", items=items)

@app.route("/wishlist/toggle/<int:pid>", methods=["POST"])
@login_required
def toggle_wishlist(pid):
    existing = fetchone("SELECT id FROM wishlists WHERE user_id = %s AND product_id = %s",
                        (session["user_id"], pid))
    if existing:
        execute("DELETE FROM wishlists WHERE user_id = %s AND product_id = %s",
                (session["user_id"], pid))
        action = "removed"
    else:
        execute("INSERT INTO wishlists (user_id, product_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (session["user_id"], pid))
        try:
            execute("""
                INSERT INTO behavior_events (product_id, session_id, wishlist, event_ts, hour, month, user_id)
                VALUES (%s, %s, 1, NOW(), EXTRACT(HOUR FROM NOW())::INT, EXTRACT(MONTH FROM NOW())::INT, %s)
            """, (pid, session.get("session_id", str(uuid.uuid4())), session["user_id"]))
        except Exception:
            pass
        action = "added"
    
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"action": action})
    return redirect(request.referrer or url_for("wishlist"))

@app.route("/search")
def search():
    q = request.args.get("q", "")
    prods = []
    if q:
        prods = fetchall("""
            SELECT p.id, p.name, p.brand, p.price, p.avg_rating, p.badge, p.emoji, c.name AS category,
                   (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
            FROM products p 
            JOIN categories c ON c.id = p.category_id
            WHERE p.is_active = TRUE AND (p.name ILIKE %s OR p.brand ILIKE %s) 
            LIMIT 30
        """, (f"%{q}%", f"%{q}%"))
    return render_template("search.html", products=prods, query=q)

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        execute("""
            INSERT INTO contact_messages (name, email, subject, message)
            VALUES (%s, %s, %s, %s)
        """, (request.form.get("name"), request.form.get("email"),
              request.form.get("subject"), request.form.get("message")))
        flash("Message sent! We'll get back to you soon.", "success")
        return redirect(url_for("contact"))
    return render_template("contact.html")

@app.route("/faq")
def faq():
    return render_template("faq.html")

@app.route("/privacy-policy")
def privacy_policy():
    return render_template("privacy_policy.html")

@app.route("/terms")
def terms():
    return render_template("terms.html")

@app.route("/trending")
def trending_products():
    hot = fetchall("""
        SELECT p.id, p.name, p.brand, p.price, p.was_price, p.avg_rating,
               p.review_count, p.badge, p.emoji, p.stock, c.name AS category,
               (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
        FROM products p 
        JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = TRUE AND p.badge = 'hot'
        ORDER BY p.review_count DESC 
        LIMIT 20
    """)
    
    rising = fetchall("""
        SELECT p.id, p.name, p.brand, p.price, p.was_price, p.avg_rating,
               p.review_count, p.badge, p.emoji, p.stock, c.name AS category,
               (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
        FROM products p 
        JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = TRUE AND p.badge = 'top'
        ORDER BY p.avg_rating DESC, p.review_count DESC 
        LIMIT 20
    """)
    
    # AI trend predictions
    ai_trending = fetchall("""
        SELECT p.id, p.name, p.brand, p.price, p.was_price, p.avg_rating,
               p.review_count, p.badge, p.emoji, p.stock, c.name AS category,
               tp.trend_score, tp.trend_status, tp.forecast_7d,
               (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
        FROM trend_predictions tp
        JOIN products p ON p.id = tp.product_id
        JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = TRUE
        ORDER BY tp.trend_score DESC 
        LIMIT 12
    """)
    
    categories = [r["name"] for r in fetchall("SELECT name FROM categories ORDER BY name")]
    
    return render_template("trending.html",
        hot=hot, rising=rising, ai_trending=ai_trending, categories=categories)

# ============================================================
# AUTHENTICATION
# ============================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("home"))
    
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pwd = request.form.get("password", "")
        
        user = fetchone("SELECT * FROM users WHERE LOWER(email) = %s AND is_active = TRUE", (email,))
        if user and _verify(pwd, user["password_hash"]):
            session.update({
                "user_id": user["id"],
                "user": user["email"],
                "name": user["name"],
                "role": user["role"],
                "session_id": str(uuid.uuid4()),
            })
            execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user["id"],))
            flash(f"Welcome back, {user['name']}! 👋", "success")
            
            if user["role"] == "admin":
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("home"))
        
        flash("Invalid email or password.", "danger")
    
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        return redirect(url_for("home"))
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pwd = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        phone = request.form.get("phone", "").strip()
        city = request.form.get("city", "").strip()
        
        errors = []
        if not name or len(name) < 2:
            errors.append("Name must be at least 2 characters.")
        if not email or "@" not in email:
            errors.append("Please enter a valid email address.")
        if len(pwd) < 8:
            errors.append("Password must be at least 8 characters.")
        if not any(c.isupper() for c in pwd):
            errors.append("Password must contain at least one uppercase letter.")
        if not any(c.isdigit() for c in pwd):
            errors.append("Password must contain at least one number.")
        if pwd != confirm:
            errors.append("Passwords do not match.")
        if fetchone("SELECT id FROM users WHERE LOWER(email) = %s", (email,)):
            errors.append("This email is already registered.")
        
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("signup.html", form={"name": name, "email": email, "phone": phone, "city": city})
        
        execute("""
            INSERT INTO users (name, email, password_hash, role, phone, city)
            VALUES (%s, %s, %s, 'customer', %s, %s)
        """, (name, email, _hash(pwd), phone or None, city or None))
        
        flash("Account created successfully! Please log in.", "success")
        return redirect(url_for("login"))
    
    return render_template("signup.html", form={})

@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("home"))

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        user = fetchone("SELECT id, name FROM users WHERE LOWER(email) = %s AND is_active = TRUE", (email,))
        if user:
            otp = _generate_otp()
            _save_otp(email, otp, "reset")
            sent = _send_email(email, "Password Reset OTP - TrendSphere", _otp_email_body(otp, "reset"))
            if not sent:
                flash(f"[DEV MODE] Your OTP is: {otp}", "info")
            else:
                flash(f"OTP sent to {email}. Check your inbox.", "success")
            session["otp_email"] = email
            return redirect(url_for("verify_otp"))
        else:
            flash("No account found with that email.", "danger")
    return render_template("forgot_password.html")

@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    email = session.get("otp_email")
    if not email:
        return redirect(url_for("forgot_password"))
    
    if request.method == "POST":
        otp = request.form.get("otp", "").strip()
        if _verify_otp(email, otp, "reset"):
            session["otp_verified"] = True
            return redirect(url_for("reset_password"))
        flash("Invalid or expired OTP. Please try again.", "danger")
    
    return render_template("verify_otp.html", email=email)

@app.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if not session.get("otp_verified"):
        return redirect(url_for("forgot_password"))
    
    email = session.get("otp_email")
    if request.method == "POST":
        pwd = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        
        errors = []
        if len(pwd) < 8:
            errors.append("Password must be at least 8 characters.")
        if not any(c.isupper() for c in pwd):
            errors.append("Password must contain at least one uppercase letter.")
        if not any(c.isdigit() for c in pwd):
            errors.append("Password must contain at least one number.")
        if pwd != confirm:
            errors.append("Passwords do not match.")
        
        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("reset_password.html")
        
        execute("UPDATE users SET password_hash = %s WHERE LOWER(email) = %s",
                (_hash(pwd), email))
        session.pop("otp_email", None)
        session.pop("otp_verified", None)
        flash("Password reset successfully! Please log in.", "success")
        return redirect(url_for("login"))
    
    return render_template("reset_password.html")

# ============================================================
# USER ACCOUNT
# ============================================================

@app.route("/account")
@login_required
def account():
    user = fetchone("SELECT * FROM users WHERE id = %s", (session["user_id"],))
    orders = fetchall("""
        SELECT o.order_code AS id, o.status, o.total_amount AS amount,
               o.placed_at::DATE AS date, o.payment_method,
               STRING_AGG(p.name, ', ') AS product,
               STRING_AGG(p.emoji, '') AS emoji
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        JOIN products p ON p.id = oi.product_id
        WHERE o.user_id = %s 
        GROUP BY o.id 
        ORDER BY o.placed_at DESC 
        LIMIT 10
    """, (session["user_id"],))
    return render_template("account.html", user=user, orders=orders)

@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = fetchone("SELECT * FROM users WHERE id = %s", (session["user_id"],))
    
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        city = request.form.get("city", "").strip()
        address = request.form.get("address", "").strip()
        
        # Handle profile picture upload
        profile_pic = request.files.get("profile_pic")
        profile_pic_url = user.get("profile_pic")
        
        if profile_pic and _allowed(profile_pic.filename):
            ext = profile_pic.filename.rsplit(".", 1)[1].lower()
            filename = f"user_{session['user_id']}_{uuid.uuid4().hex}.{ext}"
            profile_pic.save(os.path.join(UPLOAD_AVATARS, filename))
            profile_pic_url = f"/static/uploads/avatars/{filename}"
        
        execute("""
            UPDATE users SET name=%s, phone=%s, city=%s, address=%s, profile_pic=%s
            WHERE id=%s
        """, (name, phone, city, address, profile_pic_url, session["user_id"]))
        session["name"] = name
        flash("Profile updated successfully!", "success")
        return redirect(url_for("profile"))
    
    return render_template("profile.html", user=user)

@app.route("/orders")
@login_required
def orders():
    o = fetchall("""
        SELECT o.order_code AS id, o.status, o.total_amount AS amount,
               o.placed_at::DATE AS date, o.payment_method, o.tracking_status,
               STRING_AGG(p.name, ', ') AS product,
               STRING_AGG(p.emoji, '') AS emoji
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        JOIN products p ON p.id = oi.product_id
        WHERE o.user_id = %s 
        GROUP BY o.id, o.order_code, o.status, o.total_amount, o.placed_at, o.payment_method, o.tracking_status
        ORDER BY o.placed_at DESC
    """, (session["user_id"],))
    
    # Update tracking status
    for ord in o:
        if ord.get("placed_at"):
            hours = (datetime.now() - ord["placed_at"]).total_seconds() / 3600
            if hours >= 8:
                status = "Delivered"
            elif hours >= 6:
                status = "Out for Delivery"
            elif hours >= 4:
                status = "Shipped"
            elif hours >= 2:
                status = "Packing"
            else:
                status = "Order Placed"
            
            if status != ord.get("tracking_status"):
                execute("UPDATE orders SET tracking_status = %s WHERE order_code = %s", (status, ord["id"]))
                ord["tracking_status"] = status
    
    return render_template("orders.html", orders=o)

@app.route("/order/<oid>")
@login_required
def order_detail(oid):
    order = fetchone("""
        SELECT o.*, o.order_code AS id FROM orders o
        WHERE o.order_code = %s AND o.user_id = %s
    """, (oid, session["user_id"]))
    
    if not order:
        return redirect(url_for("orders"))
    
    items = fetchall("""
        SELECT p.name, p.emoji, oi.qty, oi.unit_price, oi.subtotal
        FROM order_items oi 
        JOIN products p ON p.id = oi.product_id
        WHERE oi.order_id = %s
    """, (order["id"],))
    
    # Update tracking status
    if order.get("placed_at"):
        hours = (datetime.now() - order["placed_at"]).total_seconds() / 3600
        if hours >= 8:
            tracking_status = "Delivered"
        elif hours >= 6:
            tracking_status = "Out for Delivery"
        elif hours >= 4:
            tracking_status = "Shipped"
        elif hours >= 2:
            tracking_status = "Packing"
        else:
            tracking_status = "Order Placed"
        
        if tracking_status != order.get("tracking_status"):
            execute("UPDATE orders SET tracking_status = %s WHERE id = %s", (tracking_status, order["id"]))
            order["tracking_status"] = tracking_status
    
    return render_template("order_detail.html", order=order, items=items, tracking_stages=DELIVERY_STAGES)

# ============================================================
# ADMIN ROUTES
# ============================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if session.get("role") == "admin":
        return redirect(url_for("admin_dashboard"))
    
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pwd = request.form.get("password", "")
        user = fetchone("SELECT * FROM users WHERE LOWER(email) = %s AND role = 'admin' AND is_active = TRUE", (email,))
        
        if user and _verify(pwd, user["password_hash"]):
            session.update({
                "user_id": user["id"],
                "user": user["email"],
                "name": user["name"],
                "role": "admin",
                "session_id": str(uuid.uuid4()),
            })
            execute("UPDATE users SET last_login = NOW() WHERE id = %s", (user["id"],))
            return redirect(url_for("admin_dashboard"))
        
        flash("Invalid admin credentials.", "danger")
    
    return render_template("admin_login.html")

@app.route("/admin")
@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    stats = fetchone("""
        SELECT
          (SELECT COUNT(*) FROM users WHERE role='customer' AND is_active=TRUE) AS customers,
          (SELECT COUNT(*) FROM products WHERE is_active=TRUE) AS products,
          (SELECT COUNT(*) FROM orders) AS total_orders,
          (SELECT COUNT(*) FROM orders WHERE status='Pending') AS pending_orders,
          (SELECT COALESCE(SUM(total_amount),0) FROM orders WHERE status='Delivered') AS revenue,
          (SELECT COUNT(*) FROM page_visits WHERE visited_at >= NOW()-INTERVAL '24 hours') AS visits_today
    """)
    
    daily_sales = fetchall("""
        SELECT placed_at::DATE AS day,
               COUNT(*) AS orders,
               COALESCE(SUM(total_amount),0) AS revenue
        FROM orders 
        WHERE placed_at >= NOW() - INTERVAL '7 days'
        GROUP BY placed_at::DATE 
        ORDER BY day
    """)
    
    top_products = fetchall("""
        SELECT p.name, p.emoji, c.name AS category,
               COALESCE(SUM(oi.subtotal),0) AS revenue,
               COALESCE(SUM(oi.qty),0) AS units
        FROM products p
        JOIN categories c ON c.id = p.category_id
        LEFT JOIN order_items oi ON oi.product_id = p.id
        GROUP BY p.id, p.name, p.emoji, c.name
        ORDER BY revenue DESC 
        LIMIT 5
    """)
    
    cat_split = fetchall("""
        SELECT c.name AS category, COUNT(p.id) AS count,
               COALESCE(SUM(oi.subtotal),0) AS revenue
        FROM categories c
        LEFT JOIN products p ON p.category_id = c.id
        LEFT JOIN order_items oi ON oi.product_id = p.id
        GROUP BY c.name 
        ORDER BY revenue DESC
    """)
    
    recent_orders = fetchall("""
        SELECT o.order_code AS id, o.status, o.total_amount AS amount,
               o.placed_at AS date, u.name AS customer, u.email
        FROM orders o 
        JOIN users u ON u.id = o.user_id
        ORDER BY o.placed_at DESC 
        LIMIT 8
    """)
    
    top_pages = fetchall("""
        SELECT path, COUNT(*) AS hits
        FROM page_visits 
        WHERE visited_at >= NOW() - INTERVAL '7 days'
        GROUP BY path 
        ORDER BY hits DESC 
        LIMIT 8
    """)
    
    # Get ML model status
    ml_ready = ml_predictor.is_ready()
    
    products_list = fetchall("""
        SELECT p.id, p.name, p.brand, p.price, p.avg_rating, p.stock, p.badge, p.emoji, c.name AS category
        FROM products p 
        JOIN categories c ON c.id = p.category_id
        WHERE p.is_active = TRUE 
        ORDER BY p.review_count DESC 
        LIMIT 8
    """)
    
    revenue_7d = [float(r["revenue"]) for r in daily_sales] or [0]*7
    mx = max(revenue_7d) if revenue_7d else 1
    
    trend_data = {
        "monthly_sales": revenue_7d,
        "category_share": [{"cat": r["category"], "pct": int(r["count"])} for r in cat_split[:5]],
        "trending": [],
    }
    
    return render_template("admin_dashboard.html",
        stats=stats, daily_sales=daily_sales, top_products=top_products,
        cat_split=cat_split, recent_orders=recent_orders, top_pages=top_pages,
        trend_data=trend_data, mx=mx, products=products_list, ml_ready=ml_ready)

# ============================================================
# ADMIN - PRODUCTS
# ============================================================

@app.route("/admin/products")
@admin_required
def admin_products():
    cat = request.args.get("cat", "")
    search = request.args.get("q", "")
    where, params = ["1=1"], []
    if cat:
        where.append("c.name ILIKE %s")
        params.append(cat)
    if search:
        where.append("(p.name ILIKE %s OR p.brand ILIKE %s)")
        params += [f"%{search}%", f"%{search}%"]
    
    prods = fetchall(f"""
        SELECT p.*, c.name AS category,
               (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
        FROM products p 
        JOIN categories c ON c.id = p.category_id
        WHERE {" AND ".join(where)} 
        ORDER BY p.id DESC
    """, params)
    
    categories = fetchall("SELECT name FROM categories ORDER BY name")
    return render_template("admin_products.html", products=prods,
                           categories=categories, active_cat=cat, search=search)

@app.route("/admin/products/export")
@admin_required
def admin_export_products():
    prods = fetchall("""
        SELECT p.id, p.name, p.brand, c.name AS category,
               p.price, p.was_price, p.stock, p.avg_rating, p.review_count,
               p.badge, p.is_active, p.created_at
        FROM products p 
        JOIN categories c ON c.id = p.category_id
        ORDER BY p.id
    """)
    
    si = io.StringIO()
    wr = csv.writer(si)
    wr.writerow(["ID", "Name", "Brand", "Category", "Price", "Was Price",
                 "Stock", "Rating", "Reviews", "Badge", "Active", "Created"])
    
    for p in prods:
        wr.writerow([p["id"], p["name"], p["brand"], p["category"],
                     p["price"], p["was_price"], p["stock"], p["avg_rating"], p["review_count"],
                     p["badge"], p["is_active"], str(p["created_at"])[:10]])
    
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=trendsphere_products.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route("/admin/products/add", methods=["GET", "POST"])
@admin_required
def admin_add_product():
    if request.method == "POST":
        cat = fetchone("SELECT id FROM categories WHERE name = %s", (request.form.get("category"),))
        
        new_prod = execute_returning("""
            INSERT INTO products
              (name, brand, category_id, price, was_price, description, stock, emoji, badge, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) 
            RETURNING id
        """, (
            request.form.get("name"),
            request.form.get("brand"),
            cat["id"] if cat else 1,
            float(request.form.get("price", 0) or 0),
            float(request.form.get("was_price", 0) or 0) or None,
            request.form.get("description", ""),
            int(request.form.get("stock", 0) or 0),
            request.form.get("emoji", "📦"),
            request.form.get("badge", ""),
            True,
        ))
        
        pid = new_prod["id"]
        
        # Handle image uploads
        images = request.files.getlist("images")
        primary_idx = int(request.form.get("primary_image", 0))
        saved = 0
        
        for i, img in enumerate(images[:MAX_IMGS]):
            url = _save_image(img)
            if url:
                execute("""
                    INSERT INTO product_images (product_id, url, is_primary, sort_order)
                    VALUES (%s, %s, %s, %s)
                """, (pid, url, i == primary_idx, i))
                saved += 1
        
        flash(f"Product added with {saved} image(s)!", "success")
        return redirect(url_for("admin_products"))
    
    categories = fetchall("SELECT name FROM categories ORDER BY name")
    return render_template("admin_product_form.html", product=None, categories=categories, images=[])

@app.route("/admin/products/edit/<int:pid>", methods=["GET", "POST"])
@admin_required
def admin_edit_product(pid):
    product = fetchone("""
        SELECT p.*, c.name AS category
        FROM products p 
        JOIN categories c ON c.id = p.category_id 
        WHERE p.id = %s
    """, (pid,))
    
    if not product:
        abort(404)
    
    if request.method == "POST":
        cat = fetchone("SELECT id FROM categories WHERE name = %s", (request.form.get("category"),))
        
        execute("""
            UPDATE products SET 
                name = %s, brand = %s, category_id = %s, price = %s, was_price = %s,
                description = %s, stock = %s, emoji = %s, badge = %s, 
                is_active = %s, updated_at = NOW()
            WHERE id = %s
        """, (
            request.form.get("name"),
            request.form.get("brand"),
            cat["id"] if cat else product["category_id"],
            float(request.form.get("price", 0) or 0),
            float(request.form.get("was_price", 0) or 0) or None,
            request.form.get("description", ""),
            int(request.form.get("stock", 0) or 0),
            request.form.get("emoji", "📦"),
            request.form.get("badge", ""),
            request.form.get("is_active", "true") == "true",
            pid,
        ))
        
        # Delete images
        for iid in request.form.getlist("delete_image"):
            img = fetchone("SELECT url FROM product_images WHERE id = %s AND product_id = %s", (iid, pid))
            if img:
                try:
                    fpath = os.path.join(os.path.dirname(__file__), img["url"].lstrip("/"))
                    if os.path.exists(fpath):
                        os.remove(fpath)
                except Exception:
                    pass
                execute("DELETE FROM product_images WHERE id = %s", (iid,))
        
        # Add new images
        existing_count = fetchone("SELECT COUNT(*) AS c FROM product_images WHERE product_id = %s", (pid,))["c"]
        new_saved = 0
        
        for img in request.files.getlist("images"):
            if existing_count + new_saved >= MAX_IMGS:
                break
            url = _save_image(img)
            if url:
                execute("""
                    INSERT INTO product_images (product_id, url, is_primary, sort_order)
                    VALUES (%s, %s, FALSE, %s)
                """, (pid, url, existing_count + new_saved))
                new_saved += 1
        
        # Set primary image
        set_primary = request.form.get("set_primary")
        if set_primary:
            execute("UPDATE product_images SET is_primary = FALSE WHERE product_id = %s", (pid,))
            execute("UPDATE product_images SET is_primary = TRUE WHERE id = %s AND product_id = %s", (set_primary, pid))
        
        flash("Product updated!", "success")
        return redirect(url_for("admin_products"))
    
    images = fetchall("SELECT * FROM product_images WHERE product_id = %s ORDER BY sort_order ASC", (pid,))
    categories = fetchall("SELECT name FROM categories ORDER BY name")
    return render_template("admin_product_form.html", product=product, categories=categories, images=images)

@app.route("/admin/products/delete/<int:pid>", methods=["POST"])
@admin_required
def admin_delete_product(pid):
    execute("UPDATE products SET is_active = FALSE WHERE id = %s", (pid,))
    flash("Product removed.", "info")
    return redirect(url_for("admin_products"))

@app.route("/admin/products/restore/<int:pid>", methods=["POST"])
@admin_required
def admin_restore_product(pid):
    execute("UPDATE products SET is_active = TRUE WHERE id = %s", (pid,))
    flash("Product restored.", "success")
    return redirect(url_for("admin_products"))

# ============================================================
# ADMIN - CUSTOMERS
# ============================================================

@app.route("/admin/customers")
@admin_required
def admin_customers():
    search = request.args.get("q", "")
    where, params = ["u.role='customer'"], []
    if search:
        where.append("(u.name ILIKE %s OR u.email ILIKE %s OR u.city ILIKE %s)")
        params += [f"%{search}%"] * 3
    
    customers = fetchall(f"""
        SELECT u.*,
               COUNT(DISTINCT o.id) AS orders,
               COALESCE(SUM(o.total_amount), 0) AS total_spent
        FROM users u
        LEFT JOIN orders o ON o.user_id = u.id AND o.status != 'Cancelled'
        WHERE {" AND ".join(where)}
        GROUP BY u.id 
        ORDER BY u.created_at DESC
    """, params)
    
    return render_template("admin_customers.html", customers=customers, search=search)

@app.route("/admin/customers/<int:uid>")
@admin_required
def admin_customer_detail(uid):
    customer = fetchone("SELECT * FROM users WHERE id = %s AND role = 'customer'", (uid,))
    if not customer:
        abort(404)
    
    orders = fetchall("""
        SELECT o.order_code AS id, o.status, o.total_amount AS amount,
               o.placed_at::DATE AS date,
               STRING_AGG(p.name, ', ') AS products
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        JOIN products p ON p.id = oi.product_id
        WHERE o.user_id = %s 
        GROUP BY o.id 
        ORDER BY o.placed_at DESC
    """, (uid,))
    
    return render_template("admin_customer_detail.html", customer=customer, orders=orders)

@app.route("/admin/customers/toggle/<int:uid>", methods=["POST"])
@admin_required
def admin_toggle_customer(uid):
    user = fetchone("SELECT is_active FROM users WHERE id = %s", (uid,))
    if user:
        execute("UPDATE users SET is_active = %s WHERE id = %s", (not user["is_active"], uid))
        flash("Customer status updated.", "info")
    return redirect(url_for("admin_customers"))

# ============================================================
# ADMIN - ORDERS
# ============================================================

@app.route("/admin/orders")
@admin_required
def admin_orders():
    status = request.args.get("status", "")
    where, params = ["1=1"], []
    if status:
        where.append("o.status = %s")
        params.append(status)
    
    orders = fetchall(f"""
        SELECT o.*, o.order_code AS id,
               u.name AS customer, u.email AS customer_email
        FROM orders o 
        JOIN users u ON u.id = o.user_id
        WHERE {" AND ".join(where)} 
        ORDER BY o.placed_at DESC
    """, params)
    
    statuses = ["Pending", "Processing", "Shipped", "Delivered", "Cancelled", "Refunded"]
    return render_template("admin_orders.html", orders=orders,
                           active_status=status, statuses=statuses)

@app.route("/admin/orders/<int:oid>")
@admin_required
def admin_order_detail(oid):
    order = fetchone("""
        SELECT o.*, u.name AS customer, u.email AS customer_email, u.phone
        FROM orders o 
        JOIN users u ON u.id = o.user_id 
        WHERE o.id = %s
    """, (oid,))
    
    if not order:
        abort(404)
    
    items = fetchall("""
        SELECT p.name, p.emoji, p.brand, oi.qty, oi.unit_price, oi.subtotal
        FROM order_items oi 
        JOIN products p ON p.id = oi.product_id
        WHERE oi.order_id = %s
    """, (oid,))
    
    return render_template("admin_order_detail.html", order=order, items=items)

@app.route("/admin/orders/update-status/<int:oid>", methods=["POST"])
@admin_required
def admin_update_order_status(oid):
    new_status = request.form.get("status")
    valid = ["Pending", "Processing", "Shipped", "Delivered", "Cancelled", "Refunded"]
    
    if new_status in valid:
        execute("UPDATE orders SET status = %s, updated_at = NOW() WHERE id = %s", (new_status, oid))
        flash(f"Order status updated to {new_status}.", "success")
    
    return redirect(url_for("admin_order_detail", oid=oid))

# ============================================================
# ADMIN - TRENDS (WITH ML PREDICTION)
# ============================================================

@app.route("/admin/trends")
@admin_required
def admin_trends():
    # Check if ML predictor is ready
    ml_ready = ml_predictor.is_ready()
    
    # Get product behavior data for trend calculation
    trending_products = fetchall("""
        SELECT 
            p.id, p.name, p.brand, p.emoji, p.price, p.avg_rating,
            p.review_count, p.badge, p.stock, c.name AS category,
            COALESCE(SUM(be.views), 0) AS total_views,
            COALESCE(SUM(be.search), 0) AS total_searches,
            COALESCE(SUM(be.cart), 0) AS total_carts,
            COALESCE(SUM(be.wishlist), 0) AS total_wishlists,
            COALESCE(SUM(be.purchase), 0) AS total_purchases,
            COALESCE(SUM(be.views + be.search + be.cart + be.wishlist + be.purchase * 10), 0) AS trend_score_raw,
            (SELECT url FROM product_images WHERE product_id=p.id AND is_primary=TRUE LIMIT 1) AS image_url
        FROM products p
        JOIN categories c ON c.id = p.category_id
        LEFT JOIN behavior_events be ON be.product_id = p.id
        WHERE p.is_active = TRUE
        GROUP BY p.id, p.name, p.brand, p.emoji, p.price, p.avg_rating, 
                 p.review_count, p.badge, p.stock, c.name
        HAVING COALESCE(SUM(be.views + be.search + be.cart + be.wishlist + be.purchase * 10), 0) > 0
        ORDER BY trend_score_raw DESC
        LIMIT 30
    """)
    
    # If ML model is ready, use it for predictions
    if ml_ready and trending_products:
        # Build signals for ML
        from schemas import ProductSignals
        
        signals = []
        for p in trending_products[:20]:
            # Get recent behavior data
            behavior = fetchone("""
                SELECT 
                    COALESCE(SUM(CASE WHEN event_ts >= NOW() - INTERVAL '7 days' THEN views ELSE 0 END), 0) AS views_last_7d,
                    COALESCE(SUM(CASE WHEN event_ts < NOW() - INTERVAL '7 days' AND event_ts >= NOW() - INTERVAL '14 days' THEN views ELSE 0 END), 0) AS views_prev_7d,
                    COALESCE(SUM(CASE WHEN event_ts >= NOW() - INTERVAL '7 days' THEN purchase ELSE 0 END), 0) AS purchases_last_7d
                FROM behavior_events 
                WHERE product_id = %s
            """, (p["id"],))
            
            signal = ProductSignals(
                product_id=str(p["id"]),
                category=p["category"].lower(),
                views_last_7d=int(behavior.get("views_last_7d", 0) or 0),
                views_prev_7d=int(behavior.get("views_prev_7d", 0) or 0),
                searches_last_7d=0,
                searches_prev_7d=0,
                wishlist_last_7d=0,
                wishlist_prev_7d=0,
                cart_last_7d=0,
                cart_prev_7d=0,
                purchases_last_7d=int(behavior.get("purchases_last_7d", 0) or 0),
                avg_rating=float(p["avg_rating"] or 0),
                review_count=int(p["review_count"] or 0),
                price=float(p["price"] or 0)
            )
            signals.append(signal)
        
        # Get ML predictions
        try:
            predictions, _ = ml_predictor.predict_trends(signals, top_n=20)
            
            # Save predictions to database
            for pred in predictions:
                try:
                    execute("""
                        INSERT INTO trend_predictions 
                            (product_id, trend_score, trend_status, confidence, 
                             view_velocity, search_momentum, wishlist_signal, 
                             cart_intent, anomaly, forecast_7d, predicted_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                        ON CONFLICT (product_id) DO UPDATE SET
                            trend_score = EXCLUDED.trend_score,
                            trend_status = EXCLUDED.trend_status,
                            confidence = EXCLUDED.confidence,
                            view_velocity = EXCLUDED.view_velocity,
                            search_momentum = EXCLUDED.search_momentum,
                            wishlist_signal = EXCLUDED.wishlist_signal,
                            cart_intent = EXCLUDED.cart_intent,
                            anomaly = EXCLUDED.anomaly,
                            forecast_7d = EXCLUDED.forecast_7d,
                            predicted_at = NOW()
                    """, (
                        int(pred.product_id), pred.trend_score, pred.trend_status.value,
                        pred.confidence, pred.view_velocity, pred.search_momentum,
                        pred.wishlist_signal, pred.cart_intent, pred.anomaly_detected,
                        pred.forecast_7d
                    ))
                except Exception as e:
                    app.logger.error(f"Error saving prediction: {e}")
            
            # Update products with prediction data
            pred_dict = {int(p.product_id): p for p in predictions}
            for p in trending_products:
                if p["id"] in pred_dict:
                    pred = pred_dict[p["id"]]
                    p["trend_score"] = pred.trend_score * 100
                    p["trend_status"] = pred.trend_status.value
                    p["confidence"] = pred.confidence * 100
                    p["view_velocity"] = pred.view_