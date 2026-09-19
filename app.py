import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timedelta

# 页面基础配置
st.set_page_config(page_title="强周期股票双信号拐点诊断仪", layout="wide", page_icon="🎯")

st.title("🎯 强周期股票：长短周期独立诊断仪")
st.caption("基于雪球「律动周期研究所」逻辑：股价底领先业绩底｜ROE下行末端 + PB底部 = 价格底｜10年宏观估值 + 近2年战术动能")

# 安全浮点数转换器
def safe_float(val, default=0.0):
    if val is None:
        return default
    s = str(val).replace("%", "").strip()
    if s in ["", "-", "--", "None", "null", "NaN"]:
        return default
    try:
        return float(s)
    except Exception:
        return default

# 1. 股票代码转换
def get_symbol_prefix(code):
    code = str(code).strip()
    if code.startswith('6') or code.startswith('9'):
        return f"sh{code}"
    elif code.startswith('0') or code.startswith('3'):
        return f"sz{code}"
    elif code.startswith('4') or code.startswith('8'):
        return f"bj{code}"
    return f"sz{code}"

def get_em_secid(code):
    code = str(code).strip()
    return f"1.{code}" if (code.startswith('6') or code.startswith('9')) else f"0.{code}"

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
        "中钢国际": "sz000928", "天康生物": "sz002100", "蓝思科技": "sz300433", 
        "京东方A": "sz000725", "TCL科技": "sz000100", "韦尔股份": "sh603501", "紫金矿业": "sh601899"
    }
    for k, v in preset_map.items():
        if k in keyword:
            return {"code": v[2:], "name": k, "secid": v}
    return None

