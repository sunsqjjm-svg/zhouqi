import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
from datetime import datetime

# 页面基础配置
st.set_page_config(page_title="强周期股票周期拐点诊断仪", layout="wide", page_icon="📈")

st.title("📊 强周期股票周期拐点诊断仪")
st.caption("基于雪球「律动周期研究所」复盘框架：将感觉变为读数（ROE下行末端+PB底 / 91%风险+换手率预警）")

# 1. 股票代码与市场格式转换
def get_symbol_prefix(code):
    """转换代码为腾讯/新浪格式 (sh/sz/bj)"""
    code = str(code).strip()
    if code.startswith('6') or code.startswith('9'):
        return f"sh{code}"
    elif code.startswith('0') or code.startswith('3'):
        return f"sz{code}"
    elif code.startswith('4') or code.startswith('8'):
        return f"bj{code}"
    return f"sz{code}"

# 2. 股票搜索函数 (支持名称转代码，增强容错)
@st.cache_data(ttl=86400)
def search_stock(keyword):
    keyword = keyword.strip()
    # 纯数字直接构造
    if keyword.isdigit() and len(keyword) == 6:
        return {"code": keyword, "name": keyword, "secid": get_symbol_prefix(keyword)}

    # 名称联想搜索 (新浪通用联想接口，境外节点友好)
    url = f"https://suggest3.sinajs.cn/suggest/type=11,12,13,14,15&key={keyword}"
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        # 返回格式如: var suggestvalue="中钢国际,11,000928,sz000928,..."
        text = r.text
        if "suggestvalue=" in text:
            val = text.split('="')[1].split('";')[0]
            if val:
                first_item = val.split(";")[0].split(",")
                name = first_item[0]
                code = first_item[2]
                secid = first_item[3]
                return {"code": code, "name": name, "secid": secid}
    except Exception:
        pass

    # 兜底：常用经典周期股静态映射表
    preset_map = {
        "蓝思科技": "sz300433", "京东方A": "sz000725", "京东方": "sz000725",
        "中钢国际": "sz000928", "TCL科技": "sz000100", "韦尔股份": "sh603501",
        "紫金矿业": "sh601899", "万华化学": "sh600309", "中芯国际": "sh688981"
    }
    for k, v in preset_map.items():
        if k in keyword:
            return {"code": v[2:], "name": k, "secid": v}
            
    return None

