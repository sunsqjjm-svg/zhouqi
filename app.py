import streamlit as st
import pandas as pd
import numpy as np
import requests
import json
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime

# 页面基础配置
st.set_page_config(page_title="强周期股票双信号拐点诊断仪", layout="wide", page_icon="🎯")

st.title("🎯 强周期股票：PB + ROE 双信号拐点诊断仪")
st.caption("严格遵循雪球「律动周期研究所」逻辑：股价底领先业绩底｜ROE下行末端 + PB底部 = 价格底｜ROE上行 = 已到半山腰")

# 1. 股票代码转换 (腾讯/新浪前缀)
def get_symbol_prefix(code):
    code = str(code).strip()
    if code.startswith('6') or code.startswith('9'):
        return f"sh{code}"
    elif code.startswith('0') or code.startswith('3'):
        return f"sz{code}"
    elif code.startswith('4') or code.startswith('8'):
        return f"bj{code}"
    return f"sz{code}"

# 2. 股票搜索
@st.cache_data(ttl=86400)
def search_stock(keyword):
    keyword = keyword.strip()
    if keyword.isdigit() and len(keyword) == 6:
        return {"code": keyword, "name": keyword, "secid": get_symbol_prefix(keyword)}

    url = f"https://suggest3.sinajs.cn/suggest/type=11,12,13,14,15&key={keyword}"
    headers = {"Referer": "https://finance.sina.com.cn", "User-Agent": "Mozilla/5.0"}
    try:
        r = requests.get(url, headers=headers, timeout=5)
        text = r.text
        if "suggestvalue=" in text:
            val = text.split('="')[1].split('";')[0]
            if val:
                first = val.split(";")[0].split(",")
                return {"code": first[2], "name": first[0], "secid": first[3]}
    except Exception:
        pass

    preset_map = {
        "蓝思科技": "sz300433", "京东方A": "sz000725", "中钢国际": "sz000928",
        "TCL科技": "sz000100", "韦尔股份": "sh603501", "紫金矿业": "sh601899", "万华化学": "sh600309"
    }
    for k, v in preset_map.items():
        if k in keyword:
            return {"code": v[2:], "name": k, "secid": v}
    return None

