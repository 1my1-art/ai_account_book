import sqlite3
from datetime import datetime
import streamlit as st
from aip import AipOcr
import re
import plotly.express as px
import pandas as pd
import os

# ========== 密码保护 ==========
def check_password():
    """返回 True 表示验证通过"""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False

    if not st.session_state.authenticated:
        password = st.text_input("请输入访问密码", type="password")
        if st.button("进入"):
            if password == st.secrets.get("PASSWORD", "123456"):
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("密码错误")
        return False
    return True

# 检查密码
if not check_password():
    st.stop()

# ========== 配置页面 ==========
st.set_page_config(page_title="AI记账本", page_icon="📝")

# ========== 加载商户映射表（增加异常处理） ==========
@st.cache_data
def load_merchant_map():
    merchant_map = {}
    if os.path.exists("商品细表.xlsx"):
        try:
            df = pd.read_excel("商品细表.xlsx", sheet_name="Sheet1")
            for _, row in df.iterrows():
                category = row["分类"]
                keywords = str(row["商户关键词"]).split("、")
                merchants = str(row["商户名称"]).split("、")
                for kw in keywords:
                    if kw and kw != "nan":
                        merchant_map[kw.strip()] = category
                for merchant in merchants:
                    if merchant and merchant != "nan":
                        merchant_map[merchant.strip()] = category
        except Exception as e:
            st.warning(f"Excel映射表加载失败：{str(e)}，将使用默认分类")
    # 内置基础商户兜底
    base_map = {
        "蜜雪冰城": "饮品", "瑞幸": "饮品", "茶百道": "饮品", "喜茶": "饮品",
        "肯德基": "餐饮", "麦当劳": "餐饮", "海底捞": "餐饮",
        "美团": "餐饮", "饿了么": "餐饮",
        "优衣库": "衣服", "耐克": "衣服", "阿迪达斯": "衣服",
        "滴滴": "交通", "哈啰": "交通", "地铁": "交通",
        "良品铺子": "零食", "三只松鼠": "零食"
    }
    merchant_map.update(base_map)
    return merchant_map

merchant_category_map = load_merchant_map()

# ========== 百度OCR（从secrets读取，更安全） ==========
try:
    APP_ID = st.secrets["APP_ID"]
    API_KEY = st.secrets["API_KEY"]
    SECRET_KEY = st.secrets["SECRET_KEY"]
    client = AipOcr(APP_ID, API_KEY, SECRET_KEY)
except:
    client = None
    st.warning("未配置百度OCR密钥，将无法使用图片识别功能")

