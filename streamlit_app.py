"""
参考文献核查工具 - Streamlit Web 应用
支持 24 小时缓存机制，用户粘贴参考文献后按行识别并生成 HTML 报告
"""

import streamlit as st
import hashlib
import json
import tempfile
import os
import time
import datetime

# 导入核心处理函数
from generate_json import (
    process_single_reference_new, 
    find_fuzzy_duplicates, 
    calculate_statistics,
    extract_doi_from_text
)
from generate_html import generate_html_report

# 页面配置
st.set_page_config(
    page_title="参考文献核查工具",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# 自定义样式
st.markdown("""
<style>
    .stTextArea textarea {
        font-family: 'Consolas', 'Monaco', monospace;
        font-size: 13px;
    }
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 20px;
        border-radius: 10px;
        margin-bottom: 20px;
    }
    .main-header h1 {
        margin: 0;
        font-size: 28px;
    }
    .main-header p {
        margin: 5px 0 0 0;
        opacity: 0.9;
    }
</style>
""", unsafe_allow_html=True)


def get_text_hash(text: str) -> str:
    """计算文本的 MD5 哈希值作为缓存键"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()


# 使用 24 小时缓存 (86400 秒)
@st.cache_data(ttl=86400, show_spinner=False)
def process_single_ref_cached(ref_text: str, ref_hash: str) -> dict:
    """
    缓存单条参考文献的处理结果
    
    Args:
        ref_text: 参考文献原文
        ref_hash: 用于缓存键的哈希值
        
    Returns:
        处理结果字典
    """
    # 注意：这里不传递共享的计数器，因为缓存函数需要独立运行
    all_authors_count = {}
    all_doi_count = {}
    return process_single_reference_new(ref_text, 1, 1, all_authors_count, all_doi_count)


def process_references(refs: list) -> tuple:
    """
    处理参考文献列表
    
    Args:
        refs: 参考文献列表
        
    Returns:
        (results, stats) 元组
    """
    total = len(refs)
    results = []
    all_authors_count = {}
    all_doi_count = {}
    
    # 创建进度条和状态显示
    progress_bar = st.progress(0)
    status_container = st.empty()
    
    for idx, ref in enumerate(refs, 1):
        status_container.info(f"正在处理 {idx}/{total}：{ref[:50]}...")
        
        # 计算该条参考文献的哈希
        ref_hash = get_text_hash(ref)
        
        # 使用缓存处理
        result = process_single_ref_cached(ref, ref_hash)
        results.append(result)
        
        # 更新全局计数器（用于高频作者统计）
        for author_name in result.get('all_authors', []):
            if author_name:
                all_authors_count[author_name] = all_authors_count.get(author_name, 0) + 1
        
        api_doi = result.get('api_doi', '')
        if api_doi:
            all_doi_count[api_doi] = all_doi_count.get(api_doi, 0) + 1
        
        # 更新进度
        progress_bar.progress(idx / total)
        
        # 适当延迟避免 API 限速（缓存命中时不需要延迟）
        time.sleep(0.1)
    
    status_container.success(f"处理完成！共处理 {total} 条参考文献")
    
    # 执行模糊查重
    with st.spinner("正在进行查重分析..."):
        duplicate_info, fuzzy_pairs = find_fuzzy_duplicates(results)
        for index, info in duplicate_info.items():
            if index < len(results):
                results[index]['fuzzy_duplicates'] = info
    
    # 计算统计信息
    stats = calculate_statistics(results, total, fuzzy_pairs)
    
    return results, stats


def display_dashboard(stats: dict):
    """显示统计仪表板"""
    st.subheader("统计概览")
    
    # 第一行：核心指标
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(
            "总参考文献",
            stats.get('total_references', 0)
        )
    with col2:
        matched = stats.get('matched_refs', 0)
        matched_pct = stats.get('matched_refs_pct', 0)
        st.metric(
            "匹配成功",
            f"{matched}",
            f"{matched_pct:.1f}%"
        )
    with col3:
        recent5 = stats.get('recent_5_years', 0)
        recent5_pct = stats.get('recent_5_years_pct', 0)
        st.metric(
            "近5年",
            f"{recent5}",
            f"{recent5_pct:.1f}%"
        )
    with col4:
        with_doi = stats.get('with_doi', 0)
        with_doi_pct = stats.get('with_doi_pct', 0)
        st.metric(
            "有DOI",
            f"{with_doi}",
            f"{with_doi_pct:.1f}%"
        )
    
    # 第二行：风险指标
    col5, col6, col7, col8 = st.columns(4)
    with col5:
        st.metric(
            "撤稿",
            stats.get('retraction_count', 0),
            delta_color="inverse"
        )
    with col6:
        st.metric(
            "更正",
            stats.get('correction_count', 0)
        )
    with col7:
        st.metric(
            "DOI重复",
            stats.get('duplicate_refs', 0),
            delta_color="inverse"
        )
    with col8:
        st.metric(
            "模糊重复",
            stats.get('fuzzy_duplicate_pairs', 0),
            delta_color="inverse"
        )


def generate_and_offer_download(results: list, stats: dict, project_id: str = ""):
    """生成 HTML 报告并提供下载"""
    st.subheader("下载报告")
    
    # 为结果添加缺失字段
    for res in results:
        res.setdefault('fuzzy_duplicates', '')
        res.pop('cleaned_original_ref', None)
    
    # 创建临时 JSON 文件
    output_data = {
        "statistics": stats,
        "results": results
    }
    
    # 使用临时目录
    temp_dir = tempfile.mkdtemp()
    json_path = os.path.join(temp_dir, "temp_cache.json")
    
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        
        # 生成 HTML 报告
        html_path = generate_html_report(json_path)
        
        # 读取 HTML 内容
        with open(html_path, 'r', encoding='utf-8') as f:
            html_content = f.read()
        
        # 生成文件名：如果有项目ID则使用 ID_年月日时分，否则只用时间戳
        timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M')
        if project_id.strip():
            # 清理项目ID中的非法字符
            safe_id = "".join(c for c in project_id.strip() if c.isalnum() or c in '-_')
            file_prefix = f"{safe_id}_{timestamp}"
        else:
            file_prefix = f"report_{timestamp}"
        
        col1, col2 = st.columns(2)
        
        with col1:
            st.download_button(
                label="下载 HTML 报告",
                data=html_content,
                file_name=f"{file_prefix}.html",
                mime="text/html",
                type="primary",
                use_container_width=True
            )
        
        with col2:
            # 也提供 JSON 下载
            json_content = json.dumps(output_data, ensure_ascii=False, indent=2)
            st.download_button(
                label="下载 JSON 数据",
                data=json_content,
                file_name=f"{file_prefix}_cache.json",
                mime="application/json",
                use_container_width=True
            )
        
    finally:
        # 清理临时文件
        try:
            if os.path.exists(json_path):
                os.unlink(json_path)
            if os.path.exists(html_path):
                os.unlink(html_path)
            os.rmdir(temp_dir)
        except:
            pass


def display_results_table(results: list):
    """显示结果表格预览"""
    st.subheader("结果预览")
    
    # 构建表格数据
    table_data = []
    for idx, item in enumerate(results, 1):
        # 使用文字状态代替emoji
        if item.get('has_retraction'):
            status = "撤稿"
        elif item.get('ai_diagnosis') == 'HIGH_RISK':
            status = "高危"
        elif item.get('fuzzy_duplicates'):
            status = "重复"
        elif item.get('match_status') == 'match':
            status = "通过"
        else:
            status = "未匹配"
        
        table_data.append({
            "#": idx,
            "状态": status,
            "参考文献": item.get('original_text', '')[:100] + "..." if len(item.get('original_text', '')) > 100 else item.get('original_text', ''),
            "DOI": item.get('api_doi', '') or item.get('extracted_doi', '') or "-",
            "匹配": item.get('match_status', 'None'),
            "AI诊断": item.get('ai_diagnosis', '') or "-"
        })
    
    st.dataframe(
        table_data,
        use_container_width=True,
        hide_index=True,
        column_config={
            "#": st.column_config.NumberColumn(width="small"),
            "状态": st.column_config.TextColumn(width="small"),
            "参考文献": st.column_config.TextColumn(width="large"),
            "DOI": st.column_config.TextColumn(width="medium"),
            "匹配": st.column_config.TextColumn(width="small"),
            "AI诊断": st.column_config.TextColumn(width="small")
        }
    )


def main():
    """主函数"""
    # 标题区域
    st.markdown("""
    <div class="main-header">
        <h1>参考文献核查工具</h1>
        <p>粘贴参考文献 → 自动匹配验证 → 生成核查报告</p>
    </div>
    """, unsafe_allow_html=True)
    
    # 使用说明
    with st.expander("使用说明", expanded=False):
        st.markdown("""
        **使用步骤：**
        1. （可选）输入项目ID，用于命名导出文件
        2. 在下方文本框中粘贴参考文献（每行一条）
        3. 点击「开始处理」按钮
        4. 等待处理完成后查看统计结果
        5. 下载 HTML 核查报告
        
        **缓存机制：**
        - 系统会缓存每条参考文献的处理结果，有效期 24 小时
        - 相同的参考文献再次处理时会直接使用缓存，大幅提升速度
        
        **输入格式：**
        - 每行一条参考文献
        - 支持带编号（如 `1.` `[1]`）或不带编号
        - 自动识别 DOI 链接
        """)
    
    # 项目ID输入（可选）
    project_id = st.text_input(
        "项目ID（可选，用于命名导出文件）",
        placeholder="例如：EDHT-2026-00008",
        help="输入后，导出的文件名将为：项目ID_年月日时分.html"
    )
    
    # 输入区域
    st.subheader("输入参考文献")
    
    ref_input = st.text_area(
        "请粘贴参考文献（每行一条）：",
        height=300,
        placeholder="""示例：
1. Smith, J., & Johnson, A. (2023). Example article title. Journal of Examples, 15(3), 123-145. https://doi.org/10.1234/example.2023
2. Brown, M. (2022). Another research paper. Science Today, 8(2), 56-78.
3. Davis, K., et al. (2021). Important findings in research. Nature Reviews, 10(1), 1-20.""",
        label_visibility="collapsed"
    )
    
    # 处理按钮
    col1, col2, col3 = st.columns([1, 1, 2])
    with col1:
        process_btn = st.button("开始处理", type="primary", use_container_width=True)
    with col2:
        clear_btn = st.button("清空缓存", use_container_width=True)
    
    if clear_btn:
        st.cache_data.clear()
        st.success("缓存已清空！")
    
    # 存储项目ID到session_state
    if project_id:
        st.session_state['project_id'] = project_id
    
    # 处理逻辑
    if process_btn:
        if not ref_input.strip():
            st.warning("请先输入参考文献")
            return
        
        # 按行分割，过滤空行
        refs = [line.strip() for line in ref_input.strip().split('\n') if line.strip()]
        
        if len(refs) == 0:
            st.warning("未识别到有效的参考文献，请检查输入格式")
            return
        
        st.info(f"共识别到 **{len(refs)}** 条参考文献，开始处理...")
        
        # 处理参考文献
        results, stats = process_references(refs)
        
        # 存储到 session_state 以便后续显示
        st.session_state['results'] = results
        st.session_state['stats'] = stats
        st.session_state['project_id'] = project_id
    
    # 显示结果（如果有）
    if 'results' in st.session_state and 'stats' in st.session_state:
        st.divider()
        
        # 显示仪表板
        display_dashboard(st.session_state['stats'])
        
        st.divider()
        
        # 显示结果表格
        display_results_table(st.session_state['results'])
        
        st.divider()
        
        # 生成并提供下载（传入项目ID）
        generate_and_offer_download(
            st.session_state['results'],
            st.session_state['stats'],
            st.session_state.get('project_id', '')
        )


if __name__ == "__main__":
    main()
