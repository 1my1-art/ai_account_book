import sqlite3
from datetime import datetime
import streamlit as st
from aip import AipOcr
import re
import plotly.express as px
import pandas as pd
import streamlit as st

# ========== 密码保护 ==========
def check_password():
    """返回 True 表示验证通过"""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    
    if not st.session_state.authenticated:
        password = st.text_input("请输入访问密码", type="password")
        if st.button("进入"):
            if password == "123456":  # 你可以改成自己想要的密码
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("密码错误")
        return False
    return True

# 检查密码
if not check_password():
    st.stop()  # 密码错误就停在这里，不显示后面的内容

# ========== 下面是你原来的代码 ==========
# 从 Excel 加载商户映射表
@st.cache_data
def load_merchant_map():
    df = pd.read_excel("商品细表.xlsx", sheet_name="Sheet1")
    merchant_map = {}
    for _, row in df.iterrows():
        category = row["分类"]
        keywords = str(row["商户关键词"]).split("、")
        merchants = str(row["商户名称"]).split("、")
        for kw in keywords:
            merchant_map[kw] = category
        for merchant in merchants:
            merchant_map[merchant] = category
    return merchant_map


# 配置与初始化
# ==============================================
st.set_page_config(page_title="AI记账本", page_icon="📝")
# 加载映射表
merchant_category_map = load_merchant_map()
# 百度OCR配置
APP_ID = '7624192'
API_KEY = 'gh66HbVzdO0KVrfNqB1k6Ovj'
SECRET_KEY = 'TBODzQjZE0zPcbLmLh5r60jbqRNBFjRK'
client = AipOcr(APP_ID, API_KEY, SECRET_KEY)



# ==============================================
# 数据库函数（统一使用 bills.db，不再混乱）
# ==============================================
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

# ==============================================
# AI消费建议分析函数
# ==============================================
def get_ai_suggestion(df):
    if df.empty:
        return "📝 暂无消费数据，快来记账吧！"
    
    total_expense = df['total'].sum()
    top_category = df.loc[df['total'].idxmax(), 'category']
    top_amount = df.loc[df['total'].idxmax(), 'total']
    top_ratio = round(top_amount / total_expense * 100, 1)
    
    suggestion = f"💡 本月总支出 {total_expense:.2f} 元\n"
    suggestion += f"📊 支出最高的分类是【{top_category}】，占比 {top_ratio}%，金额 {top_amount:.2f} 元\n\n"
    
    # 针对性建议
    if top_category in ["餐饮", "饮品"]:
        suggestion += "✅ 建议：减少外卖和饮品消费，多自己做饭，每月可节省不少开支~"
    elif top_category == "衣服":
        suggestion += "✅ 建议：理性消费，避免冲动购物，优先购买刚需衣物"
    elif top_category == "零食":
        suggestion += "✅ 建议：控制零食采购量，既省钱又健康"
    elif top_category == "交通":
        suggestion += "✅ 建议：优先选择公共交通，减少打车频次"
    else:
        suggestion += "✅ 建议：持续关注该分类支出，做好预算规划"
    
    return suggestion

# ==============================================
# OCR 工具函数
# ==============================================
def get_auto_category(merchant):
    for key, cat in merchant_category_map.items():
        if key in merchant:
            return cat
    return None

def ocr_image(image_bytes):
    result = client.basicGeneral(image_bytes)
    all_text = ""
    if "words_result" in result:
        for item in result["words_result"]:
            all_text += item["words"] + "\n"
    return all_text

def extract_amount(text):
    match = re.search(r"(\d+\.\d{2})", text)
    if match:
        return float(match.group(1))
    return None

def extract_merchant(text):
    keywords = ["蜜雪冰城", "瑞幸", "茶百道", "喜茶", "肯德基", "麦当劳", "海底捞",
                "美团", "饿了么", "优衣库", "耐克", "阿迪达斯", "滴滴", "哈啰",
                "良品铺子", "三只松鼠", "拼多多", "淘宝", "京东"]
    for kw in keywords:
        if kw in text:
            return kw
    return None

