import streamlit as st
from datetime import datetime
import pandas as pd
import io

# 导入你写好的核心逻辑
from scraper import scrape
from exporter import export_csv, export_excel

st.set_page_config(page_title="舆情监控大盘", layout="wide")
st.title("📊 舆情监控系统")
st.markdown("基于 DuckDuckGo 的轻量级舆情抓取工具")

# --- 侧边栏配置 ---
with st.sidebar:
    st.header("⚙️ 抓取设置")
    keywords = st.text_input("输入关键词 (如: 人工智能)", value="人工智能")
    
    col1, col2 = st.columns(2)
    with col1:
        start_date = st.date_input("开始日期")
    with col2:
        end_date = st.date_input("结束日期")
        
    sites = st.text_area("指定站点 (可选, 每行一个)", placeholder="zhihu.com\nsspai.com")
    max_results = st.number_input("每个站点最大抓取数", min_value=1, max_value=50, value=10)
    
    start_btn = st.button("🚀 开始抓取", type="primary", use_container_width=True)

# --- 主逻辑 ---
if start_btn:
    if not keywords:
        st.warning("请先输入关键词！")
    else:
        # 处理输入参数
        site_list = [s.strip() for s in sites.split('\n') if s.strip()]
        
        # 抓取过程状态提示
        status_text = st.empty()
        
        # 定义一个回调函数，把 scraper 里的日志实时打印到网页上
        def progress_log(msg):
            status_text.info(f"🔄 运行中: {msg}")

        with st.spinner('正在全网检索并提取正文，请耐心等待...'):
            try:
                # 直接调用 scraper.py 里的函数
                results = scrape(
                    keywords=[keywords],
                    sites=site_list,
                    start_date=start_dt,  
                    end_date=end_dt,
                    max_per_site=max_results,
                    progress_callback=progress_log
                )
                
                status_text.empty() # 清空进度提示
                
                if results:
                    st.success(f"🎉 抓取完成！共获取 {len(results)} 条有效数据。")
                    
                    # 转为 DataFrame 方便在网页展示
                    df = pd.DataFrame(results)
                    # 调整显示的列
                    display_cols = ['title', 'platform', 'publish_date', 'snippet']
                    st.dataframe(df[display_cols], use_container_width=True)
                    
                    # --- 下载按钮 ---
                    st.subheader("📥 导出数据")
                    
                    # 导出 CSV
                    csv_buffer = io.StringIO()
                    df.to_csv(csv_buffer, index=False)
                    st.download_button(
                        label="下载 CSV 文件",
                        data=csv_buffer.getvalue().encode('utf-8-sig'),
                        file_name=f"舆情数据_{datetime.now().strftime('%Y%m%d')}.csv",
                        mime="text/csv"
                    )
                else:
                    st.warning("未能抓取到符合条件的数据，请尝试更换关键词或扩大时间范围。")
                    
            except Exception as e:
                st.error(f"抓取过程中发生错误: {e}")
