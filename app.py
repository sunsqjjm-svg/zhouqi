import streamlit as st
import pandas as pd
import numpy as np
import requests
import plotly.graph_objects as go
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# 页面基础配置
st.set_page_config(page_title="强周期股票双信号拐点诊断仪", layout="wide", page_icon="🎯")

st.title("🎯 强周期股票：长短周期独立诊断仪")
st.caption("基于雪球「律动周期研究所」逻辑：股价底领先业绩底｜ROE下行末端 + PB底部 = 价格底｜动态对数波动包络模型")

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

# 2. 26 只自选强周期标的专属映射库
PRESET_STOCKS = {
    "恒邦股份": "sz002237", "星湖科技": "sh600866", "北元化工": "sh601568",
    "岳阳林纸": "sh600963", "中盐化工": "sh600328", "中牧股份": "sh600195",
    "六九一二": "sz301592", "岳阳兴长": "sz000819", "新希望": "sz000876",
    "安迪苏": "sh600299", "天康生物": "sz002100", "中国中冶": "sh601618",
    "中钢国际": "sz000928", "雪峰科技": "sh603227", "双环科技": "sz000707",
    "中粮科工": "sz301058", "中密控股": "sz300470", "齐翔腾达": "sz002408",
    "凯龙股份": "sz002783", "辉隆股份": "sz002556", "中农立华": "sh603970",
    "国泰集团": "sh603977", "四川美丰": "sz000731", "振华新材": "sh688707",
    "钢研高纳": "sz300034", "海南橡胶": "sh601118"
}

# ================= 极速雷达扫描器 =================
def scan_single_stock_task(name, secid):
    code = secid[2:]
    suf = "SS" if (code.startswith('6') or code.startswith('9')) else "SZ"
    try:
        yf_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.{suf}?range=1y&interval=1d"
        res = requests.get(yf_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3).json()
        chart_data = res.get("chart", {}).get("result", [])[0]
        closes = chart_data.get("indicators", {}).get("quote", [{}])[0].get("close", [])
        valid_closes = [float(x) for x in closes if x is not None]
        if len(valid_closes) >= 30:
            c_now = valid_closes[-1]
            h_250 = max(valid_closes)
            l_250 = min(valid_closes)
            denom = h_250 - l_250 if h_250 > l_250 else 1.0
            short_risk = round(((c_now - l_250) / denom) * 100.0, 1)
            return {"name": name, "code": code, "price": c_now, "short_risk": short_risk, "h250": h_250, "l250": l_250}
    except Exception:
        pass

    try:
        tx_url = f"https://web.ifzq.gtimg.cn/appstock/news/fqkline/get?param={secid},day,,,300,qfq"
        tx_r = requests.get(tx_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3).json()
        kl = tx_r.get("data", {}).get(secid, {}).get("qfqday", [])
        if len(kl) >= 30:
            cs = [float(x[2]) for x in kl][-250:]
            c_now = cs[-1]
            h_250 = max(cs)
            l_250 = min(cs)
            denom = h_250 - l_250 if h_250 > l_250 else 1.0
            short_risk = round(((c_now - l_250) / denom) * 100.0, 1)
            return {"name": name, "code": code, "price": c_now, "short_risk": short_risk, "h250": h_250, "l250": l_250}
    except Exception:
        pass

    return None

@st.cache_data(ttl=180)
def run_pool_radar():
    results = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(scan_single_stock_task, name, secid) for name, secid in PRESET_STOCKS.items()]
        for future in as_completed(futures):
            res = future.result()
            if res:
                results.append(res)
    results.sort(key=lambda x: x["short_risk"])
    return results

@st.cache_data(ttl=86400)
def search_stock(keyword):
    keyword = keyword.strip()
    if keyword in PRESET_STOCKS:
        code = PRESET_STOCKS[keyword][2:]
        return {"code": code, "name": keyword, "secid": PRESET_STOCKS[keyword]}

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

    for k, v in PRESET_STOCKS.items():
        if k in keyword:
            return {"code": v[2:], "name": k, "secid": v}
    return None

