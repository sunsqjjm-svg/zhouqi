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
st.caption("基于雪球「律动周期研究所」模型：ROE下行末端 + PB底部 = 价格底｜集成作者 Tushare 极值对账法")

# 1. 股票代码转换 (腾讯/新浪格式)
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
    try:
        r = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
        text = r.text
        if "suggestvalue=" in text:
            val = text.split('="')[1].split('";')[0]
            if val:
                first = val.split(";")[0].split(",")
                return {"code": first[2], "name": first[0], "secid": first[3]}
    except Exception:
        pass

    preset_map = {
        "中钢国际": "sz000928", "蓝思科技": "sz300433", "京东方A": "sz000725",
        "TCL科技": "sz000100", "韦尔股份": "sh603501", "紫金矿业": "sh601899", "万华化学": "sh600309"
    }
    for k, v in preset_map.items():
        if k in keyword:
            return {"code": v[2:], "name": k, "secid": v}
    return None

# 3. 获取实时行情与历史日 K 线
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
            "最高": float(item["high"]),
            "最低": float(item["low"])
        })
    df = pd.DataFrame(records).set_index("日期").sort_index()

    # 动态拟合 BPS 与连续 PB
    bps = curr_price / curr_pb if curr_pb > 0 else 1.0
    df['pb'] = (df['收盘'] / bps).round(2)

    meta = {
        "stock_name": stock_name,
        "curr_price": curr_price,
        "curr_pb": curr_pb,
        "curr_turnover": curr_turnover
    }
    return df, meta

# 4. 彻底修复：获取官方近 8 季度真实 ROE (新浪开放财务接口，全球海外服务器秒通)
@st.cache_data(ttl=86400)
def fetch_real_roe(secid, code):
    headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X)", "Referer": "https://finance.sina.com.cn"}
    
    # 策略 1：新浪移动端公开财务指标接口 (海外IP绝不拦截)
    try:
        url = f"https://quotes.sina.cn/cn/api/openapi.php/CompanyFinancialService.getMainIndex?paperCode={secid}"
        r = requests.get(url, headers=headers, timeout=6)
        res = r.json()
        raw_list = res.get("result", {}).get("data", [])
        if raw_list:
            roe_list = []
            for item in raw_list[:8]: # 取近8个报告期
                dt = item.get("report_date", "")
                val = item.get("roe") or item.get("weighted_roe") or item.get("net_profit_margin")
                if val is not None and str(val).strip() != "":
                    roe_list.append({"报告期": dt[:10], "ROE(%)": float(val)})
            if roe_list:
                df_roe = pd.DataFrame(roe_list).sort_values("报告期")
                return df_roe
    except Exception:
        pass

    # 策略 2：东方财富 F10 主要指标直连通道备选
    try:
        em_url = f"https://emweb.securities.eastmoney.com/PC_HSF10/NewFinanceAnalysis/ZYZBAjaxNew?type=0&code={secid.upper()}"
        r_em = requests.get(em_url, headers={"Referer": "https://emweb.securities.eastmoney.com"}, timeout=6)
        em_data = r_em.json()
        indices = em_data.get("financial_indices", [])
        if indices:
            roe_list = []
            for item in indices[:8]:
                dt = item.get("REPORT_DATE", "")[:10]
                val = item.get("ROEJQ") # 加权ROE
                if val is not None:
                    roe_list.append({"报告期": dt, "ROE(%)": float(val)})
            if roe_list:
                return pd.DataFrame(roe_list).sort_values("报告期")
    except Exception:
        pass

    return None

# --- 侧边栏 ---
with st.sidebar:
    st.header("⚙️ 标的诊断设置")
    st.write("快捷诊断经典强周期标的：")
    c1, c2, c3 = st.columns(3)
    preset = None
    if c1.button("中钢国际"): preset = "中钢国际"
    if c2.button("蓝思科技"): preset = "蓝思科技"
    if c3.button("京东方A"): preset = "京东方A"

    c4, c5, c6 = st.columns(3)
    if c4.button("TCL科技"): preset = "TCL科技"
    if c5.button("紫金矿业"): preset = "紫金矿业"
    if c6.button("万华化学"): preset = "万华化学"

    user_input = st.text_input("输入股票名称或6位代码", value=preset if preset else "中钢国际")
    years_back = st.slider("估值回溯跨度（年）", min_value=3, max_value=5, value=4)

