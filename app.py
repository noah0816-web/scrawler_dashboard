import streamlit as st
from datetime import datetime
import pandas as pd
import io

# 导入你写好的核心逻辑
from scraper import scrape

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
        
    st.markdown("---")
    st.markdown("**指定抓取范围**")
    sites_input = st.text_area("指定站点或种子URL (每行一个)", 
                               placeholder="zhihu.com\nhttps://www.toutiao.com/...")
    max_results = st.number_input("每个站点最大抓取数", min_value=1, max_value=50, value=10)
    
    start_btn = st.button("🚀 开始抓取", type="primary", use_container_width=True)

# --- 主逻辑 ---
if start_btn:
    if not keywords:
        st.warning("请先输入关键词！")
    else:
        # 智能解析：区分输入的是纯域名还是完整网址
        raw_lines = [s.strip() for s in sites_input.split('\n') if s.strip()]
        site_list = []
        seed_list = []
        for line in raw_lines:
            if line.startswith("http://") or line.startswith("https://"):
                seed_list.append(line)
            else:
                site_list.append(line)
        
        status_text = st.empty()
        
        def progress_log(msg):
            status_text.info(f"🔄 运行中: {msg}")

        with st.spinner('正在检索并提取正文，请耐心等待...'):
            try:
                # 修复日期格式问题
                start_dt = datetime.combine(start_date, datetime.min.time())
                end_dt = datetime.combine(end_date, datetime.max.time())
                
                # 调用爬虫核心逻辑
                results = scrape(
                    keywords=[keywords],
                    sites=site_list,
                    seed_urls=seed_list,
                    start_date=start_dt,
                    end_date=end_dt,
                    max_per_site=max_results,
                    progress_callback=progress_log
                )
                
                status_text.empty() # 清空进度提示
                
                if results:
                    st.success(f"🎉 抓取完成！共获取 {len(results)} 条有效数据。")
                    
                    df = pd.DataFrame(results)
                    display_cols = ['title', 'platform', 'publish_date', 'snippet']
                    show_cols = [c for c in display_cols if c in df.columns]
                    st.dataframe(df[show_cols], use_container_width=True)
                    
                    # --- 下载按钮区域 ---
                    st.subheader("📥 导出数据")
                    col_csv, col_excel = st.columns(2)
                    
                    # 导出 CSV
                    csv_data = df.to_csv(index=False).encode('utf-8-sig')
                    with col_csv:
                        st.download_button(
                            label="📄 下载 CSV 文件",
                            data=csv_data,
                            file_name=f"舆情数据_{datetime.now().strftime('%Y%m%d')}.csv",
                            mime="text/csv",
                            use_container_width=True
                        )
                        
                    # 导出 Excel (完美解决中文乱码)
                    excel_buffer = io.BytesIO()
                    df.to_excel(excel_buffer, index=False, engine='openpyxl')
                    with col_excel:
                        st.download_button(
                            label="📊 下载 Excel 文件",
                            data=excel_buffer.getvalue(),
                            file_name=f"舆情数据_{datetime.now().strftime('%Y%m%d')}.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            use_container_width=True
                        )
                else:
                    st.warning("未能抓取到符合条件的数据，请尝试更换关键词或扩大时间范围。")
                    
            # ↓↓↓ 就是这里，刚才被不小心删掉的报错兜底逻辑 ↓↓↓
            except Exception as e:
                st.error(f"抓取过程中发生错误: {e}")