# 3. 获取实时行情与历史 K 线
@st.cache_data(ttl=1800)
def fetch_quote_and_kline(secid, years=4):
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com"}
    
    # (1) 实时行情 (腾讯财经)
    r_q = requests.get(f"https://qt.gtimg.cn/q={secid}", headers=headers, timeout=6)
    r_q.encoding = "gbk"
    data_parts = r_q.text.split('="')[1].split('~')
    stock_name = data_parts[1]
    curr_price = float(data_parts[3])
    curr_turnover = float(data_parts[38]) if data_parts[38] != "" else 0.0
    curr_pb = float(data_parts[46]) if len(data_parts) > 46 and data_parts[46] != "" else 1.0

    # (2) 历史日K线 (新浪财经)
    datalen = min(years * 250, 1020)
    k_url = f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData?symbol={secid}&scale=240&ma=no&datalen={datalen}"
    r_k = requests.get(k_url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
    kline_data = r_k.json()
    
    records = []
    for item in kline_data:
        records.append({
            "日期": pd.to_datetime(item["day"]),
            "收盘": float(item["close"]),
            "换手率": curr_turnover # 辅助估算
        })
    df = pd.DataFrame(records).set_index("日期").sort_index()

    # 构造历史 PB 通道
    bps = curr_price / curr_pb if curr_pb > 0 else 1.0
    df['pb'] = (df['收盘'] / bps).round(2)

    meta = {
        "stock_name": stock_name,
        "curr_price": curr_price,
        "curr_pb": curr_pb,
        "curr_turnover": curr_turnover
    }
    return df, meta

# 4. 获取历史季度 ROE (加权净资产收益率，支持海外IP)
@st.cache_data(ttl=86400)
def fetch_roe_history(code):
    """从官方数据中心抓取近 8 个季度的真实加权 ROE"""
    url = (
        f"https://datacenter-web.eastmoney.com/api/data/v1/get?"
        f"reportName=RPT_LICO_FN_CPD&filter=(SECURITY_CODE%3D%22{code}%22)&"
        f"columns=REPORT_DATE,WEIGHTED_ROE,BASIC_EPS&sortTypes=-1&sortColumns=REPORT_DATE&pageSize=8"
    )
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
    try:
        r = requests.get(url, headers=headers, timeout=6)
        res = r.json()
        data = res.get("result", {}).get("data", [])
        if not data:
            return None
        
        roe_records = []
        for d in data:
            date_str = d.get("REPORT_DATE", "")[:10]
            roe_val = d.get("WEIGHTED_ROE")
            if roe_val is not None:
                roe_records.append({
                    "报告期": date_str,
                    "ROE(%)": float(roe_val)
                })
        df_roe = pd.DataFrame(roe_records).sort_values("报告期")
        return df_roe
    except Exception:
        return None

# --- 侧边栏：操作配置 ---
with st.sidebar:
    st.header("⚙️ 标的诊断设置")
    c1, c2, c3 = st.columns(3)
    preset = None
    if c1.button("蓝思科技"): preset = "蓝思科技"
    if c2.button("京东方A"): preset = "京东方A"
    if c3.button("中钢国际"): preset = "中钢国际"

    c4, c5, c6 = st.columns(3)
    if c4.button("TCL科技"): preset = "TCL科技"
    if c5.button("紫金矿业"): preset = "紫金矿业"
    if c6.button("万华化学"): preset = "万华化学"

    user_input = st.text_input("输入股票名称或代码", value=preset if preset else "蓝思科技")
    years_back = st.slider("估值回溯跨度（年）", min_value=3, max_value=5, value=4)

# --- 主页面计算 ---
if user_input:
    with st.spinner(f"正在全网定位「{user_input}」并提取财务与行情..."):
        info = search_stock(user_input)

    if not info:
        st.error(f"未找到标的「{user_input}」，请检查名称或代码。")
    else:
        try:
            df, meta = fetch_quote_and_kline(info['secid'], years=years_back)
            df_roe = fetch_roe_history(info['code'])
            
            curr_price = meta["curr_price"]
            curr_pb = meta["curr_pb"]
            curr_turnover = meta["curr_turnover"]
            name = meta["stock_name"]
            
            st.success(f"🎯 诊断标的：**{name}** (`{info['code']}`)")

            # --- 1. PB 读数计算 ---
            pb_percentile = (df['pb'] < curr_pb).mean() * 100.0
            is_pb_bottom = pb_percentile <= 25.0  # 信号1：PB是否处于底部

            # --- 2. ROE 读数与趋势计算 ---
            roe_status = "数据暂缺"
            is_roe_declining_or_bottom = False
            is_roe_rebounding = False
            latest_roe = 0.0

            if df_roe is not None and len(df_roe) >= 2:
                latest_roe = df_roe.iloc[-1]["ROE(%)"]
                prev_roe = df_roe.iloc[-2]["ROE(%)"]
                # 寻找近4-8个季度低点
                min_roe = df_roe["ROE(%)"].min()
                max_roe = df_roe["ROE(%)"].max()

                # 判断 ROE 状态
                if latest_roe <= prev_roe or abs(latest_roe - min_roe) < 0.5 or latest_roe < 2.0:
                    roe_status = "📉 下行末端 / 低谷冰点期"
                    is_roe_declining_or_bottom = True
                elif latest_roe > prev_roe and latest_roe < (max_roe * 0.75):
                    roe_status = "📈 见底反弹中 / 业绩转好"
                    is_roe_rebounding = True
                else:
                    roe_status = "🔥 景气高位巅峰"

            # --- 3. 综合长短周期风险打分 ---
            roll_high = df['收盘'].rolling(250, min_periods=30).max().iloc[-1]
            roll_low = df['收盘'].rolling(250, min_periods=30).min().iloc[-1]
            price_pos = (curr_price - roll_low) / (roll_high - roll_low) * 100.0 if roll_high > roll_low else 50.0
            risk_score = 0.7 * pb_percentile + 0.3 * price_pos

            # --- 4. 周期位置核心判定（严格对应原帖） ---
            # 状态 A：大顶警示 (风险读数 >= 90% 叠加高换手)
            if risk_score >= 90:
                if curr_turnover >= 6.0:
                    stage = "🔴 周期大顶 / 高危预警 (双条件确认)"
                    guidance = "【执行离场】长短周期指标共振见顶！文章策略：盘中至少减仓 1/3 锁定收益，严防 40%+ 级回撤。"
                else:
                    stage = "🟠 周期极值高位区 (估值过热)"
                    guidance = "【严禁追高】估值已达历史泡沫区（读数>90%），赔率极低，静待成对龙头见顶信号。"

            # 状态 B：双信号同时满足 = 周期大底！
            elif is_pb_bottom and is_roe_declining_or_bottom:
                stage = "🟢 周期大底：双信号同时触发！(价格底成立)"
                guidance = "【黄金买点】ROE 仍在下行受‘毒打’+ PB 进入历史低谷区！严格印证‘股价底领先于业绩底’规律，策略：‘不着急，慢慢买’，左侧买入博弈 3~4 倍赔率！"

            # 状态 C：ROE 已经反弹 = 半山腰！
            elif is_roe_rebounding and (20 < pb_percentile <= 55):
                stage = "🟡 周期反弹中段：已到半山腰"
                guidance = "【坚定持股】正如作者所言：‘等你看到业绩转好、ROE上行时，股价往往已经到半山腰了’。走势‘涨得慢但很稳’，不要被短线小震荡洗出局！"

            # 状态 D：普通筑底或修复
            elif is_pb_bottom and not is_roe_declining_or_bottom:
                stage = "🟢 估值底部区 (等待业绩消化)"
                guidance = "PB 已至历史低估区间，但 ROE 尚未进入冰点出清，保持分批关注。"

            else:
                stage = "🔵 景气扩张主升段 (让利润奔跑)"
                guidance = "周期中轴向上扩张，盈利与估值双击中，安心持有，紧盯 90% 风险红线。"

            # ================= 页面展示 =================
            st.subheader(f"📌 周期裁决：{stage}")

            # 专设：PB + ROE 双信号对撞仪表盘
            st.markdown("#### ⚡ 顶底核心双信号对撞校验器")
            s1, s2, s3 = st.columns(3)
            
            with s1:
                st.metric("信号 1：PB 估值位置", f"{curr_pb:.2f} (分位: {pb_percentile:.1f}%)")
                if is_pb_bottom:
                    st.success("✅ 满足：处于历史大底极值区 (≤25%)")
                elif pb_percentile >= 85:
                    st.error("🚨 警告：处于历史泡沫顶部区 (≥85%)")
                else:
                    st.info("ℹ️ 中性：处于正常周期区间")

            with s2:
                st.metric("信号 2：最新加权 ROE", f"{latest_roe:.2f} %")
                if is_roe_declining_or_bottom:
                    st.success(f"✅ 满足：{roe_status}")
                elif is_roe_rebounding:
                    st.warning(f"⚠️ 警惕：{roe_status}")
                else:
                    st.info(f"ℹ️ 当前状态：{roe_status}")

            with s3:
                st.metric("长短周期综合风险读数", f"{risk_score:.1f} %", delta="91% 极值红线", delta_color="inverse")
                if risk_score >= 90:
                    st.error("🚨 触碰 91% 周期高位危险区！")
                elif risk_score <= 30:
                    st.success("🛡️ 处于周期绝对安全低估区")
                else:
                    st.info("⚖️ 处于周期中轴运行段")

            # 战术行动指示牌
            st.markdown(f"""
            > **🎯 操作应对策略**：  
            > **{guidance}**
            """)

            # ================= 图表联动：PB 估值通道 + ROE 季度走势 =================
            st.markdown("### 📈 走势双图对账：PB 估值通道 与 季度 ROE 轨迹")
            
            # 创建双子图
            fig = make_subplots(rows=2, cols=1, shared_xaxes=False, vertical_spacing=0.15,
                                subplot_titles=("PB 走势与周期预警通道", "近 8 个季度 ROE 变动轨迹 (检验业绩底与股价底时差)"))

            # 上图：PB 曲线与红绿预警线
            fig.add_trace(go.Scatter(x=df.index, y=df['pb'], name="PB 走势", line=dict(color="#1f77b4", width=1.8)), row=1, col=1)
            pb_bot = df['pb'].quantile(0.15)
            pb_tp = df['pb'].quantile(0.90)
            fig.add_hline(y=pb_bot, line_dash="dash", line_color="green", annotation_text="PB 大底线 (15%分位)", row=1, col=1)
            fig.add_hline(y=pb_tp, line_dash="dash", line_color="red", annotation_text="高位警戒线 (90%分位)", row=1, col=1)

            # 下图：ROE 季度柱状走势
            if df_roe is not None and not df_roe.empty:
                colors = ['#2ca02c' if v < 2.0 else ('#d62728' if v > 10.0 else '#ff7f0e') for v in df_roe["ROE(%)"]]
                fig.add_trace(go.Bar(x=df_roe["报告期"], y=df_roe["ROE(%)"], name="加权 ROE(%)", marker_color=colors, text=df_roe["ROE(%)"], textposition='auto'), row=2, col=1)

            fig.update_layout(height=650, margin=dict(l=20, r=20, t=40, b=20), hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

        except Exception as e:
            st.error(f"数据解析异常: {e}")
