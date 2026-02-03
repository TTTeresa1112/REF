# REF - 参考文献核查工具

一个用于学术参考文献自动核查与审计的工具，支持 DOI 匹配验证、撤稿检测、重复查重等功能。

## 功能特点

- **DOI 优先匹配**：通过 Crossref API 验证参考文献
- **撤稿/更正检测**：通过 NLM API 检查文献状态
- **AI 智能诊断**：识别书籍、会议、预印本、网页等非期刊类型
- **重复检测**：DOI 精确重复 + 模糊文本重复
- **HTML 报告**：生成交互式可筛选的审计报告
- **24小时缓存**：Streamlit 应用支持结果缓存，避免重复 API 调用

## 安装

```bash
# 克隆项目
git clone https://github.com/TTTeresa1112/REF.git
cd REF

# 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac

# 安装依赖
pip install -r requirements.txt
```

## 配置

在项目根目录创建 `.env` 文件：

```env
DASHSCOPE_API_KEY=your_dashscope_api_key
NCBI_API_KEY=your_ncbi_api_key
MY_EMAIL=your_email@example.com
```

## 使用方式

### 方式一：Streamlit Web 应用（推荐）

```bash
streamlit run streamlit_app.py
```

访问 http://localhost:8501，在文本框中粘贴参考文献（每行一条），点击"开始处理"。

### 方式二：桌面 GUI

```bash
python main.py
```

选择 CSV/Excel 文件进行处理。

## 文件说明

| 文件 | 说明 |
|------|------|
| `streamlit_app.py` | Streamlit Web 应用入口 |
| `main.py` | Tkinter 桌面 GUI 入口 |
| `generate_json.py` | 核心处理逻辑（API 查询、匹配、诊断） |
| `generate_html.py` | HTML 报告生成 |
| `generate_reflist.py` | NLM API 补充查询 |

## License

MIT
