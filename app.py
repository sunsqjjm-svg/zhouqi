import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime

# 页面基础配置
st.set_page_config(page_title="强周期股票双信号拐点诊断仪", layout="wide", page_icon="🎯")

st.title("🎯 强周期股票：PB + ROE 双信号拐点诊断仪")
st.caption("基于雪球「律动周期研究所」逻辑：股价底领先业绩底｜ROE下行末端 + PB底部 = 价格底｜防范估值未跌透的接飞刀陷阱")

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
        "中钢国际": "sz000928", "天康生物": "sz002100", "蓝思科技": "sz300433", 
        "京东方A": "sz000725", "TCL科技": "sz000100", "韦尔股份": "sh603501", "牧原股份": "sz002714"
    }
    for k, v in preset_map.items():
        if k in keyword:
            return {"code": v[2:], "name": k, "secid": v}
    return None

# 3. 实时行情与历史连续数据
@st.cache_data(ttl=1800)
def fetch_stock_data(secid, years=4):
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.qq.com"}
    
    # 腾讯实时行情
    r_q = requests.get(f"https://qt.gtimg.cn/q={secid}", headers=headers, timeout=6)
    r_q.encoding = "gbk"
    parts = r_q.text.split('="')[1].split('~')
    
    stock_name = parts[1]
    curr_price = float(parts[3])
    curr_turnover = safe_float(parts[38]) # 换手率
    curr_pe_ttm = safe_float(parts[39])   # TTM市盈率（亏损时为负数）
    curr_pe_dyn = safe_float(parts[52]) if len(parts) > 52 else 0.0 # 动态市盈率
    curr_pb = safe_float(parts[46], default=1.0) # 市净率PB

    # 历史日K线 (新浪财经)
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

    # 构造历史连续 PB 通道
    bps = curr_price / curr_pb if curr_pb > 0 else 1.0
    df['pb'] = (df['收盘'] / bps).round(2)

    # 核心计算：长短周期连续风险读数曲线
    df['pb_pct'] = df['pb'].expanding(min_periods=30).rank(pct=True) * 100.0
    roll_high = df['收盘'].rolling(250, min_periods=30).max()
    roll_low = df['收盘'].rolling(250, min_periods=30).min()
    df['price_pos'] = ((df['收盘'] - roll_low) / (roll_high - roll_low).replace(0, 1) * 100.0).clip(0, 100)
    df['risk_score'] = (0.7 * df['pb_pct'] + 0.3 * df['price_pos']).clip(0, 100).round(1)

    meta = {
        "stock_name": stock_name,
        "curr_price": curr_price,
        "curr_pb": curr_pb,
        "curr_turnover": curr_turnover,
        "pe_ttm": curr_pe_ttm,
        "pe_dyn": curr_pe_dyn
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
    if c5.button("牧原股份"): preset = "牧原股份"
    if c6.button("紫金矿业"): preset = "紫金矿业"

    user_input = st.text_input("输入股票名称或6位代码", value=preset if preset else "天康生物")
    years_back = st.slider("估值回溯跨度（年）", min_value=3, max_value=5, value=4)

# --- 主逻辑计算 ---
if user_input:
    with st.spinner(f"正在全网拉取「{user_input}」并构建周期模型..."):
        info = search_stock(user_input)

    if not info:
        st.error(f"❌ 未识别标的「{user_input}」，请输入规范名称或代码。")
    else:
        try:
            df, meta = fetch_stock_data(info['secid'], years=years_back)
            
            curr_price = meta["curr_price"]
            curr_pb = meta["curr_pb"]
            curr_turnover = meta["curr_turnover"]
            pe_ttm = meta["pe_ttm"]
            pe_dyn = meta["pe_dyn"]
            name = meta["stock_name"]
            
            st.success(f"🎯 成功识别标的：**{name}** (代码: `{info['code']}`)")

            # 1. PB 读数
            pb_percentile = df['pb_pct'].iloc[-1]
            risk_score = df['risk_score'].iloc[-1]
            is_pb_bottom = pb_percentile <= 25.0

            # 2. 彻底修复：真实 ROE 测算（支持负数/亏损状态）
            if pe_ttm != 0:
                real_roe = (curr_pb / pe_ttm) * 100.0 # 允许计算出真实负数 ROE
            else:
                real_roe = 0.0
            
            # 判断 ROE 状态
            if real_roe <= 0:
                roe_status = "📉 深幅亏损 / 行业至暗失血期"
                is_roe_declining = True
                is_roe_rebounding = False
                roe_desc = f"公司处于净亏损状态（ROE为 {real_roe:.2f}%），行业正在经历惨烈的产能去化‘毒打’。"
            elif real_roe < 5.0:
                roe_status = "📉 处于下行末端 / 低谷冰点期"
                is_roe_declining = True
                is_roe_rebounding = False
                roe_desc = f"ROE 处于微利冰点（{real_roe:.2f}%），盈利恶化趋缓，处于出清磨底期。"
            elif pe_dyn > 0 and pe_dyn < pe_ttm * 0.90:
                roe_status = "📈 见底反弹中 / 业绩转好"
                is_roe_declining = False
                is_roe_rebounding = True
                roe_desc = "最新季度业绩出现爆发反转，ROE 步入向上通道，走势进入半山腰。"
            else:
                roe_status = "⚖️ 处于中性常态阶段"
                is_roe_declining = False
                is_roe_rebounding = False
                roe_desc = "盈利水平处于常态化波动区间。"

            # 3. 严格遵循原帖逻辑的周期位置核心裁决（消灭漏洞）
            # 规则 A：长短周期共振大顶 (风险>=90%)
            if risk_score >= 90:
                if curr_turnover >= 6.0:
                    stage = "🔴 周期大顶 / 高危预警 (双条件触发)"
                    guidance = "长短周期指标共振见顶！【执行策略】盘中至少减仓 1/3 锁定利润，严防 40%+ 级回撤。"
                else:
                    stage = "🟠 周期极值高位区 (估值过热)"
                    guidance = "估值泡沫化（风险读数>90%），赔率已极低，静待成对龙头见顶信号，严禁追高。"

            # 规则 B：双信号同时满足 = 真正价格底！
            elif is_pb_bottom and is_roe_declining:
                stage = "🟢 周期大底：双信号同时满足！(价格底成立)"
                guidance = "【黄金买点】ROE 下滑受‘毒打’+ PB 跌入历史绝对大底！完全符合原帖‘股价底领先业绩底’规律，策略：‘不着急，慢慢买’，左侧买入博弈 3~4 倍高赔率！"

            # 规则 C：【新增关键防坑】业绩大亏但估值偏高 = 价值陷阱！
            elif is_roe_declining and not is_pb_bottom:
                stage = "⚠️ 周期下行出清期：估值未到底 (防接飞刀)"
                guidance = "【切忌盲目抄底】业绩虽然处于亏损/下滑的‘毒打’期，但 PB 分位（{pb_percentile:.1f}%）仍高，尚未跌入 ≤25% 的绝对历史大底！不满足双信号叠加，严防中途接飞刀！"

            # 规则 D：ROE 已经反弹 = 半山腰
            elif is_roe_rebounding and (20 < pb_percentile <= 60):
                stage = "🟡 周期启动中段：已到半山腰"
                guidance = "【坚定持股】正如作者所言：‘等你看到业绩转好、ROE上行时，股价往往已经到半山腰了’。走势‘涨得慢但很稳’，切忌被震荡洗出局。"

            # 规则 E：真正的景气扩张
            elif pb_percentile > 50 and is_roe_rebounding:
                stage = "🔵 景气上升扩张期 (让利润奔跑)"
                guidance = "盈利与估值双击向上，安心持股，密切监控 90% 风险红线与换手率异常。"

            else:
                stage = "⚖️ 周期中轴震荡期 (耐心观望)"
                guidance = "处于多空平衡的过渡期，等待更明确的极值信号出现。"

            # ================= 视图展示 =================
            st.subheader(f"📌 周期裁决：{stage}")

            st.markdown("#### ⚡ 顶底核心双信号对撞校验器")
            s1, s2, s3 = st.columns(3)
            
            with s1:
                st.metric("信号 1：PB 估值位置", f"{curr_pb:.2f} (分位: {pb_percentile:.1f}%)")
                if is_pb_bottom:
                    st.success("✅ 满足：PB 跌入历史大底区 (≤25%)")
                elif pb_percentile >= 80:
                    st.error("🚨 警告：处于历史泡沫顶部区 (≥80%)")
                else:
                    st.info(f"ℹ️ 中性：分位 {pb_percentile:.1f}% (未跌透)")

            with s2:
                # 真实显示正负 ROE
                st.metric("信号 2：真实 ROE 状态", f"{real_roe:.2f} %", delta="净亏损" if real_roe<0 else "盈利", delta_color="inverse")
                if is_roe_declining:
                    st.success(f"✅ 状态：{roe_status}")
                elif is_roe_rebounding:
                    st.warning(f"⚠️ 状态：{roe_status}")
                else:
                    st.info(f"ℹ️ 状态：{roe_status}")
                
                if pe_ttm < 0:
                    st.caption(f"PE(TTM): {pe_ttm:.1f} (亏损) | PB: {curr_pb:.2f}")
                elif pe_dyn > 0:
                    st.caption(f"PE(动): {pe_dyn:.1f} | PE(TTM): {pe_ttm:.1f}")
                else:
                    st.caption(f"PE(TTM): {pe_ttm:.1f} | PB: {curr_pb:.2f}")

            with s3:
                st.metric("长短周期综合风险读数", f"{risk_score:.1f} %", delta="91% 极值红线", delta_color="inverse")
                if risk_score >= 90:
                    st.error("🚨 触碰 91% 周期高位危险红线！")
                elif risk_score <= 25:
                    st.success("🛡️ 处于周期绝对安全低估区")
                else:
                    st.info("⚖️ 处于周期中轴过渡段")

            st.markdown(f"""
            > **🎯 战术应对指南**：  
            > **{guidance}**  
            > *盈利趋势剖析*：{roe_desc}
            """)

            # ================= 图表一：近几年长短周期综合风险读数曲线 =================
            st.markdown("### 📊 图表一：近几年长短周期综合风险读数连续曲线（核心仪表盘）")
            fig_risk = go.Figure()
            fig_risk.add_trace(go.Scatter(
                x=df.index, y=df['risk_score'], name="综合风险读数",
                line=dict(color="#636EFA", width=2.2),
                fill='tozeroy', fillcolor='rgba(99, 110, 250, 0.08)'
            ))
            fig_risk.add_hline(y=91, line_dash="dash", line_color="red", line_width=2,
                               annotation_text="91% 极值风险预警线 (减仓1/3)", annotation_position="top right")
            fig_risk.add_hline(y=20, line_dash="dash", line_color="green", line_width=2,
                               annotation_text="20% 黄金大底机会线 (慢慢买)", annotation_position="bottom right")
            fig_risk.add_hrect(y0=90, y1=100, fillcolor="rgba(255, 0, 0, 0.08)", line_width=0)
            fig_risk.add_hrect(y0=0, y1=20, fillcolor="rgba(0, 255, 0, 0.08)", line_width=0)

            fig_risk.update_layout(height=380, margin=dict(l=20, r=20, t=30, b=20),
                                   xaxis_title="日期", yaxis_title="风险读数 (%)", yaxis=dict(range=[0, 105]),
                                   hovermode="x unified")
            st.plotly_chart(fig_risk, use_container_width=True)

            # ================= 图表二：PB 估值走势通道 =================
            st.markdown("### 📈 图表二：PB 估值走势通道与周期预警红绿线")
            fig1 = go.Figure()
            fig1.add_trace(go.Scatter(x=df.index, y=df['pb'], name="PB 走势", line=dict(color="#1f77b4", width=2)))
            pb_bot = df['pb'].quantile(0.15)
            pb_tp = df['pb'].quantile(0.90)
            fig1.add_hline(y=pb_bot, line_dash="dash", line_color="green", annotation_text="PB 大底线 (15%分位)")
            fig1.add_hline(y=pb_tp, line_dash="dash", line_color="red", annotation_text="高位警戒线 (90%分位)")
            fig1.update_layout(height=380, margin=dict(l=20, r=20, t=30, b=20),
                               xaxis_title="日期", yaxis_title="PB (市净率)", hovermode="x unified")
            st.plotly_chart(fig1, use_container_width=True)

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
            col_a.metric("本轮周期大底基准日", lowest_date)
            col_a.caption(f"最低价: ¥{lowest_price:.2f}")

            col_b.metric("大底之后最高极值", f"¥{max_price_since:.2f}")
            col_b.caption(f"极值日: {max_date_since}")

            col_c.metric("周期最大涨幅极值", f"+{max_gain:.1f} %", delta="已兑现赔率")
            col_c.caption("最低点至最高极值")

            col_d.metric("距大底当前累计涨跌", f"{'+' if curr_gain>=0 else ''}{curr_gain:.1f} %")
            col_d.caption(f"当前收盘: ¥{curr_price:.2f}")

        except Exception as e:
            st.error(f"数据解析异常: {e}")
