import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np

# ================= 页面全局配置 =================
st.set_page_config(page_title="湖北光伏 D+3 现货实战沙盘", layout="wide")
st.title("⚡ 湖北光伏 D+3 时点级交易沙盘 ")
st.markdown("💡 **核心特性**：1-24时点标准 | **数据修改实时联动** | 纯净原生输入 | 动态止损线")

hours_1_to_24 = [f"{i:02d}:00" for i in range(1, 25)]

# ================= 1. 核心数据池初始化 =================
# 只在第一次打开网页时初始化，之后全交由底层的 key 自动接管记忆
if "base_df" not in st.session_state:
    st.session_state.base_df = pd.DataFrame({
        "时点": hours_1_to_24,
        "累计上网电量(MWh)": [0.0] * 24,
        "累计仓位(MWh)": [0.0] * 24,
        "偏差罚款单价(元/MWh)": [150.0] * 24 
    })

if "df_forecast" not in st.session_state:
    st.session_state.df_forecast = pd.DataFrame({
        "时点": hours_1_to_24,
        "预测上网电量(MWh)": [0,0,0,0,0,0,0,0,17,28,20,28,32,30,35,32,20,5,0,0,0,0,0,0],
        "预测实时电价(元/MWh)": [250.0, 250.0, 220.0, 220.0, 250.0, 300.0, 350.0, 450.0, 400.0, 200.0, 150.0, 100.0, 100.0, 150.0, 200.0, 300.0, 400.0, 550.0, 600.0, 500.0, 450.0, 350.0, 300.0, 250.0],
        "年度合约量(MWh)": [20.0] * 24,
        "年度合约价(元/MWh)": [330.0] * 24
    })

# ================= 侧边栏：原生布局 =================
st.sidebar.header("⚙️ 1. 各时点历史底仓与罚款设置")

# 【核心修复】：加上 key="base_editor"，并且绝不再手动覆盖 session_state！
edited_base_df = st.sidebar.data_editor(
    st.session_state.base_df, 
    key="base_editor",
    use_container_width=True, 
    hide_index=True, 
    height=600
)

st.sidebar.markdown("---")
st.sidebar.markdown("**📉 2. 容偏与超缺额考核设置**")
max_dev = st.sidebar.slider("考核惩罚红线 (%)", min_value=1.0, max_value=10.0, value=5.0, step=0.5) / 100.0
alert_dev = st.sidebar.slider("强制干预预警线 (%)", min_value=1.0, max_value=10.0, value=4.5, step=0.5) / 100.0

coef_actual = st.sidebar.number_input("累计上网电量系数", value=0.80, step=0.05)
coef_contract_short = st.sidebar.number_input("中长期净合约 缺额系数", value=0.90, step=0.05)
coef_contract_over = st.sidebar.number_input("中长期净合约 超额系数", value=1.10, step=0.05)

st.sidebar.markdown("---")
st.sidebar.markdown("**💰 3. 交易员实盘摩擦约束 (元/MWh)**")
price_buffer = st.sidebar.number_input("买入抢单缓冲差价 (元/MWh)", value=20.0, step=5.0, format="%.1f")
friction_margin = st.sidebar.number_input("套利触发最小价差死区 (元/MWh)", value=30.0, step=5.0, format="%.1f")
max_trade_vol = st.sidebar.number_input("单时点最大盘面深度(MWh)", value=38.0, step=10.0)


# ================= 主界面：日内全要素配置区 =================
st.subheader("📊 24小时日内全要素配置区 (双击修改，底部瞬间联动)")

# 【核心修复】：加上 key="forecast_editor"，不覆盖状态，让表格自动记忆你的每一次修改
edited_forecast_df = st.data_editor(
    st.session_state.df_forecast, 
    key="forecast_editor",
    use_container_width=True, 
    num_rows="fixed"
)

# ================= 核心推演算法 =================
results = []
total_buy_vol = 0
total_sell_vol = 0
max_buy_price = 0
max_risk_hour = "-"
depth_limit_hit_count = 0