# 3. 终极算法还原：动态对数布林包络模型 (解决两年粘顶与死贴地板)
@st.cache_data(ttl=300)
def fetch_stock_data(secid, code, years=10):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://finance.qq.com"}
    
    # 实时行情
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

    # 东财 10 年高精度 K 线
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
        if len(klines) >= 1000:
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

    # 雅虎直连备选
    if len(records) < 1000:
        try:
            records = []
            yf_suffix = "SS" if (code.startswith('6') or code.startswith('9')) else "SZ"
            yf_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.{yf_suffix}?range=10y&interval=1d"
            res_yf = requests.get(yf_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8).json()
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

    # 腾讯 1000 日备选
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
        raise ValueError(f"未能获取到 {stock_name} 的行情数据，请刷新重试")

    df = pd.DataFrame(records).set_index("日期").sort_index()
    df = df[~df.index.duplicated(keep='first')]

    # 截断未来脏时间
    df = df[df.index <= datetime.now()]

    # 真实市净率估算
    bps = curr_price / curr_pb if curr_pb > 0 else 1.0
    df['pb'] = (df['收盘'] / bps).round(2)

    # ================= 核心突破：动态对数布林通道 %B 滤波 =================
    # 1. 对数收盘价
    df['log_close'] = np.log(df['收盘'])

    # 2. 采用精准对应作者呼吸节奏的 220 交易日 (约 11 个月) 动态中枢与标准差
    log_ma = df['log_close'].rolling(window=220, min_periods=30).mean()
    log_std = df['log_close'].rolling(window=220, min_periods=30).std()

    # 3. 动态波动通道 (1.85 个标准差)
    k_band = 1.85
    upper_band = log_ma + k_band * log_std
    lower_band = log_ma - k_band * log_std
    band_width = upper_band - lower_band
    band_width = band_width.replace(0, 1)

    # 4. %B 相对位置归一化：彻底消灭两年粘顶和平直死底！
    raw_b = (df['log_close'] - lower_band) / band_width
    
    # 5. 8 日微幅去噪平滑，保留锋利尖角
    df['long_risk'] = raw_b.rolling(window=8, min_periods=1).mean().clip(0.0, 1.0).round(2)

    # 短周期 1 年动能通道 (近 250 日)
    roll_high = df['收盘'].rolling(250, min_periods=30).max()
    roll_low = df['收盘'].rolling(250, min_periods=30).min()
    df['short_risk'] = (((df['收盘'] - roll_low) / (roll_high - roll_low).replace(0, 1)) * 100.0).clip(0, 100).round(1)

    # 均线系统
    df['ma20'] = df['收盘'].rolling(20, min_periods=5).mean()
    df['ma60'] = df['收盘'].rolling(60, min_periods=10).mean()

    # 综合读数
    df['risk_score'] = (0.7 * (df['long_risk'] * 100.0) + 0.3 * df['short_risk']).clip(0, 100).round(1)
    actual_years = round(len(df) / 244, 1)

    meta = {
        "stock_name": stock_name,
        "curr_price": curr_price,
        "curr_pb": curr_pb,
        "curr_turnover": curr_turnover,
        "pe_ttm": curr_pe_ttm,
        "pe_dyn": curr_pe_dyn,
        "actual_years": actual_years
    }
    return df, meta

# ================= 页面顶部：精简去重后的全池实时雷达看板 =================
with st.spinner("⚡ 正在全盘扫描 26 只周期股实时极值动能..."):
    pool_data = run_pool_radar()

ice_strictly = [x for x in pool_data if x["short_risk"] <= 1.0]
fire_strictly = [x for x in pool_data if x["short_risk"] >= 90.0]

