import json
import os
import datetime
from urllib.parse import quote_plus

def generate_html_report(json_file_path: str) -> str:
    """
    从JSON缓存生成交互式HTML审计报告
    
    Args:
        json_file_path: JSON缓存文件路径
        
    Returns:
        生成的HTML文件路径
    """
    # 读取JSON数据
    with open(json_file_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    results = data.get('results', [])
    stats = data.get('statistics', {})
    
    # 1. 数据预计算
    high_risk_count = 0
    retracted_count = 0
    match_count = 0
    mismatch_count = 0
    
    # DOI重复统计
    doi_count_map = {}
    for idx, item in enumerate(results):
        api_doi = item.get('api_doi', item.get('doi', ''))
        if api_doi:
            if api_doi not in doi_count_map:
                doi_count_map[api_doi] = []
            doi_count_map[api_doi].append(idx)
    
    # 标记DOI重复项并统计，同时生成重复信息文本
    doi_duplicate_count = 0
    for idx, item in enumerate(results):
        api_doi = item.get('api_doi', item.get('doi', ''))
        if api_doi and len(doi_count_map.get(api_doi, [])) > 1:
            item['is_doi_duplicate'] = True
            doi_duplicate_count += 1
            # 生成DOI重复信息文本（显示与哪些ref重复）
            other_refs = [str(i + 1) for i in doi_count_map[api_doi] if i != idx]
            item['doi_duplicate_info'] = f"DOI与ref. {', '.join(other_refs)} 重复"
        else:
            item['is_doi_duplicate'] = False
            item['doi_duplicate_info'] = ''
    
    # 模糊重复统计
    fuzzy_duplicate_count = 0
    for item in results:
        if item.get('fuzzy_duplicates'):
            fuzzy_duplicate_count += 1
    
    # 统计高频作者
    author_count = {}
    
    for item in results:
        ai_diag = item.get('ai_diagnosis', '')
        match_status = item.get('match_status', '')
        has_retraction = item.get('has_retraction', False)
        
        if ai_diag == 'HIGH_RISK':
            high_risk_count += 1
        if has_retraction is True or has_retraction == '是':
            retracted_count += 1
        if match_status == 'match':
            match_count += 1
        if match_status == 'doi_mismatch':
            mismatch_count += 1
            
        # 统计作者出现次数
        for author in item.get('all_authors', []):
            if author:
                author_count[author] = author_count.get(author, 0) + 1
    
    # 高频作者 (>3次)
    high_freq_authors = {k: v for k, v in author_count.items() if v > 3}
    
    # 计算百分比
    total_refs = stats.get('total_references', len(results))
    def calc_pct(count):
        return f"{count / total_refs * 100:.1f}%" if total_refs > 0 else "0%"
    
    # 2. 生成HTML
    html_template = '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>参考文献审计报告</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {

            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: #f5f7fa;
            color: #333;
            line-height: 1.5;
            padding: 16px;
            font-size: 14px;
        }
        .container { max-width: 1400px; margin: 0 auto; }
        
        /* Header */
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 14px 24px;
            border-radius: 10px;
            margin-bottom: 16px;
            box-shadow: 0 3px 10px rgba(102, 126, 234, 0.25);
        }
        .header p { opacity: 0.9; font-size: 13px; }
        
        /* Dashboard - Compact */
        .dashboard {
            display: grid;
            grid-template-columns: repeat(5, 1fr);
            gap: 10px;
            margin-bottom: 16px;
        }
        .card {
            background: white;
            border-radius: 8px;
            padding: 12px 14px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.06);
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .card-icon { font-size: 22px; }
        .card-content { flex: 1; }
        .card-value { font-size: 20px; font-weight: 700; color: #1a1a2e; }
        .card-label { color: #666; font-size: 11px; }
        
        .card-retracted { border-left: 3px solid #dc2626; }
        .card-highrisk { border-left: 3px solid #f97316; }
        .card-doi-dup { border-left: 3px solid #ef4444; }
        .card-fuzzy-dup { border-left: 3px solid #fb923c; }
        .card-mismatch { border-left: 3px solid #eab308; }
        .card-match { border-left: 3px solid #22c55e; }
        .card-stat { border-left: 3px solid #8b5cf6; }
        
        /* Filters */
        .filters {
            background: white;
            border-radius: 8px;
            padding: 12px 16px;
            margin-bottom: 12px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.06);
            display: flex;
            gap: 16px;
            align-items: center;
            flex-wrap: wrap;
        }
        .filter-group { display: flex; align-items: center; gap: 8px; }
        .filter-group label { font-size: 12px; color: #666; font-weight: 500; }
        .filter-group select {
            padding: 6px 10px;
            border: 1px solid #e5e7eb;
            border-radius: 6px;
            font-size: 12px;
            background: white;
            cursor: pointer;
        }
        .filter-group select:focus { outline: none; border-color: #667eea; }
        .result-count { margin-left: auto; font-size: 12px; color: #666; }
        
        /* Table */
        .table-container {
            background: white;
            border-radius: 10px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.06);
            overflow: hidden;
        }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 10px 12px; text-align: left; border-bottom: 1px solid #f0f0f0; }
        th { 
            background: #f9fafb; 
            font-weight: 600; 
            color: #374151;
            font-size: 12px;
            position: sticky;
            top: 0;
            white-space: nowrap;
        }
        tr:hover { background: #fafafa; }
        tr.hidden { display: none; }
        
        /* Row backgrounds */
        .row-retracted { background: #ffe6e6 !important; }
        .row-retracted:hover { background: #ffd9d9 !important; }
        .row-high-risk { background: #fff0f0 !important; }
        .row-high-risk:hover { background: #ffe6e6 !important; }
        .row-doi-dup { background: #fee2e2 !important; }
        .row-doi-dup:hover { background: #fecaca !important; }
        .row-fuzzy-dup { background: #ffedd5 !important; }
        .row-fuzzy-dup:hover { background: #fed7aa !important; }
        .row-mismatch { background: #fff3cd !important; }
        .row-mismatch:hover { background: #ffecb3 !important; }
        
        /* Status icons */
        .status-icon { font-size: 18px; text-align: center; }
        
        /* Details column */
        .ref-text { 
            font-size: 12px; 
            color: #4a5568;
            max-width: 600px;
            word-break: break-word;
        }
        .match-info {
            font-size: 11px;
            color: #059669;
            margin-top: 4px;
            padding: 3px 6px;
            background: #ecfdf5;
            border-radius: 4px;
            display: inline-block;
        }
        .author-warning {
            font-size: 11px;
            color: #d97706;
            margin-top: 3px;
        }

        .duplicate-warning {
            font-size: 11px;
            color: #c2410c; 
            margin-top: 4px;
            font-weight: 600;
            background: #ffedd5;
            padding: 2px 6px;
            border-radius: 4px;
            display: inline-block;
            border: 1px solid #fdba74;
        }
        
        /* Badges */
        .badge {
            display: inline-block;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 11px;
            font-weight: 500;
            margin-top: 4px;
        }
        .badge.red { background: #fee2e2; color: #991b1b; }
        .badge.blue { background: #dbeafe; color: #1e40af; }
        .badge.grey { background: #e5e7eb; color: #374151; }
        .badge.purple { background: #ede9fe; color: #6b21a8; }
        .badge.green { background: #d1fae5; color: #065f46; }
        .badge.orange { background: #ffedd5; color: #9a3412; }
        
        /* Action buttons */
        .btn {
            display: inline-block;
            padding: 5px 10px;
            border-radius: 5px;
            font-size: 11px;
            font-weight: 500;
            text-decoration: none;
            transition: all 0.2s;
        }
        .btn-primary { background: #3b82f6; color: white; }
        .btn-primary:hover { background: #2563eb; }
        .btn-secondary { background: #e5e7eb; color: #374151; }
        .btn-secondary:hover { background: #d1d5db; }
        
        /* High freq authors */
        .high-freq-authors {
            background: white;
            padding: 10px 14px;
            border-radius: 8px;
            margin-bottom: 12px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.06);
            border-left: 3px solid #f59e0b;
        }
        .high-freq-authors h4 { font-size: 12px; margin-bottom: 6px; color: #92400e; }
        .author-tag {
            display: inline-block;
            padding: 2px 6px;
            background: #fef08a;
            border-radius: 4px;
            font-size: 11px;
            margin: 2px;
        }
        
        @media (max-width: 768px) {
            .dashboard { grid-template-columns: repeat(2, 1fr); }
            .filters { flex-direction: column; align-items: flex-start; }
            .result-count { margin-left: 0; }
        }

        .doi-dup-warning {
            font-size: 11px;
            color: #991b1b;
            margin-top: 4px;
            font-weight: 600;
            background: #fee2e2;
            padding: 2px 6px;
            border-radius: 4px;
            display: inline-block;
            border: 1px solid #fca5a5;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <p>生成时间: ''' + datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') + '''</p>
        </div>
        
        <!-- Dashboard - Compact Cards -->
        <div class="dashboard">
            <div class="card card-retracted">
                <div class="card-icon">🚨</div>
                <div class="card-content">
                    <div class="card-value">''' + str(retracted_count) + ''' <small>(''' + calc_pct(retracted_count) + ''')</small></div>
                    <div class="card-label">撤稿</div>
                </div>
            </div>
            <div class="card card-highrisk">
                <div class="card-icon">⚠️</div>
                <div class="card-content">
                    <div class="card-value">''' + str(high_risk_count) + ''' <small>(''' + calc_pct(high_risk_count) + ''')</small></div>
                    <div class="card-label">无法判断</div>
                </div>
            </div>
            <div class="card card-doi-dup">
                <div class="card-icon">🔴</div>
                <div class="card-content">
                    <div class="card-value">''' + str(doi_duplicate_count) + ''' <small>(''' + calc_pct(doi_duplicate_count) + ''')</small></div>
                    <div class="card-label">DOI重复</div>
                </div>
            </div>
            <div class="card card-fuzzy-dup">
                <div class="card-icon">🟠</div>
                <div class="card-content">
                    <div class="card-value">''' + str(fuzzy_duplicate_count) + ''' <small>(''' + calc_pct(fuzzy_duplicate_count) + ''')</small></div>
                    <div class="card-label">模糊重复</div>
                </div>
            </div>
            <div class="card card-mismatch">
                <div class="card-icon">💀</div>
                <div class="card-content">
                    <div class="card-value">''' + str(mismatch_count) + ''' <small>(''' + calc_pct(mismatch_count) + ''')</small></div>
                    <div class="card-label">DOI不符</div>
                </div>
            </div>
            <div class="card card-match">
                <div class="card-icon">✅</div>
                <div class="card-content">
                    <div class="card-value">''' + str(match_count) + ''' <small>(''' + calc_pct(match_count) + ''')</small></div>
                    <div class="card-label">通过</div>
                </div>
            </div>
            <div class="card card-stat">
                <div class="card-icon">📊</div>
                <div class="card-content">
                    <div class="card-value">''' + str(total_refs) + '''</div>
                    <div class="card-label">总计</div>
                </div>
            </div>
            <div class="card card-stat">
                <div class="card-icon">🔗</div>
                <div class="card-content">
                    <div class="card-value">''' + str(stats.get('with_doi', 0)) + ''' <small>(''' + calc_pct(stats.get('with_doi', 0)) + ''')</small></div>
                    <div class="card-label">有DOI</div>
                </div>
            </div>
            <div class="card card-stat">
                <div class="card-icon">📅</div>
                <div class="card-content">
                    <div class="card-value">''' + str(stats.get('recent_5_years', 0)) + ''' <small>(''' + calc_pct(stats.get('recent_5_years', 0)) + ''')</small></div>
                    <div class="card-label">近5年</div>
                </div>
            </div>
            <div class="card card-stat">
                <div class="card-icon">🗓️</div>
                <div class="card-content">
                    <div class="card-value">''' + str(stats.get('recent_3_years', 0)) + ''' <small>(''' + calc_pct(stats.get('recent_3_years', 0)) + ''')</small></div>
                    <div class="card-label">近3年</div>
                </div>
            </div>
        </div>
        
        ''' + ('''
        <div class="high-freq-authors">
            <h4>⚠️ 高频引用作者 (>3次)</h4>
            <div>''' + ''.join([f'<span class="author-tag">{author} ({count}次)</span>' for author, count in sorted(high_freq_authors.items(), key=lambda x: -x[1])]) + '''</div>
        </div>
        ''' if high_freq_authors else '') + '''
        
        <!-- Filters -->
        <div class="filters">
            <div class="filter-group">
                <label>状态筛选:</label>
                <select id="statusFilter" onchange="applyFilters()">
                    <option value="all">全部</option>
                    <option value="match">✅ 通过</option>
                    <option value="high-risk">⚠️ 无法判断</option>
                    <option value="retracted">🚨 撤稿</option>
                    <option value="doi-dup">🔴 DOI重复</option>
                    <option value="fuzzy-dup">🟠 模糊重复</option>
                    <option value="mismatch">💀 DOI不符</option>
                    <option value="unknown">❓ 其他</option>
                </select>
            </div>
            <div class="filter-group">
                <label>年份筛选:</label>
                <select id="yearFilter" onchange="applyFilters()">
                    <option value="all">全部</option>
                    <option value="recent3">近3年</option>
                    <option value="recent5">近5年</option>
                    <option value="older">5年前</option>
                </select>
            </div>
            <div class="result-count" id="resultCount">显示 ''' + str(len(results)) + ''' 条</div>
        </div>
        
        <!-- Data Table -->
        <div class="table-container">
            <table>
                <thead>
                    <tr>
                        <th style="width: 40px;">#</th>
                        <th style="width: 40px;">状态</th>
                        <th>详情</th>
                        <th style="width: 80px;">操作</th>
                    </tr>
                </thead>
                <tbody id="tableBody">
'''
    
    # 生成表格行
    for idx, item in enumerate(results, 1):
        original_text = item.get('original_text', item.get('original_ref', ''))
        ai_diag = item.get('ai_diagnosis', '')
        match_status = item.get('match_status', '')
        has_retraction = item.get('has_retraction', False)
        api_doi = item.get('api_doi', item.get('doi', ''))
        title = item.get('title', '')
        journal = item.get('journal', item.get('journal_full_title', ''))
        all_authors = item.get('all_authors', [])
        is_recent_5 = item.get('is_recent_5_years', False)
        is_recent_3 = item.get('is_recent_3_years', False)
        
        # 获取重复状态
        is_doi_dup = item.get('is_doi_duplicate', False)
        is_fuzzy_dup = bool(item.get('fuzzy_duplicates'))
        
        # 确定状态分类 (用于筛选) - 重复优先级较高
        status_category = 'unknown'
        if has_retraction is True or has_retraction == '是':
            status_category = 'retracted'
        elif ai_diag == 'HIGH_RISK':
            status_category = 'high-risk'
        elif is_doi_dup:
            status_category = 'doi-dup'
        elif is_fuzzy_dup:
            status_category = 'fuzzy-dup'
        elif match_status == 'doi_mismatch':
            status_category = 'mismatch'
        elif match_status == 'match':
            status_category = 'match'
        
        # 年份分类
        year_category = 'older'
        if is_recent_3:
            year_category = 'recent3'
        elif is_recent_5:
            year_category = 'recent5'
        
        # 确定行样式
        row_class = ''
        if status_category == 'retracted':
            row_class = 'row-retracted'
        elif status_category == 'high-risk':
            row_class = 'row-high-risk'
        elif status_category == 'doi-dup':
            row_class = 'row-doi-dup'
        elif status_category == 'fuzzy-dup':
            row_class = 'row-fuzzy-dup'
        elif status_category == 'mismatch':
            row_class = 'row-mismatch'
        
        # 确定状态图标
        status_icon = ''
        if status_category == 'retracted':
            status_icon = '🚨'
        elif status_category == 'doi-dup':
            status_icon = '🔴'
        elif status_category == 'fuzzy-dup':
            status_icon = '🟠'
        elif status_category == 'mismatch':
            status_icon = '💀'
        elif status_category == 'high-risk':
            status_icon = '⚠️'
        elif ai_diag == 'BOOK':
            status_icon = '📘'
        elif ai_diag == 'CONF':
            status_icon = '📄'
        elif ai_diag == 'PREPRINT':
            status_icon = '📜'
        elif ai_diag == 'WEBSITE':
            status_icon = '🌐'
        elif status_category == 'match':
            status_icon = '✅'
        else:
            status_icon = '❓'
        
        # AI Badge
        ai_badge = ''
        if ai_diag == 'HIGH_RISK':
            ai_badge = '<span class="badge red">⚠️ 无法判断</span>'
        elif ai_diag == 'BOOK':
            ai_badge = '<span class="badge blue">📘 书籍</span>'
        elif ai_diag == 'CONF':
            ai_badge = '<span class="badge grey">📄 会议</span>'
        elif ai_diag == 'PREPRINT':
            ai_badge = '<span class="badge purple">📜 预印本</span>'
        elif ai_diag == 'WEBSITE':
            ai_badge = '<span class="badge orange">🌐 网页</span>'
        elif ai_diag == 'PATENT':
            ai_badge = '<span class="badge green">📑 专利</span>'
        
        # 匹配信息
        match_info = ''
        if match_status == 'match' and (title or journal):
            display_title = title[:50] + '...' if len(title) > 50 else title
            match_info = f'<div class="match-info">✓ {display_title} | {journal}</div>'
        
        # 高频作者警告
        author_warning = ''
        for author in all_authors:
            if author in high_freq_authors:
                author_warning = f'<div class="author-warning">⚠️ 频繁引用: {author}</div>'
                break
        
        # DOI重复警告
        doi_dup_warning = ''
        doi_dup_info = item.get('doi_duplicate_info', '')
        if doi_dup_info:
            doi_dup_warning = f'<div class="doi-dup-warning">🔴 {doi_dup_info}</div>'
        
        # 模糊重复警告
        dup_warning = ''
        fuzzy_msg = item.get('fuzzy_duplicates', '')
        if fuzzy_msg:
            dup_warning = f'<div class="duplicate-warning">� {fuzzy_msg}</div>'

        # 操作按钮
        action_btn = ''
        if api_doi:
            action_btn = f'<a href="https://doi.org/{api_doi}" target="_blank" class="btn btn-primary">DOI</a>'
        else:
            # 获取AI提取的URL (用于WEBSITE类型)
            ai_extracted_url = item.get('ai_extracted_url', '')
            
            # 如果是WEBSITE类型且有提取的URL，直接链接到该网址
            if ai_diag == 'WEBSITE' and ai_extracted_url:
                action_btn = f'<a href="{ai_extracted_url}" target="_blank" class="btn btn-secondary">访问</a>'
            else:
                # 优先使用AI生成的优化检索式
                ai_search_query = item.get('ai_search_query', '')
                if ai_search_query:
                    # 使用AI生成的优化检索式（特别适合书籍章节和短标题）
                    search_query = ai_search_query
                elif item.get('ai_extracted_title', ''):
                    # 回退：使用AI提取的题目
                    search_query = '"' + item.get('ai_extracted_title', '').replace('"', '').replace("'", '') + '"'
                else:
                    # 最终回退：截取前100字符并清理引号
                    search_query = original_text[:100].replace('"', '').replace("'", '')
                encoded_query = quote_plus(search_query)
                action_btn = f'<a href="https://scholar.google.com/scholar?q={encoded_query}" target="_blank" class="btn btn-secondary">Scholar</a>'
        
    # 生成行HTML (带data属性用于筛选)
        html_template += f'''
                    <tr class="{row_class}" data-status="{status_category}" data-year="{year_category}">
                        <td>{idx}</td>
                        <td class="status-icon">{status_icon}</td>
                        <td>
                            <div class="ref-text">{original_text[:280]}{'...' if len(original_text) > 280 else ''}</div>
                            {ai_badge}
                            
                            {doi_dup_warning}
                            {dup_warning}
                            
                            {match_info}
                            {author_warning}
                        </td>
                        <td>{action_btn}</td>
                    </tr>
'''
    
    html_template += '''
                </tbody>
            </table>
        </div>
    </div>
    
    <script>
        function applyFilters() {
            const statusFilter = document.getElementById('statusFilter').value;
            const yearFilter = document.getElementById('yearFilter').value;
            const rows = document.querySelectorAll('#tableBody tr');
            let visibleCount = 0;
            
            rows.forEach(row => {
                const status = row.getAttribute('data-status');
                const year = row.getAttribute('data-year');
                
                let showByStatus = (statusFilter === 'all' || status === statusFilter);
                let showByYear = (yearFilter === 'all' || year === yearFilter);
                
                // 特殊处理: recent5包含recent3
                if (yearFilter === 'recent5' && (year === 'recent3' || year === 'recent5')) {
                    showByYear = true;
                }
                
                if (showByStatus && showByYear) {
                    row.classList.remove('hidden');
                    visibleCount++;
                } else {
                    row.classList.add('hidden');
                }
            });
            
            document.getElementById('resultCount').textContent = '显示 ' + visibleCount + ' 条';
        }
    </script>
</body>
</html>'''
    
    # 保存HTML文件
    output_html_path = json_file_path.replace('_cache.json', '_report.html').replace('.json', '_report.html')
    with open(output_html_path, 'w', encoding='utf-8') as f:
        f.write(html_template)
    
    return output_html_path
