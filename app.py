import streamlit as st
import pandas as pd
import numpy as np
import akshare as ak
import plotly.graph_objects as go
from datetime import datetime, timedelta

# 页面基础配置
st.set_page_config(page_title="强周期股票周期位置诊断仪", layout="wide", page_icon="📈")

st.title("📊 强周期股票周期拐点诊断仪")
st.caption("基于雪球「律动周期研究所」面板周期复盘框架：将感觉变为读数（ROE下行末端+PB底 / 91%风险+换手率预警）")

# 侧边栏：输入与参数
with st.sidebar:
    st.header("⚙️ 股票与参数设置")
    stock_code = st.text_input("输入 A 股代码（6位数字）", value="300433")
    years_back = st.slider("历史回溯周期（年）", min_value=3, max_value=10, value=5)
    st.info("提示：\n- 蓝思科技: 300433\n- 京东方A: 000725\n- 韦尔股份: 603501\nTCL科技: 000100")

# 数据抓取函数
@st.cache_data(ttl=3600)
def load_stock_data(symbol, years):
    start_date = (datetime.now() - timedelta(days=years * 365)).strftime("%Y%m%d")
    
    # 1. 获取个股历史行情与换手率
    df_hist = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start_date, adjust="qfq")
    if df_hist.empty:
        return None, None, None
    df_hist['日期'] = pd.to_datetime(df_hist['日期'])
    df_hist.set_index('日期', inplace=True)
    
    # 2. 获取个股估值指标 (PB、PE等)
    try:
        df_val = ak.stock_a_indicator_lg(symbol=symbol)
        df_val['trade_date'] = pd.to_datetime(df_val['trade_date'])
        df_val.set_index('trade_date', inplace=True)
        # 合并行情与PB
        df_combined = df_hist[['收盘', '最高', '最低', '换手率']].join(df_val[['pb']], how='inner')
    except Exception:
        # 降级备用容错
        df_combined = df_hist[['收盘', '最高', '最低', '换手率']]
        df_combined['pb'] = np.nan

    # 3. 获取财报主要指标（获取季度 ROE）
    try:
        df_fin = ak.stock_financial_abstract(symbol=symbol)
    except Exception:
        df_fin = pd.DataFrame()
        
    return df_combined, df_fin, symbol