# ==============================================
# 页面主逻辑
# ==============================================
init_db()
st.title("📝 AI智能记账本")
st.write("上传支付截图，自动识别并记账")

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
        with st.spinner("正在识别中..."):
            text = ocr_image(image_bytes)
        st.session_state.current_amount = extract_amount(text)
        st.session_state.current_merchant = extract_merchant(text)

        if st.session_state.current_merchant:
            auto_cat = get_auto_category(st.session_state.current_merchant)
            if auto_cat:
                st.session_state.current_category = auto_cat
                st.session_state.need_manual = False
            else:
                st.session_state.need_manual = True
        else:
            st.session_state.need_manual = True

        st.session_state.ocr_done = True

    # 显示识别结果
    st.subheader("📋 识别结果")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("💰 金额", f"{st.session_state.current_amount} 元" if st.session_state.current_amount else "未识别到")
    with col2:
        st.metric("🏪 商户", st.session_state.current_merchant if st.session_state.current_merchant else "未识别到")

    # 自动分类
    if st.session_state.current_category and not st.session_state.need_manual:
        st.success(f"🤖 已自动分类为：{st.session_state.current_category}")

    # 手动分类
    if st.session_state.need_manual:
        st.subheader("🏷️ 请手动选择分类")
        categories = ["餐饮", "饮品", "衣服", "零食", "交通", "美妆", "学习", "医疗", "其他"]
        cols = st.columns(4)
        for i, cat in enumerate(categories):
            if cols[i % 4].button(cat, key=f"manual_{cat}"):
                st.session_state.current_category = cat
                st.session_state.need_manual = False
                st.rerun()

    # 保存按钮
    if st.button("💾 保存记账", type="primary"):
        if st.session_state.current_amount and st.session_state.current_category:
            save_bill(
                st.session_state.current_amount,
                st.session_state.current_merchant or "未知商户",
                st.session_state.current_category
            )
            st.success(f"已保存！{st.session_state.current_merchant or '未知商户'} - {st.session_state.current_amount}元 - {st.session_state.current_category}")
            
            # 重置
            st.session_state.ocr_done = False
            st.session_state.current_amount = None
            st.session_state.current_merchant = None
            st.session_state.current_category = None
            st.session_state.need_manual = False
        else:
            st.error("请先完成识别与分类")

else:
    st.session_state.ocr_done = False
    st.session_state.current_amount = None
    st.session_state.current_merchant = None
    st.session_state.current_category = None
    st.session_state.need_manual = False
    st.info("👆 请上传一张支付截图开始记账")

# ==============================================
# 图表展示
# ==============================================
st.markdown("---")
st.subheader("📊 本月支出统计")
df = get_monthly_summary()
if not df.empty:
    fig = px.pie(df, values='total', names='category', title='支出分类占比')
    st.plotly_chart(fig)
else:
    st.write("暂无数据，请先记账")

# ==============================================
# AI消费建议模块
# ==============================================
st.markdown("---")
st.subheader("💡 AI消费建议")
suggestion = get_ai_suggestion(df)
st.info(suggestion)
# 显示历史账单
st.subheader("📋 历史账单")

conn = sqlite3.connect('bills.db')
df_bills = pd.read_sql_query("SELECT date, merchant, amount, category FROM bills ORDER BY date DESC", conn)
conn.close()

if not df_bills.empty:
    # 重命名列名显示
    df_bills.columns = ["日期", "商户", "金额", "分类"]
    st.dataframe(df_bills, use_container_width=True)
    
    # 可选：添加删除按钮
    if st.button("🗑️ 清空所有账单"):
        conn = sqlite3.connect('bills.db')
        conn.execute("DELETE FROM bills")
        conn.commit()
        conn.close()
        st.success("已清空所有账单")
        st.rerun()
else:
    st.write("暂无账单，请先记账")