with st.expander(f"🔔 【全池雷达】26只周期股动能监控台 (成功拉取 {len(pool_data)}/26 只标的)", expanded=True):
    col_k1, col_k2 = st.columns(2)
    
    with col_k1:
        st.markdown("##### 🟢 冰点潜伏梯队 (动能极低/地板区)")
        if ice_strictly:
            for item in ice_strictly:
                st.success(f"🎯 **{item['name']}** (`{item['code']}`)：动能 **`{item['short_risk']:.1f}%`** (现价 ¥{item['price']:.2f}，踩在年内最低点 ¥{item['l250']:.2f})")
        else:
            st.info("提示：当前暂无标的绝对踩在 ≤1.0% 地线上。")
            
        if pool_data:
            st.caption("🔍 **当前最靠近地板的 3 只标的明细：**")
            for rank_i, it in enumerate(pool_data[:3], 1):
                flag = " 🎯 触发极寒！" if it["short_risk"] <= 1.0 else ""
                st.markdown(f"{rank_i}. **{it['name']}** (`{it['code']}`): 动能 **`{it['short_risk']:.1f}%`** (现价 ¥{it['price']:.2f}){flag}")

    with col_k2:
        st.markdown("##### 🔴 过热警戒梯队 (动能极高/天花板)")
        if fire_strictly:
            for item in fire_strictly:
                st.error(f"🚨 **{item['name']}** (`{item['code']}`)：动能 **`{item['short_risk']:.1f}%`** (现价 ¥{item['price']:.2f}，摸到年内最高点 ¥{item['h250']:.2f})")
        else:
            st.info("提示：当前暂无标的绝对贴在 ≥90.0% 天花板上。")

        if pool_data:
            st.caption("🔍 **当前最靠近天花板的 3 只标的明细：**")
            for rank_i, it in enumerate(pool_data[-3:][::-1], 1):
                flag = " 🚨 触发过热！" if it["short_risk"] >= 90.0 else ""
                st.markdown(f"{rank_i}. **{it['name']}** (`{it['code']}`): 动能 **`{it['short_risk']:.1f}%`** (现价 ¥{it['price']:.2f}){flag}")

    if st.button("🔄 立即强制刷新全池数据缓存"):
        st.cache_data.clear()
        st.rerun()

# --- 侧边栏交互 ---
with st.sidebar:
    st.header("⚙️ 标的诊断设置")
    
    stock_names = list(PRESET_STOCKS.keys())
    dropdown_pick = st.selectbox("📌 26只经典周期股快捷直选：", ["-- 点击下拉选择 --"] + stock_names)

    preset_btn = None
    with st.expander("📂 展开 26 只周期股按钮网格", expanded=False):
        col1, col2 = st.columns(2)
        for idx, sname in enumerate(stock_names):
            target_col = col1 if idx % 2 == 0 else col2
            if target_col.button(sname, key=f"btn_{idx}", use_container_width=True):
                preset_btn = sname

    selected_target = "钢研高纳"
    if dropdown_pick != "-- 点击下拉选择 --":
        selected_target = dropdown_pick
    elif preset_btn:
        selected_target = preset_btn

    user_input = st.text_input("或直接输入股票名称/代码", value=selected_target)
    years_back = st.slider("大周期回溯跨度（年）", min_value=5, max_value=12, value=10, step=1, help="强周期行业设备与产能周期（朱格拉周期）通常历时7-10年，推荐10年完整视角。")