# ========== 数据库函数 ==========
def init_db():
    conn = sqlite3.connect('bills.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS bills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL,
            merchant TEXT,
            category TEXT,
            date TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_bill(amount, merchant, category):
    conn = sqlite3.connect('bills.db')
    c = conn.cursor()
    c.execute(
        "INSERT INTO bills (amount, merchant, category, date) VALUES (?, ?, ?, ?)",
        (amount, merchant, category, datetime.now().strftime("%Y-%m-%d"))
    )
    conn.commit()
    conn.close()

def get_monthly_summary():
    conn = sqlite3.connect('bills.db')
    current_month = datetime.now().strftime("%Y-%m")
    query = f"SELECT category, SUM(amount) as total FROM bills WHERE date LIKE '{current_month}%' GROUP BY category"
    df = pd.read_sql_query(query, conn)
    conn.close()
    return df

# ========== AI消费建议 ==========
def get_ai_suggestion(df):
    if df.empty:
        return "📝 暂无消费数据，快来记账吧！"
    total_expense = df['total'].sum()
    top_category = df.loc[df['total'].idxmax(), 'category']
    top_amount = df.loc[df['total'].idxmax(), 'total']
    top_ratio = round(top_amount / total_expense * 100, 1)
    suggestion = f"💡 本月总支出 {total_expense:.2f} 元\n"
    suggestion += f"📊 支出最高：【{top_category}】{top_ratio}%，{top_amount:.2f}元\n\n"
    if top_category in ["餐饮", "饮品"]:
        suggestion += "✅ 建议：减少外卖，自己做饭更省钱~"
    elif top_category == "衣服":
        suggestion += "✅ 建议：理性消费，避免冲动购物"
    elif top_category == "零食":
        suggestion += "✅ 建议：控制零食，省钱又健康"
    elif top_category == "交通":
        suggestion += "✅ 建议：多坐公共交通，减少打车"
    else:
        suggestion += "✅ 建议：做好预算，理性消费"
    return suggestion

# ========== OCR 工具函数（增强版） ==========
def get_auto_category(merchant):
    if not merchant:
        return None
    for key, cat in merchant_category_map.items():
        if key in merchant:
            return cat
    return "其他"

def ocr_image(image_bytes):
    if not client:
        return ""
    result = client.basicGeneral(image_bytes)
    all_text = ""
    if "words_result" in result:
        for item in result["words_result"]:
            all_text += item["words"] + "\n"
    return all_text

def extract_amount(text):
    # 增强正则：支持 25 / 25.0 / 25.00 / ¥25 / 金额25
    patterns = [
        r"(\d+\.\d{1,2})",
        r"(\d+)元",
        r"¥(\d+)",
        r"金额[：:](\d+)"
    ]
    for pat in patterns:
        match = re.search(pat, text)
        if match:
            return float(match.group(1))
    return None

def extract_merchant(text):
    for name in merchant_category_map.keys():
        if name in text:
            return name
    return None

# ========== 页面主逻辑 ==========
init_db()
st.title("📝 AI智能记账本")
st.write("上传支付截图，自动识别金额、商户、分类")

# 初始化状态
if "current_amount" not in st.session_state:
    st.session_state.current_amount = None
if "current_merchant" not in st.session_state:
    st.session_state.current_merchant = None
if "current_category" not in st.session_state:
    st.session_state.current_category = None
if "ocr_done" not in st.session_state:
    st.session_state.ocr_done = False
if "need_manual" not in st.session_state:
    st.session_state.need_manual = False

# 上传图片
uploaded_file = st.file_uploader("选择截图", type=["png", "jpg", "jpeg"])

if uploaded_file:
    image_bytes = uploaded_file.read()
    st.image(image_bytes, caption="上传的截图", width=300)

    if not st.session_state.ocr_done:
        with st.spinner("正在AI识别中..."):
            text = ocr_image(image_bytes)
        st.session_state.current_amount = extract_amount(text)
        st.session_state.current_merchant = extract_merchant(text)
        st.session_state.current_category = get_auto_category(st.session_state.current_merchant)
        st.session_state.need_manual = (st.session_state.current_category is None)
        st.session_state.ocr_done = True

    # 显示识别结果
    st.subheader("📋 识别结果")
    col1, col2 = st.columns(2)
    with col1:
        amt = st.session_state.current_amount
        st.metric("💰 金额", f"{amt:.2f}元" if amt else "未识别")
    with col2:
        mer = st.session_state.current_merchant
        st.metric("🏪 商户", mer if mer else "未识别")

    # 自动分类
    cat = st.session_state.current_category
    if cat and not st.session_state.need_manual:
        st.success(f"🤖 自动分类：{cat}")

    # 手动分类
    if st.session_state.need_manual or not cat:
        st.subheader("🏷️ 手动选择分类")
        categories = ["餐饮", "饮品", "衣服", "零食", "交通", "美妆", "学习", "医疗", "其他"]
        cols = st.columns(4)
        for i, c in enumerate(categories):
            if cols[i % 4].button(c, key=f"cat_{c}"):
                st.session_state.current_category = c
                st.session_state.need_manual = False
                st.rerun()

    # 保存按钮（增加校验）
    if st.button("💾 保存记账", type="primary"):
        amt = st.session_state.current_amount
        cat = st.session_state.current_category
        if amt and amt > 0 and cat:
            save_bill(
                amt,
                st.session_state.current_merchant or "未知商户",
                cat
            )
            st.success("保存成功！")
            # 重置
            for k in ["current_amount", "current_merchant", "current_category", "ocr_done", "need_manual"]:
                st.session_state[k] = None if k != "ocr_done" else False
        else:
            st.error("请确保金额有效且已选择分类")

else:
    for k in ["current_amount", "current_merchant", "current_category", "ocr_done", "need_manual"]:
        st.session_state[k] = None if k != "ocr_done" else False
    st.info("👆 请上传支付截图开始记账")

# ========== 图表 ==========
st.markdown("---")
st.subheader("📊 本月支出统计")
df = get_monthly_summary()
if not df.empty:
    fig = px.pie(df, values='total', names='category', title='支出占比')
    st.plotly_chart(fig)
else:
    st.write("暂无数据")

# ========== AI建议 ==========
st.markdown("---")
st.subheader("💡 AI消费建议")
st.info(get_ai_suggestion(df))

# ========== 历史账单 ==========
st.subheader("📋 历史账单")
conn = sqlite3.connect('bills.db')
df_bills = pd.read_sql_query("SELECT date, merchant, amount, category FROM bills ORDER BY date DESC", conn)
conn.close()

if not df_bills.empty:
    df_bills.columns = ["日期", "商户", "金额", "分类"]
    st.dataframe(df_bills, use_container_width=True)

    # 清空账单（增加二次确认）
    if st.button("🗑️ 清空所有账单"):
        st.session_state.confirm_clear = True
    if st.session_state.get("confirm_clear"):
        if st.button("⚠️ 确认清空（不可恢复）"):
            conn = sqlite3.connect('bills.db')
            conn.execute("DELETE FROM bills")
            conn.commit()
            conn.close()
            st.success("已清空所有账单")
            st.session_state.confirm_clear = False
            st.rerun()
else:
    st.write("暂无账单")
