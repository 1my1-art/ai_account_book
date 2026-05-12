import os
import sqlite3
import json
import re
from datetime import datetime
from functools import lru_cache
from flask import Flask, request, jsonify, session, redirect, url_for, render_template, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from aip import AipOcr
import pandas as pd

app = Flask(__name__)
app.secret_key = "dev-secret-key-123456"
DB_PATH = "ai_accounting.db"
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ================= 百度OCR配置 =================
BAIDU_APP_ID = '7624192'
BAIDU_API_KEY = 'gh66HbVzdO0KVrfNqB1k6Ovj'
BAIDU_SECRET_KEY = 'TBODzQjZE0zPcbLmLh5r60jbqRNBFjRK'
client = AipOcr(BAIDU_APP_ID, BAIDU_API_KEY, BAIDU_SECRET_KEY)

# ================= 商户分类映射 =================
@lru_cache(maxsize=1)
def load_merchant_map():
    try:
        df = pd.read_excel("商品细表.xlsx", sheet_name="Sheet1")
        merchant_map = {}
        for _, row in df.iterrows():
            category = row["分类"]
            keywords = str(row["商户关键词"]).split("、")
            merchants = str(row["商户名称"]).split("、")
            for kw in keywords:
                if kw and kw != 'nan':
                    merchant_map[kw] = category
            for merchant in merchants:
                if merchant and merchant != 'nan':
                    merchant_map[merchant] = category
        return merchant_map
    except:
        return {"瑞幸":"饮品", "蜜雪冰城":"餐饮", "美团":"餐饮", "滴滴":"交通", "京东":"购物"}