# 遍历时直接使用 edited_forecast_df，这里面包含了你刚刚敲进去的所有最新数字
for i in range(24):
    q_forecast = edited_forecast_df.loc[i, "预测上网电量(MWh)"]
    p_rt = edited_forecast_df.loc[i, "预测实时电价(元/MWh)"]
    q_annual_h = edited_forecast_df.loc[i, "年度合约量(MWh)"]
    p_annual_h = edited_forecast_df.loc[i, "年度合约价(元/MWh)"]
    
    historical_actual_h = edited_base_df.loc[i, "累计上网电量(MWh)"]
    historical_contract_h = edited_base_df.loc[i, "累计仓位(MWh)"]
    p_penalty_h = edited_base_df.loc[i, "偏差罚款单价(元/MWh)"] 
    
    # 【逻辑重构核心】：干预前的基础 = 历史底仓 + 今日预测数据 (此时还没进行D+3操作)
    cum_actual_pre = historical_actual_h + q_forecast
    cum_contract_pre = historical_contract_h + q_annual_h
    
    # 基于干预前的总数据，计算此时点最真实的初始超缺额压力
    val_shortage_pre = cum_actual_pre * coef_actual - cum_contract_pre * coef_contract_short
    val_excess_pre = cum_actual_pre * coef_actual - cum_contract_pre * coef_contract_over
    
    if val_shortage_pre < 0:
        status_oe = f"缺额 {val_shortage_pre:.2f}"
        net_oe_value_pre = val_shortage_pre
    elif val_excess_pre > 0:
        status_oe = f"超额 +{val_excess_pre:.2f}"
        net_oe_value_pre = val_excess_pre
    else:
        status_oe = "安全 0.00"
        net_oe_value_pre = 0.00

    # D+3 策略判定的基础基于干预前的水位
    initial_dev_vol = cum_actual_pre - cum_contract_pre
    initial_dev_pct = initial_dev_vol / cum_contract_pre if cum_contract_pre > 0 else 0
    
    is_shortage_alert = (cum_actual_pre * coef_actual) < (cum_contract_pre * coef_contract_short) 
    is_excess_alert = (cum_actual_pre * coef_actual) > (cum_contract_pre * coef_contract_over)  
    
    if is_excess_alert:
        strategy = "🚨 触发超额红线 -> 强制拉高合约制造欠发"
        target_contract = q_forecast / (1 - max_dev)
    elif initial_dev_pct > alert_dev:
        strategy = "⚠️ 濒临超发 -> 强制拉高合约制造欠发"
        target_contract = q_forecast / (1 - max_dev)
    elif is_shortage_alert:
        strategy = "🚨 触发缺额红线 -> 强制压低合约制造超发"
        target_contract = q_forecast / (1 + max_dev)
    elif initial_dev_pct < -alert_dev:
        strategy = "⚠️ 濒临欠发 -> 强制压低合约制造超发"
        target_contract = q_forecast / (1 + max_dev)
    else:
        price_diff = p_rt - p_annual_h
        if price_diff > friction_margin:
            strategy = "✅ 价差理想(现货贵) -> 压低合约留给现货"
            target_contract = q_forecast / (1 + max_dev)
        elif price_diff < -friction_margin:
            strategy = "✅ 价差理想(现货便宜) -> 拉高合约低价买回"
            target_contract = q_forecast / (1 - max_dev)
        else:
            strategy = "⏸️ 处于套利死区 -> 锁定基本盘"
            target_contract = q_annual_h 
            
    raw_d3_volume = target_contract - q_annual_h
    d3_volume = raw_d3_volume
    buy_limit = 0.0 
    
    is_depth_limited = False
    if d3_volume > max_trade_vol:
        d3_volume = max_trade_vol
        is_depth_limited = True
        depth_limit_hit_count += 1
    elif d3_volume < -max_trade_vol:
        d3_volume = -max_trade_vol
        is_depth_limited = True
        depth_limit_hit_count += 1

    if is_depth_limited:
        strategy += f" 🛑 (受限于盘面深度，原需 {raw_d3_volume:.1f} 已截断)"

    if d3_volume > 0:
        direction = "卖出"
        d3_price = max(p_annual_h, p_rt - price_buffer)
        total_sell_vol += d3_volume
    elif d3_volume < 0:
        direction = "买入"
        buy_limit = p_rt + p_penalty_h 
        d3_price = p_rt + price_buffer 
        d3_price = min(d3_price, buy_limit)
        total_buy_vol += abs(d3_volume)
        
        if d3_price > max_buy_price:
            max_buy_price = d3_price
            max_risk_hour = hours_1_to_24[i]
    else:
        direction = "不动"
        d3_price = 0.0
        
    # 干预后(买卖落地后)的最终合约数据
    cum_contract_post = cum_contract_pre + d3_volume 
    final_dev_pct = (cum_actual_pre - cum_contract_post) / cum_contract_post if cum_contract_post > 0 else 0
    
    # 动态计算干预后最终的超缺额数据
    val_shortage_post = cum_actual_pre * coef_actual - cum_contract_post * coef_contract_short
    val_excess_post = cum_actual_pre * coef_actual - cum_contract_post * coef_contract_over
    if val_shortage_post < 0:
        net_oe_value_post = val_shortage_post
    elif val_excess_post > 0:
        net_oe_value_post = val_excess_post
    else:
        net_oe_value_post = 0.00
    
    results.append({
        "时点": hours_1_to_24[i],
        "初始超缺额状态": status_oe,          # 表格展示用：操作前的压力评估
        "初始超缺额数据": net_oe_value_pre,   # 图表虚线用：历史+预测，D+3执行前
        "最终超缺额数据": net_oe_value_post,  # 图表实线用：执行D+3买卖后
        "上网_初始": cum_actual_pre,          # 图表虚线用：上网预测汇总
        "上网_最终": cum_actual_pre,          # 图表实线用：(由于买卖不影响上网，两者重合)
        "合约_初始": cum_contract_pre,        # 图表虚线用：未做交易前的合约总和
        "合约_最终": cum_contract_post,       # 图表实线用：做完交易后的合约总和
        "初始偏差率": initial_dev_pct,
        "策略判定": strategy,
        "动作方向": direction,
        "D+3申报量": d3_volume,
        "D+3指导价": d3_price,
        "买入止损线": buy_limit if direction == "买入" else 0.0,
        "操作后最终水位": final_dev_pct
    })