# --- 主逻辑计算 ---
if user_input:
    with st.spinner(f"正在全网调取「{user_input}」并运行动态对数包络模型..."):
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
            actual_span = meta.get("actual_years", 10.0)

            st.success(f"🎯 成功识别标的：**{name}** (代码: `{info['code']}`，已载入近 **{actual_span} 年** 完整大周期数据)")

            # 最新指标 (0.0~1.0 标度)
            long_risk = df['long_risk'].iloc[-1]
            short_risk = df['short_risk'].iloc[-1]
            risk_score = df['risk_score'].iloc[-1]
            is_pb_bottom = long_risk <= 0.20

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

            # 【🚀 底部启动右侧雷达】
            curr_close = df['收盘'].iloc[-1]
            ma20_curr = df['ma20'].iloc[-1]
            ma60_curr = df['ma60'].iloc[-1]
            
            c_trend = (curr_close >= ma60_curr) and (ma20_curr >= ma60_curr)
            low_25d = df['最低'].tail(25).min()
            low_120d = df['最低'].tail(120).min()
            c_wbottom = low_25d > (low_120d * 1.04)
            c_momentum = short_risk >= 48.0
            c_val_safe = long_risk <= 0.45

            launch_checks = [c_trend, c_wbottom, c_momentum, c_val_safe]
            launch_score = sum(launch_checks)

            if launch_score >= 3 and c_val_safe:
                launch_badge = "🚀 右侧启动初段确立！(涨得慢但很稳，果断上车)"
                launch_style = "success"
                launch_action = "【买入并坚定持股】长线风险处于安全吸筹区，但右侧趋势与动能已破局！符合原帖：‘涨得慢但很稳，周期来了，还犹豫什么？’"
            elif launch_score >= 2:
                launch_badge = "⚡ 异动酝酿中（初现右侧端倪，密切盯盘）"
                launch_style = "info"
                launch_action = "【观察准备】部分启动信号已亮起，可建立底仓观察，等待突破 60 日均线与 50% 动能中轴加仓。"
            else:
                launch_badge = "💤 左侧磨底沉睡期（未见右侧信号，耐心等待）"
                launch_style = "warning"
                launch_action = "【切忌重仓盲目冲入】虽然长线估值便宜，但短线均线仍受压制、缺乏向上动能。策略：继续‘不着急，慢慢买’分批潜伏。"

            # 周期位置核心裁决 (0.0~1.0 标度完全对齐)
            if long_risk >= 0.85 and short_risk >= 85:
                stage = "🔴 周期大顶：长短周期同时触顶 (双共振清仓)"
                guidance = "长线风险达 0.85+ 极值泡沫 + 短周期情绪极限超买！触发最高级别大顶预警，坚决分批离场防 40%+ 级暴跌！"
            elif risk_score >= 90:
                stage = "🟠 周期极值高位区 (估值过热)"
                guidance = "综合风险读数突破 90%，赔率已极低，模型建议：盘中卖掉 1/3 锁定收益，留纯利润奔跑。"
            elif is_pb_bottom and is_roe_declining:
                stage = "🟢 周期大底：双信号同时满足！(价格底成立)"
                guidance = f"【黄金买点】长线风险跌入绝对大底（≤0.20）+ ROE 遭受毒打！完全符合‘股价底领先业绩底’规律，策略：‘不着急，慢慢买’！"
            elif is_roe_declining and not is_pb_bottom:
                stage = "⚠️ 周期下行出清期：估值未到底 (防接飞刀)"
                guidance = f"【防盲目抄底】业绩虽然处于亏损毒打期，但长线风险水平（{long_risk:.2f}）未跌透，绝不能盲目接飞刀！"
            elif is_roe_rebounding and (0.20 < long_risk <= 0.60):
                stage = "🟡 周期启动中段：已到半山腰"
                guidance = "【坚定持股】正如作者所言：‘等你看到业绩转好、ROE上行时，股价往往已经到半山腰了’。走势‘涨得慢但很稳’。"
            elif long_risk > 0.50 and is_roe_rebounding:
                stage = "🔵 景气上升扩张期 (让利润奔跑)"
                guidance = "盈利与估值双击向上，安心持股，密切监控长短周期向 0.85 警戒线的推移。"
            else:
                stage = "⚖️ 周期中轴过渡期 (耐心观望)"
                guidance = "处于多空平衡期，静待更极端的长短周期信号出现。"

            # ================= 视图展示 =================
            st.subheader(f"📌 周期裁决：{stage}")

            # 【🚀 底部启动右侧雷达看板】
            st.markdown("#### 🚀 底部启动右侧雷达 (解决不知道何时启动的痛点)")
            if launch_style == "success":
                st.success(f"**判定结果：{launch_badge}**\n\n{launch_action}")
            elif launch_style == "info":
                st.info(f"**判定结果：{launch_badge}**\n\n{launch_action}")
            else:
                st.warning(f"**判定结果：{launch_badge}**\n\n{launch_action}")

            r1, r2, r3, r4 = st.columns(4)
            with r1:
                st.metric("1. 均线生命线破局", "站上MA60" if c_trend else "受均线压制", 
                          delta="破局确认" if c_trend else "未突破", delta_color="normal" if c_trend else "inverse")
                st.caption(f"股价 ¥{curr_close:.2f} | 60日线 ¥{ma60_curr:.2f}")

            with r2:
                st.metric("2. 双底重心抬高", "底底抬升" if c_wbottom else "仍处前低", 
                          delta="形态确立" if c_wbottom else "未走出", delta_color="normal" if c_wbottom else "inverse")
                st.caption(f"近1月低点 ¥{low_25d:.2f} > 前期底 ¥{low_120d:.2f}")

            with r3:
                st.metric("3. 短周期动能破中轴", f"{short_risk:.1f} %", 
                          delta="动能过半" if c_momentum else "动能不足", delta_color="normal" if c_momentum else "inverse")
                st.caption("短周期读数突破 48% 压制")

            with r4:
                st.metric("4. 估值安全垫充足", f"{long_risk:.2f}", 
                          delta="安全吸筹区" if c_val_safe else "偏高", delta_color="normal" if c_val_safe else "inverse")
                st.caption("长线风险水平处于 ≤0.45 空间")

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
                st.metric(f"🟣 长线风险水平", f"{long_risk:.2f}", 
                          delta="大底机会" if long_risk<=0.20 else ("高估泡沫" if long_risk>=0.85 else "中性"),
                          delta_color="inverse" if long_risk>=0.85 else "normal")
                st.caption("动态对数波动包络")

            with c4:
                st.metric("🟠 短周期风险读数 (1年动能)", f"{short_risk:.1f} %", 
                          delta="短线超卖" if short_risk<=20 else ("短线超买" if short_risk>=85 else "平稳"),
                          delta_color="inverse" if short_risk>=85 else "normal")
                st.caption("基于近250日价格通道情绪")

            # ================= 图表部分：完全复刻作者原版 =================
            st.markdown("### 📊 长短周期独立图表")
            tab_author, tab_short = st.tabs(["🟣 长线风险水平 (作者原版 0.0~1.0 标度)", "🟠 近2年短周期动能风险 (战术波段)"])

            with tab_author:
                st.caption("动态对数波动包络滤波：以约 1 年动态中枢为基准，兼顾内生增长与周期呼吸，彻底消灭两年死板粘顶，波峰波谷极其锐利。")
                
                custom_hover = np.stack((df['收盘'], df['pb']), axis=-1)

                fig_auth = go.Figure()
                fig_auth.add_trace(go.Scatter(
                    x=df.index, y=df['long_risk'], name="长线风险水平",
                    line=dict(color="#7B1FA2", width=2.4),
                    fill='tozeroy', fillcolor='rgba(123, 31, 162, 0.08)',
                    customdata=custom_hover,
                    hovertemplate="<b>%{x|%Y-%m-%d}</b><br>长线风险水平: <b>%{y:.2f}</b><br>收盘价: <b>¥%{customdata[0]:.2f}</b><br>对应市净率 PB: %{customdata[1]:.2f}<extra></extra>"
                ))

                fig_auth.add_hline(y=0.85, line_dash="dash", line_color="red", annotation_text="0.85 极值风险预警线")
                fig_auth.add_hline(y=0.20, line_dash="dash", line_color="green", annotation_text="0.20 黄金大底机会线")
                fig_auth.add_hrect(y0=0.85, y1=1.0, fillcolor="rgba(255, 0, 0, 0.05)", line_width=0)
                fig_auth.add_hrect(y0=0.0, y1=0.20, fillcolor="rgba(0, 255, 0, 0.05)", line_width=0)

                fig_auth.update_layout(
                    height=420, margin=dict(l=20, r=20, t=30, b=20),
                    xaxis_title="真实交易日期 (近10年动态大视野)",
                    yaxis_title="长线风险水平",
                    yaxis=dict(
                        range=[-0.05, 1.05],
                        tickmode='array',
                        tickvals=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                        ticktext=['0.0', '0.2', '0.4', '0.6', '0.8', '1.0']
                    ),
                    hovermode="x unified"
                )

                st.plotly_chart(fig_auth, use_container_width=True)

            with tab_short:
                st.caption("短周期动能风险：仅精准截取近 2 年（730天）二级市场交易水温，聚焦当下战术波段买卖点。")
                two_years_cutoff = df.index[-1] - timedelta(days=730)
                df_short = df[df.index >= two_years_cutoff]
                custom_short_hover = np.stack((df_short['收盘'],), axis=-1)
                
                fig_short = go.Figure()
                fig_short.add_trace(go.Scatter(
                    x=df_short.index, y=df_short['short_risk'], 
                    name="近2年短周期动能", 
                    line=dict(color="#ff7f0e", width=2), 
                    fill='tozeroy', 
                    fillcolor='rgba(255, 127, 14, 0.08)',
                    customdata=custom_short_hover,
                    hovertemplate="<b>%{x|%Y-%m-%d}</b><br>短周期动能读数: <b>%{y:.1f}%</b><br>收盘价: ¥%{customdata[0]:.2f}<extra></extra>"
                ))
                fig_short.add_hline(y=90, line_dash="dash", line_color="red", annotation_text="短线极度超买 (90%)")
                fig_short.add_hline(y=20, line_dash="dash", line_color="green", annotation_text="短线极度超卖 (20%)")
                fig_short.update_layout(height=400, margin=dict(l=20, r=20, t=30, b=20), xaxis_title="交易日期 (近2年高清视角)", yaxis_title="短周期读数 (%)", yaxis=dict(range=[0, 105]), hovermode="x unified")
                st.plotly_chart(fig_short, use_container_width=True)

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