# 3. 数据拉取函数 (腾讯实时行情PB + 新浪历史K线，彻底解决海外IP封锁)
@st.cache_data(ttl=1800)
def fetch_stock_data(secid, years=5):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://finance.qq.com"
    }
    
    # (1) 通过腾讯财经拉取实时价格、换手率和 PB（市净率）
    quote_url = f"https://qt.gtimg.cn/q={secid}"
    try:
        r_q = requests.get(quote_url, headers=headers, timeout=6)
        r_q.encoding = "gbk"
        q_text = r_q.text
        if '="' not in q_text:
            return None, "未获取到该股票的实时行情数据"
            
        data_parts = q_text.split('="')[1].split('~')
        stock_name = data_parts[1]
        curr_price = float(data_parts[3])
        curr_turnover = float(data_parts[38]) if data_parts[38] != "" else 0.0 # 换手率
        curr_pb = float(data_parts[46]) if len(data_parts) > 46 and data_parts[46] != "" else 0.0 # 市净率PB
    except Exception as e:
        return None, f"实时行情解析异常: {e}"

    # (2) 通过新浪财经拉取历史日K线（覆盖近 1000 个交易日，约4年）
    datalen = min(years * 250, 1020)
    kline_url = f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol={secid}&scale=240&ma=no&datalen={datalen}"
    
    try:
        r_k = requests.get(kline_url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
        kline_data = r_k.json()
        if not kline_data or not isinstance(kline_data, list):
            return None, "历史日K线数据接口返回为空"
    except Exception as e:
        return None, f"历史行情接口响应异常: {e}"

    # 解析 K 线
    records = []
    for item in kline_data:
        records.append({
            "日期": pd.to_datetime(item["day"]),
            "收盘": float(item["close"]),
            "最高": float(item["high"]),
            "最低": float(item["low"]),
            "成交量": float(item["volume"])
        })
    
    df = pd.DataFrame(records).set_index("日期").sort_index()

    # (3) 构造历史连续 PB 通道（BPS = 当前收盘 / 当前PB）
    if curr_pb > 0:
        bps = curr_price / curr_pb
        df['pb'] = (df['收盘'] / bps).round(2)
    else:
        df['pb'] = 1.0

    meta = {
        "stock_name": stock_name,
        "curr_price": curr_price,
        "curr_pb": curr_pb,
        "curr_turnover": curr_turnover
    }
    return (df, meta), None

# --- 侧边栏：操作面板 ---
with st.sidebar:
    st.header("⚙️ 股票搜索与快捷选择")
    
    st.write("点击快捷诊断经典周期股：")
    c1, c2, c3 = st.columns(3)
    preset = None
    if c1.button("中钢国际"): preset = "中钢国际"
    if c2.button("蓝思科技"): preset = "蓝思科技"
    if c3.button("京东方A"): preset = "京东方A"
    
    c4, c5, c6 = st.columns(3)
    if c4.button("TCL科技"): preset = "TCL科技"
    if c5.button("紫金矿业"): preset = "紫金矿业"
    if c6.button("万华化学"): preset = "万华化学"

    user_input = st.text_input("输入股票名称或代码", value=preset if preset else "中钢国际")
    years_back = st.slider("历史回溯周期（年）", min_value=3, max_value=5, value=4)

# --- 主页面诊断逻辑 ---
if user_input:
    with st.spinner(f"正在匹配「{user_input}」代码..."):
        info = search_stock(user_input)

    if not info:
        st.error(f"❌ 未识别到「{user_input}」，请输入规范的股票名称（如：中钢国际、蓝思科技、京东方A）或6位代码。")
    else:
        with st.spinner(f"正在拉取 {info['name']} ({info['code']}) 数据并运行周期读数模型..."):
            res, err = fetch_stock_data(info['secid'], years=years_back)

        if err:
            st.error(f"数据获取遇到问题: {err}")
        else:
            df, meta = res
            curr_price = meta["curr_price"]
            curr_pb = meta["curr_pb"]
            curr_turnover = meta["curr_turnover"]
            stock_real_name = meta["stock_name"]

            st.success(f"🎯 成功识别：**{stock_real_name}** (`{info['code']}`)")

            # --- 核心指标读数计算 ---
            # 1. PB 历史分位数 (0% ~ 100%)
            pb_percentile = (df['pb'] < curr_pb).mean() * 100.0

            # 2. 综合长短周期风险读数 (PB 分位 70% + 价格波动通道 30%)
            roll_high = df['收盘'].rolling(250, min_periods=30).max().iloc[-1]
            roll_low = df['收盘'].rolling(250, min_periods=30).min().iloc[-1]
            price_pos = (curr_price - roll_low) / (roll_high - roll_low) * 100.0 if roll_high > roll_low else 50.0
            risk_score = 0.7 * pb_percentile + 0.3 * price_pos

            # --- 周期位置判定规则 ---
            if risk_score >= 90:
                if curr_turnover >= 6.0:
                    status = "🔴 周期大顶 / 高危预警 (双条件触发)"
                    advice = "短线与周期风险极高！模型建议：盘中至少减仓 1/3 锁定收益，严防 40%+ 级回撤。"
                    desc = f"风险读数高达 {risk_score:.1f}%（超 90% 极值红线），且当日换手率（{curr_turnover:.2f}%）大幅过热，长短周期共振承压。"
                else:
                    status = "🟠 周期高位区 (估值过热)"
                    advice = "估值已进入泡沫化阶段，不宜追高加仓。保持防守，观察龙头成对见顶信号。"
                    desc = f"综合风险读数达 {risk_score:.1f}%，处于历史极高估值区间，向上赔率较低。"

            elif pb_percentile <= 20:
                status = "🟢 周期大底区间 (高赔率黄金区)"
                advice = "已跌进买入区间，“不着急，慢慢买”，分批左侧建仓，博弈 3~4 倍景气修复赔率。"
                desc = f"PB 处于近 {years_back} 年历史极低位 ({pb_percentile:.1f}%)。周期规律表明：股价底总是在行业亏损恐慌中先于业绩底形成。"

            elif 20 < pb_percentile <= 45:
                status = "🟡 周期启动初段 (价值修复)"
                advice = "“涨得慢但很稳”，估值修复开始，坚定持股，不要被短线震荡轻易甩下车。"
                desc = f"PB 分位数脱离底部进入低估修复区（{pb_percentile:.1f}%），市场将信将疑，业绩拐点逐步酝酿中。"

            else:
                status = "🔵 景气上升中段 (让利润奔跑)"
                advice = "享受周期景气红利，持股待涨，密切监控 90% 风险红线与换手率异常。"
                desc = f"处于周期中轴扩张阶段，当前风险读数 {risk_score:.1f}%，离大顶极值区尚有空间。"

            # --- 页面展示仪表盘 ---
            st.subheader(f"📌 周期定位：{status}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("最新收盘价", f"¥ {curr_price:.2f}")
            c1.caption(f"当前 PB: {curr_pb:.2f}")

            c2.metric("PB 历史分位数", f"{pb_percentile:.1f} %", 
                      delta="周期大底" if pb_percentile<=20 else ("极度高估" if pb_percentile>=85 else "中性区间"))
            c2.caption(f"基准: 近 {years_back} 年历史分位")

            c3.metric("当日换手率", f"{curr_turnover:.2f} %", 
                      delta="过热" if curr_turnover>=7.0 else "温和", delta_color="inverse")
            c3.caption("市场交投活跃度")

            c4.metric("综合风险读数 (警戒: 91%)", f"{risk_score:.1f} %", 
                      delta="触碰预警" if risk_score>=90 else "安全", delta_color="inverse")
            c4.caption("长短周期风险综合打分")

            st.markdown(f"""
            > **🎯 战术应对指南**：  
            > **{advice}**  
            > *逻辑支撑*：{desc}
            """)

            # --- 走势与预警图表 ---
            st.markdown("### 📈 估值分位与周期预警通道")
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=df.index, y=df['pb'], name="PB 走势", line=dict(color="#1f77b4", width=1.8)))

            pb_bottom = df['pb'].quantile(0.15)
            pb_top = df['pb'].quantile(0.90)

            fig.add_hline(y=pb_bottom, line_dash="dash", line_color="green", annotation_text="周期底部线 (15%分位)")
            fig.add_hline(y=pb_top, line_dash="dash", line_color="red", annotation_text="高位警戒线 (90%分位)")

            fig.update_layout(height=420, margin=dict(l=20, r=20, t=30, b=20),
                              xaxis_title="日期", yaxis_title="PB (市净率)", hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)