df_results = pd.DataFrame(results)

# ================= 操盘手决策驾驶舱 =================
st.divider()
st.subheader("🎯 操盘手全天战略汇总")
met1, met2, met3, met4 = st.columns(4)
met1.metric(label="全天总计需买入 (MWh)", value=f"{total_buy_vol:.2f}", delta="防守补仓/平掉欠发", delta_color="inverse")
met2.metric(label="全天总计需卖出 (MWh)", value=f"{total_sell_vol:.2f}", delta="主动套利/吃现货差")
met3.metric(label="最具风险买入指导价 (元/MWh)", value=f"{max_buy_price:.2f}", delta=f"预警时点 {max_risk_hour}", delta_color="off")

depth_status = "市场流动性充足" if depth_limit_hit_count == 0 else f"需分时段提前建仓!"
met4.metric(label="触达流动性上限次数", value=depth_limit_hit_count, delta=depth_status, 
            delta_color="normal" if depth_limit_hit_count==0 else "inverse")

# ================= 可视化图表区 =================
st.divider()

# 【原生排版】：图1和图2依然在上面并排
col1, col2 = st.columns(2)
with col1:
    fig1 = make_subplots(specs=[[{"secondary_y": True}]])
    fig1.add_trace(go.Bar(x=edited_forecast_df["时点"], y=edited_forecast_df["预测上网电量(MWh)"], name="预测电量", opacity=0.6, marker_color='#FFA15A'), secondary_y=False)
    fig1.add_trace(go.Scatter(x=edited_forecast_df["时点"], y=edited_forecast_df["预测实时电价(元/MWh)"], name="预测现货价", mode='lines+markers', line=dict(color='#19D3F3', width=2)), secondary_y=True)
    fig1.add_trace(go.Scatter(x=edited_forecast_df["时点"], y=edited_forecast_df["年度合约价(元/MWh)"], name="中长期均价", mode='lines', line=dict(color='gray', width=2, dash='dash')), secondary_y=True)
    fig1.update_layout(title="图1: 24小时量价预测与中长期价差空间", height=400, hovermode="x unified", margin=dict(l=20, r=20, t=40, b=20))
    fig1.update_yaxes(title_text="上网电量 (MWh)", secondary_y=False)
    fig1.update_yaxes(title_text="电价 (元/MWh)", secondary_y=True)
    st.plotly_chart(fig1, use_container_width=True)

with col2:
    colors = ['#EF553B' if val < 0 else '#00CC96' for val in df_results["D+3申报量"]]
    fig2 = go.Figure(data=[go.Bar(x=df_results["时点"], y=df_results["D+3申报量"], marker_color=colors, text=df_results["动作方向"])])
    fig2.add_hline(y=max_trade_vol, line_dash="dash", line_color="rgba(255,0,0,0.5)", annotation_text="流动性上限")
    fig2.add_hline(y=-max_trade_vol, line_dash="dash", line_color="rgba(255,0,0,0.5)", annotation_text="流动性下限")
    fig2.update_layout(title="图2: D+3 执行单量 (触顶将被强行截断)", height=400, margin=dict(l=20, r=20, t=40, b=20))
    st.plotly_chart(fig2, use_container_width=True)

# 【原生排版】：图3在下方独占一行（横向填满）
fig3 = go.Figure()
fig3.add_trace(go.Scatter(x=df_results["时点"], y=df_results["初始偏差率"], mode='lines', name='干预前: 初始偏差率', line=dict(color='gray', width=2, dash='dot')))
fig3.add_trace(go.Scatter(x=df_results["时点"], y=df_results["操作后最终水位"], mode='lines+markers', name='干预后: 真实落地水位', line=dict(color='#AB63FA', width=3)))
fig3.add_hline(y=max_dev, line_dash="solid", line_color="#EF553B")
fig3.add_hline(y=-max_dev, line_dash="solid", line_color="#EF553B")
fig3.add_hline(y=alert_dev, line_dash="dash", line_color="#FECB52")
fig3.add_hline(y=-alert_dev, line_dash="dash", line_color="#FECB52")
fig3.layout.yaxis.tickformat = '.1%'
fig3.update_layout(title="图3: 水库对冲监控视图 (紫线越平稳，策略越优)", height=350, hovermode="x unified")
st.plotly_chart(fig3, use_container_width=True)

