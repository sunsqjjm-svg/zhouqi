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
st.caption("基于雪球「律动周期研究所」逻辑：股价底领先业绩底｜ROE下行末端 + PB底部 = 价格底｜全池26只极值动能自动雷达扫描")

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

# 3. 恢复原始纯粹的长周期估值引擎 (剔除人工 BPS 复合增长假设)
@st.cache_data(ttl=300)
def fetch_stock_data(secid, code, years=10):
    he
