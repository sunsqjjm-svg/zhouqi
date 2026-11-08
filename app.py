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
st.caption("基于雪球「律动周期研究所」逻辑：股价底领先业绩底｜ROE下行末端 + PB底部 = 价格底｜全池26只极值动能自动雷达扫描｜集成PB-ROE盈利调节市净率")

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

# ================= 极速雷达扫描器：高并发稳健抓取 =================
def scan_single_stock_task(name, secid):
    """抓取单只股票近1年收盘极值，计算短周期动能"""
    code = secid[2:]
    suf = "SS" if (code.startswith('6') or code.startswith('9')) else "SZ"
    
    # 优先源：雅虎 v8 图表轻量接口 (0.1秒)
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

    # 备选源：腾讯前复权 K 线
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

# 3. 10 年大周期数据抓取与动态 BPS 估值引擎
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

    # 通道 2：雅虎 v8 直连备选
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

    # 通道 3：腾讯 1000 日备选
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

    # 动态 BPS 复合增长模型
    latest_bps = curr_price / curr_pb if curr_pb > 0 else 1.0
    latest_date = df.index[-1]
    
    delta_years = (latest_date - df.index).days / 365.25
    dynamic_bps = latest_bps / ((1.055) ** delta_years)
    df['pb'] = (df['收盘'] / dynamic_bps).round(2)

    # 20日平滑
    pb_smoothed = df['pb'].rolling(window=20, min_periods=1).mean()
    
    # 统计周期极值
    p_floor = float(df['pb'].quantile(0.02))
    p_cap = max(float(df['pb'].quantile(0.98)), 1.90)

    denom = p_cap - p_floor if p_cap > p_floor else 1.0
    raw_long_risk = ((pb_smoothed - p_floor) / denom) * 100.0
    df['long_risk'] = raw_long_risk.clip(0, 100).round(1)

    # 短周期 1 年动能通道 (近 250 日)
    roll_high = df['收盘'].rolling(250, min_periods=30).max()
    roll_low = df['收盘'].rolling(250, min_periods=30).min()
    df['short_risk'] = (((df['收盘'] - roll_low) / (roll_high - roll_low).replace(0, 1)) * 100.0).clip(0, 100).round(1)

    # 均线系统 (用于右侧启动雷达)
    df['ma20'] = df['收盘'].rolling(20, min_periods=5).mean()
    df['ma60'] = df['收盘'].rolling(60, min_periods=10).mean()

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

# ================= 页面顶部：全盘自动雷达扫描看板 (精简去重版) =================
with st.spinner("⚡ 正在执行 26 只周期股批量打包雷达扫描..."):
    pool_data = run_pool_radar()

# 筛选条件：≤ 1.0% 与 ≥ 90.0%
ice_stocks = [x for x in pool_data if x["short_risk"] <= 1.0]
fire_stocks = [x for x in pool_data if x["short_risk"] >= 90.0]

with st.expander(f"🔔 【全池实时雷达】26只周期股极端异动预警 (已扫描 {len(pool_data)}/26 只标的)", expanded=True):
    col_k1, col_k2 = st.columns(2)
    
    with col_k1:
        st.markdown("##### 🟢 冰点潜伏梯队 (动能极低/地板区)")
        if ice_stocks:
            for item in ice_stocks:
                st.success(f"🎯 **{item['name']}** (`{item['code']}`)：现价 **¥{item['price']:.2f}** ｜ 动能 **`{item['short_risk']:.1f}%`** (年内底 ¥{item['l250']:.2f})")
        else:
            st.info("提示：当前暂无标的绝对踩在 ≤1.0% 地线上。")
            
        if pool_data:
            st.caption("🔍 **当前最靠近地板的 3 只标的明细：**")
            for rank_i, it in enumerate(pool_data[:3], 1):
                flag = " 🎯 触发极寒！" if it["short_risk"] <= 1.0 else ""
                st.markdown(f"{rank_i}. **{it['name']}** (`{it['code']}`): 动能 **`{it['short_risk']:.1f}%`** (现价 ¥{it['price']:.2f}){flag}")

    with col_k2:
        st.markdown("##### 🔴 过热警戒梯队 (动能极高/天花板)")
        if fire_stocks:
            for item in fire_stocks:
                st.error(f"🚨 **{item['name']}** (`{item['code']}`)：现价 **¥{item['price']:.2f}** ｜ 动能 **`{item['short_risk']:.1f}%`** (年内顶 ¥{item['h250']:.2f})")
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

    selected_target = "星湖科技"
    if dropdown_pick != "-- 点击下拉选择 --":
        selected_target = dropdown_pick
    elif preset_btn:
        selected_target = preset_btn

    user_input = st.text_input("或直接输入股票名称/代码", value=selected_target)
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

            # ================= 核心：【盈利调节市净率 (PB-ROE 模型)】 =================
            # 1. 稳态周期平准化 ROE：若当前因出清巨亏，取行业基准 8% 测算修复空间
            norm_roe = max(real