# 【新增图4】：紧跟在图3下方，完全复刻三条平滑曲线结构，并加入干预前后的虚实线对比
fig4 = go.Figure()

# --- 虚线组 (干预前 / 评估策略前的初始压力状态) ---
fig4.add_trace(go.Scatter(x=df_results["时点"], y=df_results["合约_初始"], mode='lines', name='干预前: 合约', 
                          line=dict(color='#3498db', width=2, dash='dash', shape='spline'), opacity=0.6))
fig4.add_trace(go.Scatter(x=df_results["时点"], y=df_results["上网_初始"], mode='lines', name='干预前: 上网', 
                          line=dict(color='#e67e22', width=2, dash='dash', shape='spline'), opacity=0.6))
fig4.add_trace(go.Scatter(x=df_results["时点"], y=df_results["初始超缺额数据"], mode='lines', name='干预前: 超缺额', 
                          line=dict(color='#f1c40f', width=2, dash='dash', shape='spline'), opacity=0.6))

# --- 实线组 (干预后 / 执行D+3买卖后的最终状态) ---
fig4.add_trace(go.Scatter(x=df_results["时点"], y=df_results["合约_最终"], mode='lines+markers', name='干预后: 合约', 
                          line=dict(color='#3498db', width=3, shape='spline'), 
                          marker=dict(symbol='circle', size=6, color='white', line=dict(color='#3498db', width=2))))
fig4.add_trace(go.Scatter(x=df_results["时点"], y=df_results["上网_最终"], mode='lines+markers', name='干预后: 上网', 
                          line=dict(color='#e67e22', width=3, shape='spline'), 
                          marker=dict(symbol='circle', size=6, color='white', line=dict(color='#e67e22', width=2))))
fig4.add_trace(go.Scatter(x=df_results["时点"], y=df_results["最终超缺额数据"], mode='lines+markers', name='干预后: 超缺额', 
                          line=dict(color='#f1c40f', width=3, shape='spline'), 
                          marker=dict(symbol='circle', size=6, color='white', line=dict(color='#f1c40f', width=2))))

fig4.update_layout(title="图4: 仓位上网与超缺额走势曲线 (虚线: D+3交易前预测状态 | 实线: D+3交易落地后)", height=380, hovermode="x unified", margin=dict(l=20, r=20, t=40, b=20))
fig4.update_xaxes(showgrid=True, gridwidth=1, gridcolor='rgba(200,200,200,0.3)')
fig4.update_yaxes(showgrid=True, gridwidth=1, gridcolor='rgba(200,200,200,0.3)', title="数据指标 (MWh)")
st.plotly_chart(fig4, use_container_width=True)


# ================= 详情结果表 =================
with st.expander("📝 展开查看完整 24小时 D+3 台账明细", expanded=True):
    # 拼接时排除掉新增的图表专用数值列，保持台账表格原本的纯净样式
    display_results = df_results.drop(columns=["时点", "初始超缺额数据", "最终超缺额数据", "上网_初始", "上网_最终", "合约_初始", "合约_最终"])
    display_df_full = pd.concat([edited_forecast_df, display_results], axis=1)
    
    st.dataframe(display_df_full.style.format({
        "预测上网电量(MWh)": "{:.2f}", "预测实时电价(元/MWh)": "{:.2f}",
        "年度合约量(MWh)": "{:.2f}", "年度合约价(元/MWh)": "{:.2f}",
        "初始偏差率": "{:.2%}", "D+3申报量": "{:.2f}",
        "D+3指导价": "{:.2f}", "买入止损线": lambda x: f"{x:.2f}" if x > 0 else "-",
        "操作后最终水位": "{:.2%}"
    }).map(
        # 【终极UI修复】：使用 RGBA 20% 透明度。白天是小清新，黑夜是高级暗红。自动适配字体颜色！
        lambda x: "background-color: rgba(255, 75, 75, 0.2);" if "🚨" in str(x) or "⚠️" in str(x) or "缺额" in str(x) or "超额 +" in str(x) 
        else ("background-color: rgba(255, 170, 0, 0.2);" if "🛑" in str(x) 
        else ("background-color: rgba(128, 128, 128, 0.2);" if "⏸️" in str(x) 
        else ("background-color: rgba(0, 200, 0, 0.2);" if "✅" in str(x) else ""))), 
        subset=["策略判定", "初始超缺额状态"]
    ), 
    use_container_width=True, height=880)