# ================= 数据库 =================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            type TEXT DEFAULT 'expense',
            merchant TEXT,
            category TEXT,
            date TEXT,
            remark TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS budgets (
            user_id INTEGER,
            month TEXT,
            total_budget REAL,
            category_budget TEXT,
            updated_at TEXT,
            PRIMARY KEY(user_id, month),
            FOREIGN KEY(user_id) REFERENCES users(id)
        )''')
        
        # 清理错误数据：把收入分类的 type 改成 income
        conn.execute("UPDATE bills SET type='income' WHERE category IN ('工资','兼职','奖金','红包','理财收益','报销')")
        conn.execute("UPDATE bills SET type='expense' WHERE type IS NULL OR type=''")
        
        # 默认测试账号
        if not conn.execute("SELECT id FROM users WHERE username='test'").fetchone():
            conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                        ("test", generate_password_hash("123456")))
        if not conn.execute("SELECT id FROM users WHERE username='0000'").fetchone():
            conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                        ("0000", generate_password_hash("123456")))

# ================= OCR工具函数 =================
def ocr_image(image_bytes):
    try:
        result = client.basicGeneral(image_bytes)
        all_text = ""
        if "words_result" in result:
            for item in result["words_result"]:
                all_text += item["words"] + "\n"
        return all_text
    except Exception as e:
        return ""

def extract_amount(text):
    patterns = [
        r'[¥￥]\s*(\d+\.?\d{0,2})',
        r'(\d+\.?\d{0,2})\s*元',
        r'实付\D*(\d+\.?\d{0,2})',
        r'金额\D*(\d+\.?\d{0,2})',
        r'支付\D*(\d+\.?\d{0,2})',
        r'消费\D*(\d+\.?\d{0,2})'
    ]
    for p in patterns:
        match = re.search(p, text, re.I)
        if match:
            return round(float(match.group(1)), 2)
    nums = re.findall(r'\d+\.?\d*', text)
    for n in nums:
        try:
            val = float(n)
            if 0.1 < val < 100000:
                return round(val, 2)
        except:
            pass
    return None

def extract_merchant(text):
    keywords = ["瑞幸","蜜雪冰城","茶百道","喜茶","肯德基","麦当劳","美团","饿了么","滴滴","超市","餐厅"]
    for kw in keywords:
        if kw in text:
            return kw
    lines = [l.strip() for l in text.splitlines() if len(l.strip()) < 20 and l.strip()]
    for line in lines:
        if any(x in line for x in ["店","馆","市","厅","社"]):
            return line
    return "未知商户"

def get_auto_category(merchant):
    merchant_map = load_merchant_map()
    for key, cat in merchant_map.items():
        if key in str(merchant):
            return cat
    return None

# ================= 路由 =================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        action = request.form.get("action", "login")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with get_db() as conn:
            if action == "register":
                if len(username) < 4 or len(password) < 6:
                    flash("用户名≥4位，密码≥6位", "danger")
                    return redirect(url_for("login"))
                if conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone():
                    flash("用户名已存在", "warning")
                    return redirect(url_for("login"))
                conn.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                            (username, generate_password_hash(password)))
                flash("注册成功！请登录", "success")
                return redirect(url_for("login"))
            elif action == "login":
                user = conn.execute("SELECT id, password_hash FROM users WHERE username=?", (username,)).fetchone()
                if user and check_password_hash(user["password_hash"], password):
                    session["user_id"] = user["id"]
                    session["username"] = username
                    return redirect(url_for("index"))
                flash("账号或密码错误", "danger")
                return redirect(url_for("login"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/")
def index():
    return render_template("index.html", username=session.get("username", "游客"))

@app.route("/api/me")
def api_me():
    if session.get("user_id"):
        return jsonify({
            "username": session.get("username", "用户"),
            "has_api_key": False
        })
    return jsonify({"username": "游客", "has_api_key": False})

# ================= OCR API =================
@app.route("/api/ocr", methods=["POST"])
def api_ocr():
    if "image" not in request.files:
        return jsonify({"error": "未上传图片"}), 400
    file = request.files["image"]
    img_bytes = file.read()
    text = ocr_image(img_bytes)
    amount = extract_amount(text)
    merchant = extract_merchant(text)
    category = get_auto_category(merchant)
    return jsonify({
        "amount": amount,
        "merchant": merchant,
        "category": category,
        "need_manual": category is None
    })

# ================= 账单API（修复版） =================
@app.route('/api/save_bill', methods=['POST'])
def save_bill():
    if 'user_id' not in session:
        return jsonify({'error': '未登录'}), 401
    data = request.json
    
    bill_type = data.get('type', 'expense')
    
    conn = get_db()
    conn.execute('''
        INSERT INTO bills (user_id, date, merchant, category, amount, remark, type)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (session['user_id'], 
          data.get('date', datetime.now().strftime('%Y-%m-%d')),
          data.get('merchant', ''),
          data.get('category', '其他'),
          abs(float(data.get('amount', 0))),
          data.get('remark', ''),
          bill_type))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route("/api/bills", methods=["GET"])
def get_bills():
    user_id = session.get("user_id", 1)
    date_str = request.args.get("date")
    year = request.args.get("year")
    month = request.args.get("month")
    
    with get_db() as conn:
        if date_str:
            rows = conn.execute("""
                SELECT id, date, merchant, category, amount, remark, type 
                FROM bills 
                WHERE user_id=? AND date=? 
                ORDER BY created_at DESC
            """, (user_id, date_str)).fetchall()
        elif year and month:
            rows = conn.execute("""
                SELECT id, date, merchant, category, amount, remark, type 
                FROM bills 
                WHERE user_id=? AND strftime('%Y', date)=? AND strftime('%m', date)=?
                ORDER BY date DESC
            """, (user_id, str(year), f"{int(month):02d}")).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, date, merchant, category, amount, remark, type 
                FROM bills 
                WHERE user_id=? 
                ORDER BY created_at DESC LIMIT 50
            """, (user_id,)).fetchall()
    return jsonify({"data": [dict(r) for r in rows]})

@app.route("/api/monthly_bills", methods=["GET"])
def monthly_bills():
    user_id = session.get("user_id", 1)
    year = request.args.get("year", datetime.now().year)
    month = request.args.get("month", datetime.now().month)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT id, date, merchant, category, amount, remark, type 
            FROM bills 
            WHERE user_id=? AND strftime('%Y', date)=? AND strftime('%m', date)=?
            ORDER BY date DESC
        """, (user_id, str(year), f"{int(month):02d}")).fetchall()
        total = conn.execute("""
            SELECT SUM(amount) as total FROM bills 
            WHERE user_id=? AND strftime('%Y', date)=? AND strftime('%m', date)=? AND type='expense'
        """, (user_id, str(year), f"{int(month):02d}")).fetchone()
    return jsonify({
        "bills": [dict(r) for r in rows],
        "total": total["total"] if total and total["total"] else 0
    })

