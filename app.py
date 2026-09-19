import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
from datetime import datetime, timedelta

# 页面基础配置
st.set_page_config(page_title="强周期股票周期拐点诊断仪", layout="wide", page_icon="📈")

st.title("📊 强周期股票周期拐点诊断仪")
st.caption("基于雪球「律动周期研究所」框架：将感觉变为读数（ROE下行末端+PB底 / 91%风险+换手率预警）")

# 统一请求头（伪装正常浏览器，防止被切断连接）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/"
}

# 1. 股票名称/代码自动联想搜索函数
@st.cache_data(ttl=86400)
def search_stock(keyword):
    """支持输入中文名称或拼音/代码，自动匹配市场 secid"""
    url = f"https://searchapi.eastmoney.com/api/suggest/get?input={keyword}&type=14"
    try:
        r = requests.get(url, headers=HEADERS, timeout=5)
        data = r.json()
        items = data.get("QuotationCodeTable", {}).get("Data", [])
        if not items:
            return None
        # 优先取 A 股 (Classify == AStock)
        a_stocks = [x for x in items if x.get("Classify") == "AStock"]
        target = a_stocks[0] if a_stocks else items[0]
        
        code = target["Code"]
        name = target["Name"]
        # 东方财富市场标识：0为深市/京市，1为沪市
        market_id = target["SecurityTypeName"]
        secid = f"1.{code}" if "沪" in market_id or code.startswith("6") else f"0.{code}"
        return {"code": code, "name": name, "secid": secid}
    except Exception:
        return None

# 2. 东方财富官方直连数据抓取函数 (稳定抗封)
@st.cache_data(ttl=1800)
def fetch_eastmoney_data(secid, years=5):
    # (1) 获取实时行情与当前市净率 PB
    quote_url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f43,f57,f58,f167,f168,f169,f170"
    r_quote = requests.get(quote_url, headers=HEADERS, timeout=6)
    q_data = r_quote.json().get("data", {})
    if not q_data or q_data.get("f43") == "-":
        return None, "无法获取该股票的实时行情"

    curr_price = float(q_data.get("f43", 0)) / 100.0  # 最新收盘价
    curr_pb = float(q_data.get("f167", 0)) / 100.0 if q_data.get("f167") != "-" else 0.0 # 当前 PB
    curr_turnover = float(q_data.get("f168", 0)) / 100.0 if q_data.get("f168") != "-" else 0.0 # 当日换手率

    # (2) 获取历史日 K 线与换手率（前复权）
    beg_date = (datetime.now() - timedelta(days=years * 365)).strftime("%Y%m%d")
    kline_url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
        f"secid={secid}&klt=101&fqt=1&beg={beg_date}&end=20500101&"
        f"fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"
    )
    r_kline = requests.get(kline_url, headers=HEADERS, timeout=8)
    k_data = r_kline.json().get("data", {})
    klines = k_data.get("klines", [])
    if not klines:
        return None, "获取历史行情为空"

    # 解析 K 线数据: 日期, 开盘, 收盘, 最高, 最低, 成交量, 成交额, 振幅, 涨跌幅, 涨跌额, 换手率
    records = []
    for k in klines:
        items = k.split(",")
        records.append({
            "日期": pd.to_datetime(items[0]),
            "收盘": float(items[2]),
            "最高": float(items[3]),
            "最低": float(items[4]),
            "换手率": float(items[10])
        })
    df = pd.DataFrame(records).set_index("日期")

    # (3) 构造历史连续 PB 走势曲线
    # 每股净资产 BPS = 最新收盘 / 最新PB
    if curr_pb > 0:
        bps_est = curr_price / curr_pb
        df['pb'] = (df['收盘'] / bps_est).round(2)
    else:
        df['pb'] = 1.0

    meta = {
        "curr_price": curr_price,
        "curr_pb": curr_pb,
        "curr_turnover": curr_turnover
    }
    return (df, meta), None

# --- 侧边栏：交互输入 ---
with st.sidebar:
    st.header("⚙️ 股票搜索与设置")
    
    # 快捷填入按钮
    st.write("快捷选择经典周期标的：")
    cols = st.columns(3)
    preset = None
    if cols[0].button("蓝思科技"): preset = "蓝思科技"
    if cols[1].button("京东方A"): preset = "京东方A"
    if cols[2].button("TCL科技"): preset = "TCL科技"
    
    cols2 = st.columns(3)
    if cols2[0].button("韦尔股份"): preset = "韦尔股份"
    if cols2[1].button("紫金矿业"): preset = "紫金矿业"
    if cols2[2].button("万华化学"): preset = "万华化学"

    # 输入框：支持中文名称、拼音或代码
    user_input = st.text_input("输入股票名称或代码", value=preset if preset else "蓝思科技")
    years_back = st.slider("历史回溯周期（年）", min_value=3, max_value=10, value=5)