# 3. 10 年大周期数据抓取与动态 BPS 估值引擎
@st.cache_data(ttl=300)
def fetch_stock_data(secid, code, years=10):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://finance.qq.com"}
    
    # 实时行情 (腾讯)
    r_q = requests.get(f"https://qt.gtimg.cn/q={secid}", headers=headers, timeout=6)
    r_q.encoding = "gbk"
    parts = r_q.text.split('="')[1].split('~')
    
    stock_name = parts[1]
    curr_price = float(parts[3])
    curr_turnover = safe_float(parts[38])
    curr_pe_ttm = safe_float(parts[39])
    curr_pe_dyn = safe_float(parts[52]) if len(parts) > 52 else 0.0
    curr_pb = safe_float(parts[46], default=1.0)

    records = []

    # 通道 1：东财 10 年高精度 K 线
    try:
        em_id = get_em_secid(code)
        em_url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get?"
            f"secid={em_id}&"
            "ut=7eea3edcaed7343ac48e18df4f617d8f&"
            "fields1=f1,f2,f3,f4,f5,f6&"
            "fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61&"
            "klt=101&fqt=1&end=20500101&lmt=2600"
        )
        r_em = requests.get(em_url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}, timeout=8)
        k_data = r_em.json().get("data", {})
        klines = k_data.get("klines", [])
        if len(klines) >= 1500:
            for k in klines:
                p = k.split(",")
                records.append({
                    "日期": datetime.strptime(p[0], "%Y-%m-%d"),
                    "收盘": float(p[2]),
                    "最高": float(p[3]),
                    "最低": float(p[4])
                })
    except Exception:
        pass

    # 通道 2：雅虎财经 10 年前复权直连
    if len(records) < 1500:
        try:
            records = []
            yf_suffix = "SS" if (code.startswith('6') or code.startswith('9')) else "SZ"
            yf_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.{yf_suffix}?range=10y&interval=1d"
            r_yf = requests.get(yf_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            res_yf = r_yf.json()
            chart_res = res_yf.get("chart", {}).get("result", [])
            if chart_res:
                timestamps = chart_res[0].get("timestamp", [])
                quote = chart_res[0].get("indicators", {}).get("quote", [{}])[0]
                adjclose_list = chart_res[0].get("indicators", {}).get("adjclose", [{}])[0].get("adjclose", [])
                closes = adjclose_list if adjclose_list else quote.get("close", [])
                highs = quote.get("high", [])
                lows = quote.get("low", [])
                for ts, c, h, l in zip(timestamps, closes, highs, lows):
                    if c is not None and h is not None and l is not None:
                        records.append({
                            "日期": datetime.fromtimestamp(ts),
                            "收盘": float(c),
                            "最高": float(h),
                            "最低": float(l)
                        })
        except Exception:
            pass

    # 通道 3：腾讯 1000 日兜底
    if not records:
        try:
            url_tx = f"https://web.ifzq.gtimg.cn/appstock/news/fqkline/get?param={secid},day,,,1000,qfq"
            res_tx = requests.get(url_tx, headers=headers, timeout=6).json()
            data_tx = res_tx.get("data", {}).get(secid, {})
            list_tx = data_tx.get("qfqday") or data_tx.get("day") or []
            for it in list_tx:
                records.append({
                    "日期": datetime.strptime(str(it[0])[:10], "%Y-%m-%d"),
                    "收盘": float(it[2]),
                    "最高": float(it[3]),
                    "最低": float(it[4])
                })
        except Exception:
            pass

    if not records:
        raise ValueError(f"未能获取到 {stock_name} 的历史行情数据，请刷新重试")

    df = pd.DataFrame(records).set_index("日期").sort_index()
    df = df[~df.index.duplicated(keep='first')]

    # 截断未来脏时间
    df = df[df.index <= datetime.now()]

    # 动态 BPS 复合增长回溯模型 (还原真实历史天花板)
    latest_bps = curr_price / curr_pb if curr_pb > 0 else 1.0
    latest_date = df.index[-1]
    
    delta_years = (latest_date - df.index).days / 365.25
    dynamic_bps = latest_bps / ((1.055) ** delta_years)
    
    df['pb'] = (df['收盘'] / dynamic_bps).round(2)

    # 20日平滑
    pb_smoothed = df['pb'].rolling(window=20, min_periods=1).mean()
    
    # 统计 10 年完整极值
    p_floor = float(df['pb'].quantile(0.02))
    p_cap = max(float(df['pb'].quantile(0.98)), 1.90)

    denom = p_cap - p_floor if p_cap > p_floor else 1.0
    raw_long_risk = ((pb_smoothed - p_floor) / denom) * 100.0
    df['long_risk'] = raw_long_risk.clip(0, 100).round(1)

    # 短周期 1 年动能通道 (近 250 日)
    roll_high = df['收盘'].rolling(250, min_periods=30).max()
    roll_low = df['收盘'].rolling(250, min_periods=30).min()
    df['short_risk'] = (((df['收盘'] - roll_low) / (roll_high - roll_low).replace(0, 1)) * 100.0).clip(0, 100).round(1)

    # 综合加权读数
    df['risk_score'] = (0.7 * df['long_risk'] + 0.3 * df['short_risk']).clip(0, 100).round(1)

    actual_years = round(len(df) / 244, 1)

    meta = {
        "stock_name": stock_name,
        "curr_price": curr_price,
        "curr_pb": curr_pb,
        "curr_turnover": curr_turnover,
        "pe_ttm": curr_pe_ttm,
        "pe_dyn": curr_pe_dyn,
        "pb_floor": p_floor,
        "pb_cap": p_cap,
        "actual_years": actual_years
    }
    return df, meta

# --- 侧边栏 ---
with st.sidebar:
    st.header("⚙️ 标的诊断设置")
    st.write("快捷诊断经典强周期标的：")
    c1, c2, c3 = st.columns(3)
    preset = None
    if c1.button("中钢国际"): preset = "中钢国际"
    if c2.button("天康生物"): preset = "天康生物"
    if c3.button("蓝思科技"): preset = "蓝思科技"

    c4, c5, c6 = st.columns(3)
    if c4.button("京东方A"): preset = "京东方A"
    if c5.button("TCL科技"): preset = "TCL科技"
    if c6.button("紫金矿业"): preset = "紫金矿业"

    user_input = st.text_input("输入股票名称或6位代码", value=preset if preset else "中钢国际")
    years_back = st.slider("大周期回溯跨度（年）", min_value=5, max_value=12, value=10, step=1, help="强周期行业设备与产能周期（朱格拉周期）通常历时7-10年，推荐10年完整视角。")

# --- 主逻辑计算 ---
if user_input:
    with st.spinner(f"正在全网调取「{user_input}」大周期数据..."):
        info = search_stock(user_input)

    if not info:
        st.error(f"❌ 未识别标的「{user_input}」，请输入规范名称或代码。")
    else:
        try:
            df, meta = fetch_stock_data(info['secid'], info['code'], years=years_back)
            
            curr_price = meta["curr_price"]
            curr_pb = meta["curr_pb"]
            curr_turnover = meta["curr_turnover"]
            pe_ttm = meta["pe_ttm"]
            pe_dyn = meta["pe_dyn"]
            name = meta["stock_name"]
            
            pb_floor = meta.get("pb_floor", float(df['pb'].quantile(0.02)))
            pb_cap = meta.get("pb_cap", float(df['pb'].quantile(0.98)))
            actual_span = meta.get("actual_years", 10.0)

            st.success(f"🎯 成功识别标的：**{name}** (代码: `{info['code']}`，已载入近 **{actual_span} 年** 完整大周期数据)")

            # 最新指标
            long_risk = df['long_risk'].iloc[-1]
            short_risk = df['short_risk'].iloc[-1]
            risk_score = df['risk_score'].iloc[-1]
            is_pb_bottom = long_risk <= 25.0

            # 真实 ROE 测算
            real_roe = (curr_pb / pe_ttm) * 100.0 if pe_ttm != 0 else 0.0
            
            if real_roe <= 0:
                roe_status = "📉 深幅亏损 / 行业至暗失血期"
                is_roe_declining = True
                is_roe_rebounding = False
                roe_desc = f"公司处于净亏损状态（ROE为 {real_roe:.2f}%），行业正在经历产能出清‘毒打’。"
            elif real_roe < 6.0:
                roe_status = "📉 处于下行末端 / 低谷冰点期"
                is_roe_declining = True
                is_roe_rebounding = False
                roe_desc = f"ROE 处于低谷冰点（{real_roe:.2f}%），盈利恶化趋缓，处于出清磨底期。"
            elif pe_dyn > 0 and pe_dyn < pe_ttm * 0.90:
                roe_status = "📈 见底反弹中 / 业绩转好"
                is_roe_declining = False
                is_roe_rebounding = True
                roe_desc = "最新季度业绩出现改善，ROE 步入回升通道，走势进入半山腰。"
            else:
                roe_status = "⚖️ 处于中性常态阶段"
                is_roe_declining = False
                is_roe_rebounding = False
                roe_desc = "盈利水平处于常态化波动区间。"

            # 周期位置核心裁决
            if long_risk >= 85 and short_risk >= 85:
                stage = "🔴 周期大顶：长短周期同时触顶 (双共振清仓)"
                guidance = "10年长周期估值泡沫化 + 短周期情绪极限超买！触发作者最高级别大顶预警，坚决分批离场防 40%+ 级暴跌！"
            elif risk_score >= 90:
                stage = "🟠 周期极值高位区 (估值过热)"
                guidance = "综合风险读数突破 90%，赔率已极低，模型建议：盘中卖掉 1/3 锁定收益，留纯利润奔跑。"
            elif is_pb_bottom and is_roe_declining:
                stage = "🟢 周期大底：双信号同时满足！(价格底成立)"
                guidance = f"【黄金买点】10年长周期 PB 跌入绝对大底（≤25%）+ ROE 遭受毒打！完全符合‘股价底领先业绩底’规律，策略：‘不着急，慢慢买’！"
            elif is_roe_declining and not is_pb_bottom:
                stage = "⚠️ 周期下行出清期：估值未到底 (防接飞刀)"
                guidance = f"【防盲目抄底】业绩虽然处于亏损毒打期，但 10 年估值分位（{long_risk:.1f}%）未跌透，绝不能盲目接飞刀！"
            elif is_roe_rebounding and (20 < long_risk <= 60):
                stage = "🟡 周期启动中段：已到半山腰"
                guidance = "【坚定持股】正如作者所言：‘等你看到业绩转好、ROE上行时，股价往往已经到半山腰了’。走势‘涨得慢但很稳’。"
            elif long_risk > 50 and is_roe_rebounding:
                stage = "🔵 景气上升扩张期 (让利润奔跑)"
                guidance = "盈利与估值双击向上，安心持股，密切监控长短周期向 90% 警戒线的推移。"
            else:
                stage = "⚖️ 周期中轴过渡期 (耐心观望)"
                guidance = "处于多空平衡期，静待更极端的长短周期信号出现。"

            # ================= 视图展示 =================
            st.subheader(f"📌 周期裁决：{stage}")

            # 4 个独立指标卡
            st.markdown("#### ⚡ 周期独立读数与核心信号")
            c1, c2, c3, c4 = st.columns(4)
            
            with c1:
                st.metric("信号 1：PB 估值位置", f"{curr_pb:.2f}")
                st.caption("当前市净率绝对值")

            with c2:
                st.metric("信号 2：真实 ROE 状态", f"{real_roe:.2f} %", delta="净亏损" if real_roe<0 else "盈利", delta_color="inverse")
                st.caption(f"{roe_status}")

            with c3:
                st.metric(f"🔵 10年长周期风险", f"{long_risk:.1f} %", 
                          delta="大底机会" if long_risk<=25 else ("高估泡沫" if long_risk>=85 else "中性"),
                          delta_color="inverse" if long_risk>=85 else "normal")
                st.caption(f"10年绝对底: {pb_floor:.2f} ~ 顶: {pb_cap:.2f}")

            with c4:
                st.metric("🟠 短周期风险读数 (1年动能)", f"{short_risk:.1f} %", 
                          delta="短线超卖" if short_risk<=20 else ("短线超买" if short_risk>=85 else "平稳"),
                          delta_color="inverse" if short_risk>=85 else "normal")
                st.caption("基于近250日价格通道情绪")

            # 共振状态提示条
            if long_risk >= 85 and short_risk >= 85:
                st.error("🚨 **长短周期双共振触顶**：长线估值极高 且 短线情绪极度超买，见顶概率极高！")
            elif long_risk <= 25 and short_risk <= 25:
                st.success("🎯 **长短周期双共振触底**：长线极度低估 且 短线充分出清，黄金大底确立！")
            elif long_risk <= 35 and short_risk >= 70:
                st.info("💡 **长低短高（底部启动）**：长周期仍在安全低谷，短周期快速反弹，属于典型的‘涨得慢但很稳’启动期。")
            elif long_risk >= 70 and short_risk <= 35:
                st.warning("⚠️ **长高短低（高位假摔）**：长线估值仍在高位，短线下跌只是震荡，切勿误当成大底抄底！")

            st.markdown(f"""
            > **🎯 战术应对指南**：  
            > **{guidance}**  
            > *盈利状态解析*：{roe_desc}
            """)

            # ================= 图表部分：彻底分离两大视角 =================
            st.markdown("### 📊 长短周期风险走势独立图表")
            
            tab_long, tab_short = st.tabs(["🔵 10年长周期估值风险 (宏观中枢)", "🟠 近2年短周期动能风险 (战术波段)"])

            with tab_long:
                st.caption(f"10年动态估值中枢（底部PB {pb_floor:.2f} ~ 顶部PB {pb_cap:.2f}）。基于 10 年大视野，横盘期真实贴地，唯有真正牛市暴涨才会触顶。")
                fig_long = go.Figure()
                fig_long.add_trace(go.Scatter(x=df.index, y=df['long_risk'], name="10年长周期风险", line=dict(color="#1f77b4", width=2.5), fill='tozeroy', fillcolor='rgba(31, 119, 180, 0.08)'))
                fig_long.add_hline(y=85, line_dash="dash", line_color="red", annotation_text="长线高估警戒 (85%)")
                fig_long.add_hline(y=25, line_dash="dash", line_color="green", annotation_text="长线大底低估 (25%)")
                fig_long.update_layout(height=380, margin=dict(l=20, r=20, t=30, b=20), xaxis_title="真实交易日期 (近10年)", yaxis_title="长周期读数 (%)", yaxis=dict(range=[0, 105]), hovermode="x unified")
                st.plotly_chart(fig_long, use_container_width=True)

            with tab_short:
                st.caption("短周期动能风险：仅精准截取近 2 年（730天）二级市场交易水温，聚焦当下战术波段买卖点。")
                
                # 核心改变：严格只截取最近 2 年数据
                two_years_cutoff = df.index[-1] - timedelta(days=730)
                df_short = df[df.index >= two_years_cutoff]
                
                fig_short = go.Figure()
                fig_short.add_trace(go.Scatter(x=df_short.index, y=df_short['short_risk'], name="近2年短周期动能", line=dict(color="#ff7f0e", width=2), fill='tozeroy', fillcolor='rgba(255, 127, 14, 0.08)'))
                fig_short.add_hline(y=90, line_dash="dash", line_color="red", annotation_text="短线极度超买 (90%)")
                fig_short.add_hline(y=20, line_dash="dash", line_color="green", annotation_text="短线极度超卖 (20%)")
                fig_short.update_layout(height=380, margin=dict(l=20, r=20, t=30, b=20), xaxis_title="交易日期 (近2年高清视角)", yaxis_title="短周期读数 (%)", yaxis=dict(range=[0, 105]), hovermode="x unified")
                st.plotly_chart(fig_short, use_container_width=True)

            # ================= 图表二：10年 PB 估值通道 =================
            st.markdown("### 📈 图表二：10年 PB 绝对估值通道走势")
            fig_pb = go.Figure()
            fig_pb.add_trace(go.Scatter(x=df.index, y=df['pb'], name="PB 走势", line=dict(color="#2ca02c", width=2)))
            pb_bot = df['pb'].quantile(0.15)
            pb_tp = df['pb'].quantile(0.90)
            fig_pb.add_hline(y=pb_bot, line_dash="dash", line_color="green", annotation_text="10年大底线 (15%分位)")
            fig_pb.add_hline(y=pb_tp, line_dash="dash", line_color="red", annotation_text="10年高位线 (90%分位)")
            fig_pb.update_layout(height=360, margin=dict(l=20, r=20, t=30, b=20), xaxis_title="真实交易日期 (近10年)", yaxis_title="PB (市净率)", hovermode="x unified")
            st.plotly_chart(fig_pb, use_container_width=True)

            # ================= 模块：作者同款 Tushare 极值对账 =================
            st.markdown("### 📋 周期极值回溯对账（复现作者复盘方法）")
            lowest_row = df.loc[df['收盘'].idxmin()]
            lowest_date = df['收盘'].idxmin().strftime('%Y-%m-%d')
            lowest_price = lowest_row['最低']
            
            df_since_bottom = df.loc[df.index >= df['收盘'].idxmin()]
            max_price_since = df_since_bottom['最高'].max()
            max_date_since = df_since_bottom['最高'].idxmax().strftime('%Y-%m-%d')
            max_gain = ((max_price_since - lowest_price) / lowest_price) * 100.0
            curr_gain = ((curr_price - lowest_price) / lowest_price) * 100.0

            col_a, col_b, col_c, col_d = st.columns(4)
            col_a.metric("10年周期大底基准日", lowest_date)
            col_a.caption(f"最低价: ¥{lowest_price:.2f}")

            col_b.metric("大底之后最高极值", f"¥{max_price_since:.2f}")
            col_b.caption(f"极值日: {max_date_since}")

            col_c.metric("周期最大涨幅极值", f"+{max_gain:.1f} %", delta="已兑现赔率")
            col_c.caption("最低点至最高极值")

            col_d.metric("距大底当前累计涨跌", f"{'+' if curr_gain>=0 else ''}{curr_gain:.1f} %")
            col_d.caption(f"当前收盘: ¥{curr_price:.2f}")

        except Exception as e:
            st.error(f"数据解析异常: {e}")