if stock_code:
    with st.spinner(f"正在拉取 {stock_code} 的历史数据并运行周期模型..."):
        try:
            df, df_fin, sym = load_stock_data(stock_code, years_back)
        except Exception as e:
            st.error(f"数据加载失败，请检查股票代码或网络: {e}")
            df = None

    if df is not None and not df.empty and 'pb' in df.columns and not df['pb'].isna().all():
        latest = df.iloc[-1]
        curr_price = latest['收盘']
        curr_pb = latest['pb']
        curr_turnover = latest['换手率']

        # --- 核心指标计算（将感觉变成读数） ---
        # 1. PB 历史分位数 (0% ~ 100%)
        pb_percentile = (df['pb'] < curr_pb).mean() * 100

        # 2. 换手率近 1 年分位数
        one_year_df = df.last('365D')
        turnover_percentile = (one_year_df['换手率'] < curr_turnover).mean() * 100

        # 3. 计算长短周期综合风险评分 (Risk Score: 0~100)
        # 权重模型：PB历史分位占 70%，近期价格动量/布林通道位置占 30%
        rolling_high = df['收盘'].rolling(250, min_periods=60).max().iloc[-1]
        rolling_low = df['收盘'].rolling(250, min_periods=60).min().iloc[-1]
        price_pos = (curr_price - rolling_low) / (rolling_high - rolling_low) * 100 if rolling_high > rolling_low else 50
        risk_score = 0.7 * pb_percentile + 0.3 * price_pos

        # --- 状态判定逻辑（作者文章规则） ---
        status = ""
        color = ""
        action_advice = ""
        desc = ""

        # 规则 1：周期大顶 / 高危预警 (风险分位接近或超过 91%，且叠加高换手)
        if risk_score >= 90:
            if turnover_percentile >= 80 or curr_turnover >= 7.0:
                status = "🔴 周期大顶 / 高危预警 (双条件触发)"
                color = "red"
                action_advice = "短线/周期风险极高！建议至少盘中卖掉 1/3 仓位锁定利润，严防 40%+ 级回撤。"
                desc = f"风险读数达到 {risk_score:.1f}%（超 90% 警戒线），且换手率处于年内高位（{curr_turnover:.2f}%），长短周期同时共振预警。"
            else:
                status = "🟠 周期极值高位区 (估值过热)"
                color = "orange"
                action_advice = "不宜追高加仓，做好防守，等待换手率异动或成对龙头见顶信号。"
                desc = f"当前估值已进入泡沫化阶段（风险读数 {risk_score:.1f}%），赔率已极低。"

        # 规则 2：周期大底 (PB处于历史极低位)
        elif pb_percentile <= 20:
            status = "🟢 周期大底区间 (高赔率黄金区)"
            color = "green"
            action_advice = "跌进买入区间，‘不着急，慢慢买’，左侧分批布局，博弈 3~4 倍景气修复赔率。"
            desc = f"PB 处于近 {years_back} 年极低分位 ({pb_percentile:.1f}%)。若当前公司正遭遇全行业亏损或极端恐慌，股价底往往先于业绩底形成。"

        # 规则 3：启动初段 (慢而稳)
        elif 20 < pb_percentile <= 45:
            status = "🟡 周期启动初段 (价值修复)"
            color = "blue"
            action_advice = "‘涨得慢但很稳’，预期开始修复，坚定持有，不要因短线震荡被甩下车。"
            desc = f"估值分位脱离底部，进入低估恢复区（PB分位 {pb_percentile:.1f}%），业绩拐点逐步酝酿中。"

        # 规则 4：主升浪/中段
        else:
            status = "🔵 景气上升中段 (让利润奔跑)"
            color = "gray"
            action_advice = "持股待涨，密切监控换手率异动与 90% 风险红线。"
            desc = f"处于周期中轴向上扩张阶段，当前风险读数 {risk_score:.1f}%，尚未触及极值区。"

        # --- 界面展示 ---
        st.subheader(f"📌 诊断结果：{status}")
        
        # 核心指标卡片
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("当前收盘价", f"¥ {curr_price:.2f}")
        c1.caption(f"PB: {curr_pb:.2f}")
        
        c2.metric("PB 历史分位数", f"{pb_percentile:.1f} %", delta="极低估" if pb_percentile<=20 else ("极高估" if pb_percentile>=85 else "中性"))
        c2.caption(f"{years_back}年历史周期内")

        c3.metric("当日换手率", f"{curr_turnover:.2f} %", delta="换手异常" if turnover_percentile>=80 else "正常")
        c3.caption(f"换手率处于年内 {turnover_percentile:.1f}% 分位")

        c4.metric("综合风险读数 (Target: 91%)", f"{risk_score:.1f} %", delta_color="inverse", delta="触及预警" if risk_score>=90 else "安全")
        c4.caption("模型长线周期风险指标")

        # 策略提示框
        st.markdown(f"""
        > **🎯 战术应对建议**：  
        > **{action_advice}**  
        > *逻辑说明*：{desc}
        """)

        # --- 图表绘制：PB 估值分位走势与预警通道 ---
        st.markdown("### 📈 估值分位数与周期位置图")
        
        # 计算滚动的 PB 分位数曲线
        df['pb_pct'] = df['pb'].rolling(window=250*3, min_periods=100).apply(lambda x: (x < x[-1]).mean() * 100)

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df.index, y=df['pb'], name="PB 走势", line=dict(color="#1f77b4", width=1.5)))
        
        # 底部与顶部参考线
        pb_bottom_ref = df['pb'].quantile(0.15)
        pb_top_ref = df['pb'].quantile(0.90)
        
        fig.add_hline(y=pb_bottom_ref, line_dash="dash", line_color="green", annotation_text="周期底部线 (15%分位)")
        fig.add_hline(y=pb_top_ref, line_dash="dash", line_color="red", annotation_text="高危风险线 (90%分位)")
        
        fig.update_layout(height=450, margin=dict(l=20, r=20, t=30, b=20),
                          xaxis_title="日期", yaxis_title="PB (市净率)",
                          hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

    else:
        st.warning("暂未获取到该股票的有效 PB 数据或行情，请确认该股票代码为 A 股上市公司（如 000725、300433）。")