# --- 主逻辑计算 ---
if user_input:
    with st.spinner(f"正在搜索并解析「{user_input}」的数据..."):
        stock_info = search_stock(user_input.strip())
        
    if not stock_info:
        st.error(f"❌ 未找到与「{user_input}」匹配的 A 股上市公司，请核对股票名称（如输入：蓝思科技、京东方A、中信证券）。")
    else:
        st.success(f"🎯 成功识别股票：**{stock_info['name']}** (代码: `{stock_info['code']}`)")
        
        with st.spinner("正在直连拉取数据并计算周期读数..."):
            res, err = fetch_eastmoney_data(stock_info['secid'], years=years_back)

        if err:
            st.error(f"数据加载异常: {err}")
        else:
            df, meta = res
            curr_price = meta["curr_price"]
            curr_pb = meta["curr_pb"]
            curr_turnover = meta["curr_turnover"]

            # --- 周期量化核心指标计算 ---
            # 1. PB 历史分位数 (0% ~ 100%)
            pb_percentile = (df['pb'] < curr_pb).mean() * 100.0

            # 2. 换手率近 1 年分位数
            one_year_df = df.last('365D')
            turnover_percentile = (one_year_df['换手率'] < curr_turnover).mean() * 100.0

            # 3. 综合风险读数（PB 分位数 70% + 价格波动通道 30%）
            roll_high = df['收盘'].rolling(250, min_periods=30).max().iloc[-1]
            roll_low = df['收盘'].rolling(250, min_periods=30).min().iloc[-1]
            price_pos = (curr_price - roll_low) / (roll_high - roll_low) * 100.0 if roll_high > roll_low else 50.0
            risk_score = 0.7 * pb_percentile + 0.3 * price_pos

            # --- 周期拐点状态判定 ---
            if risk_score >= 90:
                if turnover_percentile >= 75 or curr_turnover >= 6.0:
                    status = "🔴 周期大顶 / 高危预警 (双条件触发)"
                    action_advice = "短线与周期风险极高！模型建议：盘中至少减仓 1/3 锁定利润，防范 40%+ 级别深幅回撤。"
                    desc = f"综合风险读数摸到 {risk_score:.1f}%（超 90% 警戒线），且换手率处于年内高位（{curr_turnover:.2f}%），长短周期同时共振预警。"
                else:
                    status = "🟠 周期极值高位区 (估值过热)"
                    action_advice = "估值泡沫化，不宜追高加仓。保持防守，等待换手率异动或成对龙头见顶信号。"
                    desc = f"当前估值进入历史极高分位（风险读数 {risk_score:.1f}%），赔率已极低。"

            elif pb_percentile <= 20:
                status = "🟢 周期大底区间 (高赔率黄金区)"
                action_advice = "已跌进买入区间，“不着急，慢慢买”，分批左侧建仓，博弈 3~4 倍景气反转赔率。"
                desc = f"PB 处于近 {years_back} 年历史极低位 ({pb_percentile:.1f}%)。强周期规律表明：股价底总是在 ROE 恶化、全行业亏损恐慌中完成。"

            elif 20 < pb_percentile <= 45:
                status = "🟡 周期启动初段 (价值修复)"
                action_advice = "“涨得慢但很稳”，估值修复开始，坚定持股，不要被短线小波动甩下车。"
                desc = f"PB 分位脱离底部低谷（{pb_percentile:.1f}%），市场在犹疑中震荡抬升，业绩拐点正在酝酿。"

            else:
                status = "🔵 景气上升中段 (让利润奔跑)"
                action_advice = "享受景气向上红利，耐心持股，密切关注 90% 风险红线与换手率异动。"
                desc = f"周期中段扩张期，当前风险读数 {risk_score:.1f}%，离大顶极值区尚有空间。"

            # --- 仪表盘展现 ---
            st.subheader(f"📌 周期定位：{status}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("最新收盘价", f"¥ {curr_price:.2f}")
            c1.caption(f"当前 PB: {curr_pb:.2f}")

            c2.metric("PB 历史分位数", f"{pb_percentile:.1f} %", 
                      delta="周期大底" if pb_percentile<=20 else ("极度泡沫" if pb_percentile>=85 else "中性区间"))
            c2.caption(f"基准: 近 {years_back} 年历史分位")

            c3.metric("当日换手率", f"{curr_turnover:.2f} %", 
                      delta="高换手过热" if turnover_percentile>=75 else "正常", delta_color="inverse")
            c3.caption(f"处于近 1 年 {turnover_percentile:.1f}% 分位")

            c4.metric("综合风险读数 (警戒: 91%)", f"{risk_score:.1f} %", 
                      delta="触碰预警" if risk_score>=90 else "安全", delta_color="inverse")
            c4.caption("长短周期风险综合读数")

            # 策略指引卡片
            st.markdown(f"""
            > **🎯 战术应对指南**：  
            > **{action_advice}**  
            > *逻辑支撑*：{desc}
            """)

            # --- 图表：PB 走势与周期预警红绿线 ---
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
