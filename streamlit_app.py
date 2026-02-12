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
import random
import threading

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
    page_title="参考文献核查",
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
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_system_status():
    """创建全局共享状态，包含信号量、任务开始时间和取消标志"""
    return {
        "lock": threading.Semaphore(3),  # 允许最多3人同时查询
        "start_time": None,
        "active_users": 0,               # 当前活跃用户数
        "cancel_requested": False
    }


def get_text_hash(text: str) -> str:
    """计算文本的 MD5 哈希值作为缓存键"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()


# 使用 24 小时缓存 (86400 秒)
@st.cache_data(ttl=86400, show_spinner=False)
def process_single_ref_cached(ref_text: str, ref_hash: str) -> dict:
    """
    缓存单条参考文献的处理结果
    如果上次处理超时（timeout_error=True），则不缓存，下次重新请求
    
    Args:
        ref_text: 参考文献原文
        ref_hash: 用于缓存键的哈希值
        
    Returns:
        处理结果字典
    """
    all_authors_count = {}
    all_doi_count = {}
    result = process_single_reference_new(ref_text, 1, 1, all_authors_count, all_doi_count)
    
    # 如果该条目超时，抛出异常使 st.cache_data 不缓存此结果
    if result.get('timeout_error', False):
        raise Exception("TIMEOUT_NO_CACHE")
    
    return result


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
        # 检查是否被取消
        system_status = get_system_status()
        if system_status["cancel_requested"]:
            status_container.warning(f"任务已被中断，已处理 {idx-1}/{total} 条")
            break
        
        status_container.info(f"正在处理 {idx}/{total}：{ref[:50]}...")
        
        # 计算该条参考文献的哈希
        ref_hash = get_text_hash(ref)
        
        # 使用缓存处理
        try:
            result = process_single_ref_cached(ref, ref_hash)
        except Exception as e:
            if "TIMEOUT_NO_CACHE" in str(e):
                # 超时的条目：构造临时结果，标记 timeout_error
                result = {
                    "original_text": ref,
                    "extracted_doi": "",
                    "api_doi": "",
                    "match_status": "None",
                    "has_retraction": False,
                    "has_correction": False,
                    "title": "",
                    "journal": "",
                    "year": "",
                    "all_authors": [],
                    "pmid": "",
                    "pmcid": "",
                    "is_recent_5_years": False,
                    "is_recent_3_years": False,
                    "ai_diagnosis": "",
                    "ai_extracted_title": "",
                    "ai_extracted_url": "",
                    "ai_search_query": "",
                    "timeout_error": True,
                    "matched_ref": "Not Found",
                    "similarity": 0,
                }
            else:
                raise
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
        time.sleep(0.5 + random.uniform(0, 0.5))
    
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
    # --- 侧边栏管理工具 ---
    system_status = get_system_status()
    with st.sidebar:
        st.header("管理工具")
        st.divider()
        
        # 实时显示当前状态
        st.subheader("系统状态")
        current_active = system_status["active_users"]
        st.metric("当前活跃任务数", f"{current_active} / 3")
        
        if current_active > 0:
            st.info(f"系统运行中，当前 {current_active} 人正在使用")
        else:
            st.success("系统空闲，可正常使用")
        
        st.divider()
        
        # 紧急重置按钮
        st.subheader("紧急操作")
        st.caption("不要随便点！当系统出现死锁（没人使用却显示繁忙）时，可使用下方按钮强制重置。")
        if st.button("强制重置系统锁", use_container_width=True, type="secondary"):
            system_status["lock"] = threading.Semaphore(3)
            system_status["active_users"] = 0
            system_status["cancel_requested"] = False
            system_status["start_time"] = None
            st.toast("系统锁已强制重置", icon="✅")
            st.success("系统锁已强制重置，信号量已恢复为 3。")
    
    # --- 主界面 ---
    # 标题区域 - 改为原生简洁风格
    st.title("参考文献核查")
    st.caption("粘贴参考文献 → 自动匹配验证 → 生成核查报告")
    
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
    col1, col2 = st.columns([1, 1])
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
        
        # 获取全局共享状态
        system_status = get_system_status()
        
        # 自动超时保护：如果任务超过3小时，强制释放一个信号量（防止用户关浏览器导致死锁）
        if system_status["start_time"] and system_status["active_users"] >= 3:
            elapsed = (datetime.datetime.now() - system_status["start_time"]).total_seconds()
            if elapsed > 10800:  # 3小时超时
                system_status["start_time"] = None
                system_status["cancel_requested"] = False
                system_status["active_users"] = max(0, system_status["active_users"] - 1)
                try:
                    system_status["lock"].release()
                except ValueError:
                    pass
                st.info("上一个任务已超时（3小时），已自动释放一个名额。")
        
        # 尝试获取锁（非阻塞模式）
        if not system_status["lock"].acquire(blocking=False):
            # 格式化开始时间
            start_time_str = system_status["start_time"].strftime("%H:%M:%S") if system_status["start_time"] else "不久前"
            
            st.warning(f"""
                ### 系统繁忙：已达到最大并发数（3人）
                为了避免 API 被并发请求挤爆，系统最多允许3人同时查询。
                
                - **当前活跃用户**：{system_status['active_users']} 人
                - **首个任务开始于**：`{start_time_str}`
                - **预计用时**：通常处理一篇文章（约30-60条文献）需要 **3-5 分钟**。（实际用时与当前网络环境有关）
                
                请您稍后刷新页面再试。
            """)
            return
        
        try:
            # 记录任务开始时间，重置取消标志，增加活跃用户数
            if system_status["start_time"] is None:
                system_status["start_time"] = datetime.datetime.now()
            system_status["active_users"] = system_status.get("active_users", 0) + 1
            system_status["cancel_requested"] = False
            
            # 按行分割，过滤空行
            refs = [line.strip() for line in ref_input.strip().split('\n') if line.strip()]
            
            if len(refs) == 0:
                st.warning("未识别到有效的参考文献，请检查输入格式")
                return
            
            st.info(f"共识别到 **{len(refs)}** 条参考文献，开始处理...")
            
            # 处理参考文献
            results, stats = process_references(refs)
            
            # 检查是否有超时的条目，向用户展示警告
            timeout_refs = []
            for i, res in enumerate(results, 1):
                if res.get('timeout_error', False):
                    timeout_refs.append(f"Ref.{i}")
            
            if timeout_refs:
                st.warning(
                    f"⚠️ 以下条目因网络超时未能获取数据：{', '.join(timeout_refs)}。\n\n"
                    f"建议：获取全部文献后，**不要清除缓存**，重新点击「开始处理」，"
                    f"系统将仅重新请求超时的条目，已成功的条目会直接使用缓存。"
                )
            
            # 存储到 session_state 以便后续显示
            st.session_state['results'] = results
            st.session_state['stats'] = stats
            st.session_state['project_id'] = project_id
        finally:
            # 处理完成后，减少活跃用户数并释放信号量
            system_status["active_users"] = max(0, system_status.get("active_users", 1) - 1)
            if system_status["active_users"] == 0:
                system_status["start_time"] = None
                system_status["cancel_requested"] = False
            system_status["lock"].release()
    
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