@app.route("/api/monthly_summary")
def monthly_summary():
    user_id = session.get("user_id", 1)
    month = request.args.get("month", datetime.now().strftime("%Y-%m"))
    with get_db() as conn:
        rows = conn.execute("""
            SELECT category, SUM(amount) as total 
            FROM bills 
            WHERE user_id=? AND date LIKE ? AND type='expense'
            GROUP BY category
        """, (user_id, f"{month}%")).fetchall()
    return jsonify({"data": [dict(r) for r in rows]})

@app.route("/api/budget", methods=["GET", "POST"])
def api_budget():
    user_id = session.get("user_id", 1)
    month = request.args.get("month", datetime.now().strftime("%Y-%m"))
    with get_db() as conn:
        if request.method == "GET":
            row = conn.execute("SELECT * FROM budgets WHERE user_id=? AND month=?", 
                              (user_id, month)).fetchone()
            return jsonify({
                "total_budget": row["total_budget"] if row else 3000,
                "category_budget": json.loads(row["category_budget"]) if row and row["category_budget"] else {}
            })
        data = request.json
        conn.execute("""INSERT OR REPLACE INTO budgets (user_id, month, total_budget, category_budget, updated_at)
                        VALUES (?, ?, ?, ?, ?)""",
                    (user_id, month, float(data["total_budget"]), 
                     json.dumps(data.get("category_budget", {})), datetime.now().isoformat()))
    return jsonify({"success": True})

@app.route("/api/budget_status")
def budget_status():
    user_id = session.get("user_id", 1)
    month = datetime.now().strftime("%Y-%m")
    with get_db() as conn:
        budget_row = conn.execute("SELECT total_budget FROM budgets WHERE user_id=? AND month=?", 
                                 (user_id, month)).fetchone()
        spend_row = conn.execute("SELECT SUM(amount) as total FROM bills WHERE user_id=? AND date LIKE ? AND type='expense'",
                                (user_id, f"{month}%")).fetchone()
    total_budget = budget_row["total_budget"] if budget_row else 3000
    current_spend = spend_row["total"] if spend_row and spend_row["total"] else 0
    return jsonify({
        "total_budget": total_budget,
        "current_spend": current_spend,
        "remain": total_budget - current_spend
    })

@app.route("/api/ai_suggestion")
def ai_suggestion():
    user_id = session.get("user_id", 1)
    month = datetime.now().strftime("%Y-%m")
    with get_db() as conn:
        stats = conn.execute("""
            SELECT category, SUM(amount) as total 
            FROM bills 
            WHERE user_id=? AND date LIKE ? AND type='expense'
            GROUP BY category
        """, (user_id, f"{month}%")).fetchall()
    if not stats:
        return jsonify({"suggestion": "📝 本月暂无支出数据"})
    stats_list = [dict(r) for r in stats]
    top = max(stats_list, key=lambda x: x["total"])
    total = sum(d["total"] for d in stats_list)
    ratio = round(top["total"] / total * 100, 1)
    sug = f"💡 本月总支出 {total:.2f} 元。\n📊 【{top['category']}】占比最高 ({ratio}%)，建议适当控制。"
    return jsonify({"suggestion": sug})

if __name__ == "__main__":
    init_db()
    print("🚀 AI记账本启动 → http://127.0.0.1:5000")
    app.run(debug=True, host="0.0.0.0", port=5000)