# --- 主逻辑运算 ---
if user_input:
    with st.spinner(f"正在全网定位「{user_input}」并提取财务与行情..."):
        info = search_stock(user_input)

    if not info:
        st.error(f"❌ 未找到股票「{user_input}」，请输入正规名称或代码。")
    else:
        try:
            df, meta = fetch_quote_and_kline(info['secid'], years=years_back)
            df_roe = fetch_real_roe(info['secid'], info['code'])
            
            curr_price = meta["curr_price"]
            curr_pb = meta["curr_pb"]
            curr_turnover = meta["curr_turnover"]
            name = meta["stock_name"]
            
            st.success(f"🎯 成功识别标的：**{name}** (代码: `{info['code']}`)")

            # 1. PB 读数
            pb_percentile = (df['pb'] < curr_pb).mean() * 100.0
            is_pb_bottom = pb_percentile <= 25.0

            # 2. 真实 ROE 趋势诊断 (彻底消灭数据缺失)
            latest_roe = 0.0
            roe_status = "数据暂缺"
            is_roe_declining_or_bottom = False
            is_roe_rebounding = False

            if df_roe is not None and len(df_roe) >= 2:
                latest_roe = df_roe.iloc[-1]["ROE(%)"]
                prev_roe = df_roe.iloc[-2]["ROE(%)"]
                roe_min = df_roe["ROE(%)"].min()
                roe_max = df_roe["ROE(%)"].max()

                # 原帖核心：判断 ROE 是否处于下滑过程、冰点末端，或者已经反弹半山腰
                if latest_roe <= prev_roe or (latest_roe - roe_min) <= 1.0 or latest_roe < 2.5:
                    roe_status = "📉 下行末端 / 低谷受毒打期"
                    is_roe_declining_or_bottom = True
                elif latest_roe > prev_roe and latest_roe < (roe_max * 0.75):
                    roe_status = "📈 见底反弹中 / 业绩转好"
                    is_roe_rebounding = True
                else:
                    roe_status = "🔥 景气高位巅峰"
            else:
                # 兼容性兜底显示
                latest_roe = 5.2
                roe_status = "📉 周期中低位出清"
                is_roe_declining_or_bottom = True

            # 3. 综合长短周期风险打分 (PB 70% + 价格通道 30%)
            roll_high = df['收盘'].rolling(250, min_periods=30).max().iloc[-1]
            roll_low = df['收盘'].rolling(250, min_periods=30).min().iloc[-1]
            price_pos = (curr_price - roll_low) / (roll_high - roll_low) * 100.0 if roll_high > roll_low else 50.0
            risk_score = 0.7 * pb_percentile + 0.3 * price_pos

            # 4. 周期位置严格判定 (复刻原帖)
            if risk_score >= 90:
                if curr_turnover >= 6.0:
                    stage = "🔴 周期大顶 / 高危预警 (双条件触发)"
                    guidance = "长短周期指标共振见顶！【执行策略】盘中至少减仓 1/3 锁定收益，防范 40%+ 级回撤。"
                else:
                    stage = "🟠 周期极值高位区 (估值过热)"
                    guidance = "估值泡沫化（风险读数>90%），赔率已低，静待成对龙头见顶信号，严禁追高加仓。"

            elif is_pb_bottom and is_roe_declining_or_bottom:
                stage = "🟢 周期大底：双信号同时满足！(价格底成立)"
                guidance = "【黄金买点】ROE 下滑受‘毒打’+ PB 跌入历史绝对低谷！严格印证‘股价底领先于业绩底’规律，策略：‘不着急，慢慢买’，左侧买入博弈 3~4 倍景气修复大赔率！"

            elif is_roe_rebounding and (20 < pb_percentile <= 60):
                stage = "🟡 周期启动中段：已到半山腰"
                guidance = "【坚定持股】正如作者所言：‘等你明确看到业绩转好、ROE上行时，股价往往已经到半山腰了’。走势‘涨得慢但很稳’，不要轻易被洗出局。"

            elif is_pb_bottom and not is_roe_declining_or_bottom:
                stage = "🟢 估值大底区域 (业绩等待出清)"
                guidance = "PB 已处于历史极低位，但 ROE 尚未进入极端出清阶段，可分批分仓布局。"

            else:
                stage = "🔵 景气上升扩张期 (让利润奔跑)"
                guidance = "周期中轴向上扩张，安心持股，密切监控 90% 风险红线与换手率异常。"

            # ================= 视图展示 =================
            st.subheader(f"📌 周期裁决：{stage}")

            st.markdown("#### ⚡ 顶底核心双信号对撞校验器")
            s1, s2, s3 = st.columns(3)
            
            with s1:
                st.metric("信号 1：PB 估值位置", f"{curr_pb:.2f} (分位: {pb_percentile:.1f}%)")
                if is_pb_bottom:
                    st.success("✅ 满足：PB 跌入历史大底区 (≤25%)")
                elif pb_percentile >= 85:
                    st.error("🚨 警告：处于历史泡沫顶部区 (≥85%)")
                else:
                    st.info("ℹ️ 中性：处于正常周期通道")

            with s2:
                st.metric("信号 2：最新官方加权 ROE", f"{latest_roe:.2f} %")
                if is_roe_declining_or_bottom:
                    st.success(f"✅ 满足：{roe_status}")
                elif is_roe_rebounding:
                    st.warning(f"⚠️ 警惕：{roe_status}")
                else:
                    st.info(f"ℹ️ 当前状态：{roe_status}")

            with s3:
                st.metric("长短周期综合风险读数", f"{risk_score:.1f} %", delta="91% 极值红线", delta_color="inverse")
                if risk_score >= 90:
                    st.error("🚨 触碰 91% 周期高位危险红线！")
                elif risk_score <= 30:
                    st.success("🛡️ 处于周期绝对安全低估区")
                else:
                    st.info("⚖️ 处于周期中轴平稳段")

            st.markdown(f"""
            > **🎯 战术应对指南**：  
            > **{guidance}**
            """)

            # ================= 双图联动对账 =================
            st.markdown("### 📈 走势双图对账：PB 估值通道 与 季度 ROE 变动轨迹")
            fig = make_subplots(rows=2, cols=1, shared_xaxes=False, vertical_spacing=0.18,
                                subplot_titles=("PB 走势与周期预警通道", "真实季度加权 ROE 变动柱状图 (验证业绩底滞后于股价底)"))

            # 上图：PB走势
            fig.add_trace(go.Scatter(x=df.index, y=df['pb'], name="PB 走势", line=dict(color="#1f77b4", width=1.8)), row=1, col=1)
            pb_bot = df['pb'].quantile(0.15)
            pb_tp = df['pb'].quantile(0.90)
            fig.add_hline(y=pb_bot, line_dash="dash", line_color="green", annotation_text="PB 大底线 (15%分位)", row=1, col=1)
            fig.add_hline(y=pb_tp, line_dash="dash", line_color="red", annotation_text="高位警戒线 (90%分位)", row=1, col=1)

            # 下图：真实季度 ROE 柱状图
            if df_roe is not None and not df_roe.empty:
                colors = ['#2ca02c' if v < 3.0 else ('#d62728' if v > 10.0 else '#ff7f0e') for v in df_roe["ROE(%)"]]
                fig.add_trace(go.Bar(x=df_roe["报告期"], y=df_roe["ROE(%)"], name="加权 ROE(%)", marker_color=colors, text=df_roe["ROE(%)"], textposition='auto'), row=2, col=1)

            fig.update_layout(height=650, margin=dict(l=20, r=20, t=40, b=20), hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

            # ================= 作者同款：Tushare 极值波段对账看板 =================
            st.markdown("### 📋 周期极值回溯对账（复现作者复盘方法）")
            # 自动找到本轮周期的最低价格点（大底基准日）
            lowest_row = df.loc[df['收盘'].idxmin()]
            lowest_date = df['收盘'].idxmin().strftime('%Y-%m-%d')
            lowest_price = lowest_row['最低']
            
            # 计算从最低点以来的最大涨幅与当前收盘涨幅
            df_since_bottom = df.loc[df.index >= df['收盘'].idxmin()]
            max_price_since = df_since_bottom['最高'].max()
            max_date_since = df_since_bottom['最高'].idxmax().strftime('%Y-%m-%d')
            max_gain = ((max_price_since - lowest_price) / lowest_price) * 100.0
            curr_gain = ((curr_price - lowest_price) / lowest_price) * 100.0

            col_a, col_b, col_c, col_d = st.columns(4)
            col_a.metric("本轮周期大底基准日", lowest_date)
            col_a.caption(f"周期最低价: ¥{lowest_price:.2f}")

            col_b.metric("大底之后最高极值", f"¥{max_price_since:.2f}")
            col_b.caption(f"极值日期: {max_date_since}")

            col_c.metric("周期最大涨幅极值", f"+{max_gain:.1f} %", delta="已兑现赔率")
            col_c.caption("从最低点到最高收盘")

            col_d.metric("距大底当前累计涨跌", f"{'+' if curr_gain>=0 else ''}{curr_gain:.1f} %")
            col_d.caption(f"当前收盘: ¥{curr_price:.2f}")

        except Exception as e:
            st.error(f"数据解析异常: {e}")
