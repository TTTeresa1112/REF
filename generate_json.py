import requests
import json
import time
import re
import os
import random
import logging
import datetime
from dotenv import load_dotenv
from fuzzywuzzy import fuzz
from urllib.parse import quote_plus
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional, Tuple, Any, Dict, Callable
import dashscope
from dashscope import Generation
from dashscope.api_entities.dashscope_response import Role

# 加载环境变量
load_dotenv()
MY_EMAIL = os.getenv("MY_EMAIL", "teresa.l@explorationpub.com")
NCBI_API_KEY = os.getenv("NCBI_API_KEY") 
USER_AGENT = f"ref for Match scopus/1.0.test (Teresa L <{MY_EMAIL}>)"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='api_requests.log'
)
logger = logging.getLogger(__name__)

@dataclass
class Author:
    """作者信息"""
    family: str
    given: str

    def format_name(self) -> str:
        """格式化作者姓名"""
        if self.family and self.given:
            return f"{self.family} {self.given}".strip()
        elif self.family:
            return self.family
        elif self.given:
            return self.given
        else:
            return ""

def clean_author_name(family: str, given: str) -> str:
    """Standardize author name to 'Family I' format (e.g., 'Smith J')."""
    if not family:
        return ""
    family = family.strip()
    if not given:
        return family
    
    # Extract initials from given name
    # Handle "John Doe" -> "J D" or "John -> J"
    initials = ""
    parts = re.split(r'[\s\.\-]+', given)
    for part in parts:
        if part and part[0].isalpha():
            initials += part[0].upper()
    
    if initials:
        return f"{family} {initials}"
    return family

def extract_authors_regex(text: str) -> List[str]:
    """
    Attempt to extract authors from citation text using regex.
    Heuristic: Usually at the start involving names and initials.
    This is a fallback and might be imperfect.
    """
    # Simple heuristic: Look for patterns like "Smith, J., Doe, A." at the beginning
    # Stop at year like (2020) or title beginning.
    
    # Try to find the year part (e.g., (2020) or 2020.)
    match = re.search(r'\(?\d{4}\)?', text)
    if not match:
        return []
    
    end_index = match.start()
    potential_authors = text[:end_index].strip()
    
    # Split by commas or semi-colons
    splitted = re.split(r'[,;]\s*', potential_authors)
    cleaned_authors = []
    for part in splitted:
        part = part.strip()
        if len(part) > 2 and not any(char.isdigit() for char in part):
            # Try to format as "Family I" if possible, but regex extraction is messy.
            # Here we just keep the extracted string but try to clean slightly.
            cleaned_authors.append(part)
            
    return cleaned_authors


@dataclass
class CrossrefData:
    """Crossref API 返回的数据"""
    doi: str
    title: str
    authors: List[Author]
    journal_short_title: str
    journal_full_title: str
    year: Optional[int]
    volume: str
    issue: str
    page: str
    has_correction: bool = False
    has_retraction: bool = False
    correction_doi: str = ""
    retraction_doi: str = ""
    all_authors: List[str] = None

    @classmethod
    def from_api_response(cls, item: dict) -> 'CrossrefData':
        """从 API 响应创建对象"""
        authors = []
        all_authors_list = []
        
        for author_data in item.get("author", []):
            family = author_data.get("family", "")
            given = author_data.get("given", "")
            authors.append(Author(
                family=family,
                given=given
            ))
            # Clean and add to all_authors list
            clean_name = clean_author_name(family, given)
            if clean_name:
                all_authors_list.append(clean_name)
        
        has_correction = False
        has_retraction = False
        correction_doi = ""
        retraction_doi = ""

        updated_by = item.get('updated-by', [])
        for update in updated_by:
            if isinstance(update, dict):
                update_type = update.get('type', '').lower()
                update_label = update.get('label', '').lower()

                if update_type == 'correction' or 'correction' in update_label:
                    has_correction = True
                    correction_doi = update.get('DOI', '')
                if update_type == 'retraction' or 'retraction' in update_label:
                    has_retraction = True
                    retraction_doi = update.get('DOI', '')

        relation_data = item.get('relation', {})
        if relation_data:
            for rel_type, rel_list in relation_data.items():
                if rel_type in ["is-corrected-by", "corrected-by", "has-correction"]:
                    if rel_list:
                        has_correction = True
                        for rel_item in rel_list:
                            if isinstance(rel_item, dict) and 'id' in rel_item:
                                correction_doi = rel_item['id']
                                break
                            elif isinstance(rel_item, str):
                                correction_doi = rel_item
                                break
                if rel_type in ["is-retracted-by", "retracted-by", "has-retraction"]:
                    if rel_list:
                        has_retraction = True
                        for rel_item in rel_list:
                            if isinstance(rel_item, dict) and 'id' in rel_item:
                                retraction_doi = rel_item['id']
                                break
                            elif isinstance(rel_item, str):
                                retraction_doi = rel_item
                                break

        for update_field in ["update-to", "update-policy"]:
            if update_field in item:
                for update in item[update_field]:
                    if isinstance(update, dict):
                        label = update.get("label", "").lower()
                        if "correction" in label:
                            has_correction = True
                            if 'DOI' in update:
                                correction_doi = update['DOI']
                        if "retract" in label:
                            has_retraction = True
                            if 'DOI' in update:
                                retraction_doi = update['DOI']

        return cls(
            doi=item.get("DOI", ""),
            title=(item.get("title", [""])[0] or ""),
            authors=authors,
            journal_short_title=(item.get("short-container-title") or [""])[0] or "",
            journal_full_title=(item.get("container-title") or [""])[0] or "",
            year=item.get("issued", {}).get("date-parts", [[None]])[0][0],
            volume=item.get("volume", ""),
            issue=item.get("issue", ""),
            page=item.get("page", ""),
            has_correction=has_correction,
            has_retraction=has_retraction,
            correction_doi=correction_doi,
            retraction_doi=retraction_doi,
            all_authors=all_authors_list
        )


def format_authors_for_output(authors: List[Author]) -> str:
    """按照APA格式格式化作者姓名"""
    if not authors:
        return ""
    
    authors = authors[:6]
    
    formatted_names = []
    for author in authors:
        if author.family and author.given:
            given_initials = " ".join([name[0].upper() + "." for name in author.given.split() if name])
            formatted_names.append(f"{author.family}, {given_initials}")
        elif author.family:
            formatted_names.append(author.family)
        elif author.given:
            given_initials = " ".join([name[0].upper() + "." for name in author.given.split() if name])
            formatted_names.append(given_initials)
    
    if not formatted_names:
        return ""
    
    if len(formatted_names) == 1:
        return formatted_names[0]
    elif len(formatted_names) == 2:
        return f"{formatted_names[0]} & {formatted_names[1]}"
    else:
        all_but_last = ", ".join(formatted_names[:-1])
        return f"{all_but_last}, & {formatted_names[-1]}"


def format_reference_apa(crossref_data: CrossrefData) -> str:
    """将Crossref数据格式化为APA格式的参考文献"""
    parts = []
    if crossref_data.authors:
        parts.append(format_authors_for_output(crossref_data.authors) + ".")
    if crossref_data.year:
        parts.append(f"({crossref_data.year}).")
    if crossref_data.title:
        title = crossref_data.title.rstrip('. ')
        parts.append(title + ".")
    if crossref_data.journal_full_title:
        parts.append(crossref_data.journal_full_title + ".")

    volume_issue_parts = []
    if crossref_data.volume:
        if crossref_data.issue:
            volume_issue_parts.append(f"*{crossref_data.volume}*({crossref_data.issue})")
        else:
            volume_issue_parts.append(f"*{crossref_data.volume}*")
    
    if volume_issue_parts:
        parts.append(", ".join(volume_issue_parts) + ",")
    if crossref_data.page:
        parts.append(crossref_data.page + ".")
    if crossref_data.doi:
        parts.append(f"https://doi.org/{crossref_data.doi}")
    return " ".join(parts)


def query_crossref_by_doi(doi: str) -> Optional[CrossrefData]:
    """通过DOI直接查询Crossref"""
    if not doi:
        return None
    clean_doi = doi.strip()
    if clean_doi.startswith('http'):
        clean_doi = clean_doi.split('doi.org/')[-1] if 'doi.org/' in clean_doi else clean_doi
    clean_doi = re.sub(r'[.,;:!?]+$', '', clean_doi)
    clean_doi = re.sub(r'\s+', '', clean_doi)
    clean_doi = clean_doi.strip()
    url = f"https://api.crossref.org/works/{clean_doi}"
    headers = {"User-Agent": USER_AGENT, "mailto": MY_EMAIL}
    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        if 'message' in data:
            return CrossrefData.from_api_response(data['message'])
        return None
    except Exception as e:
        logger.error(f"Crossref DOI查询错误: {e}")
        return None


def extract_doi_from_text(text: str) -> Optional[str]:
    """从文本中提取DOI"""
    if not text:
        return None
    doi_patterns = [
        r'10\.\d{4,}/[-._;()/:\w]+',
        r'doi\.org/(10\.\d{4,}/[-._;()/:\w]+)',
        r'https?://doi\.org/(10\.\d{4,}/[-._;()/:\w]+)',
        r'DOI:\s*(10\.\d{4,}/[-._;()/:\w]+)',
        r'doi:\s*(10\.\d{4,}/[-._;()/:\w]+)',
    ]
    for pattern in doi_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            doi = match.group(1) if len(match.groups()) > 0 else match.group(0)
            doi = doi.strip()
            if doi.startswith('doi.org/'):
                doi = doi[8:]
            doi = re.sub(r'[.,;:!?]+$', '', doi)
            doi = re.sub(r'\s+', '', doi)
            doi = doi.strip()
            if re.match(r'^10\.\d{4,}/[-._;()/:\w]+$', doi):
                return doi
    return None


def query_crossref_search(reference: str) -> Optional[CrossrefData]:
    """通过全文搜索查询Crossref (Fallback)"""
    url = "https://api.crossref.org/works"
    headers = {"User-Agent": USER_AGENT, "mailto": MY_EMAIL} 
    params = {"query.bibliographic": reference, "rows": 1}
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        items = data.get('message', {}).get('items', [])
        if items:
            return CrossrefData.from_api_response(items[0])
        return None
    except Exception as e:
        logger.error(f"Crossref Search Error: {e}")
        return None

def ai_diagnosis_ref(ref_text: str) -> Tuple[str, str, str, str]:
    """
    Use Qwen API to diagnose the reference type and extract title/URL if not found in databases.
    Returns: Tuple of (diagnosis_tag, extracted_title, extracted_url, search_query)
        diagnosis_tag: BOOK, CONF, PREPRINT, WEBSITE, PATENT, HIGH_RISK, UNKNOWN
        extracted_title: AI extracted article/chapter title (for non-WEBSITE types)
        extracted_url: AI extracted URL (for WEBSITE type)
        search_query: Optimized search query for Google Scholar
    Fallback: Regex for tag and URL extraction
    """
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        # Try regex URL extraction as fallback
        url_match = re.search(r'https?://[^\s<>"\']+', ref_text)
        extracted_url = url_match.group(0).rstrip('.,;:)') if url_match else ""
        return ("NO_API_KEY", "", extracted_url, "")

    # Enhanced prompt for diagnosis and detailed extraction
    prompt = f"""我是一名学术编辑。请分析这条参考文献：'{ref_text}'。
数据库无法检索到它。请完成以下任务：

**任务1 - 类型判断**：判断它属于以下哪种情况：
- BOOK: 书籍或书籍章节
- CONF: 会议论文、论文集
- PREPRINT: 预印本 (arXiv, bioRxiv等)
- WEBSITE: 网址、新闻、非学术资源
- PATENT: 专利
- HIGH_RISK: 看起来像期刊论文，但格式怪异、期刊名存疑，或疑似AI虚构/幻觉

**任务2 - 详细信息提取**：
- 如果是WEBSITE类型：提取URL网址
- 如果是BOOK类型（书籍章节）：分别提取章节名、书名、出版社(Publisher)
- 如果是其他类型：提取论文/文章题目
- 提取第一作者的姓氏（仅姓）
- 提取年份

**任务3 - 生成检索式**：
根据提取的信息，生成一个优化的谷歌学术检索式。规则：
- 对于BOOK类型：同时使用章节名、书名、作者、出版社、年份构建组合检索式
- 对于短标题（<5个词）：必须添加作者姓和年份到检索式中
- 用英文双引号包裹完整标题短语
- 示例格式："Chapter Title" "Book Title" AuthorLastName Publisher 2023

请按以下格式返回（不要解释）：
TYPE: [类型标签]
CHAPTER: [章节标题，仅BOOK类型填写，否则留空]
BOOK_TITLE: [书名，仅BOOK类型填写，否则留空]
PUBLISHER: [出版社，仅BOOK类型填写，否则留空]
TITLE: [论文/文章题目，非BOOK类型填写]
AUTHOR: [第一作者姓氏]
YEAR: [年份]
URL: [网址，仅WEBSITE类型填写]
SEARCH_QUERY: [优化的谷歌学术检索式]"""

    extracted_title = ""
    extracted_url = ""
    diagnosis_tag = "UNKNOWN"
    search_query = ""
    chapter_title = ""
    book_title = ""
    author = ""
    year = ""
    publisher = ""

    try:
        dashscope.api_key = api_key
        response = Generation.call(
            model='qwen-turbo',
            messages=[{'role': Role.USER, 'content': prompt}],
            result_format='message'
        )
        if response.status_code == 200:
            content = response.output.choices[0].message.content.strip()
            
            # Parse TYPE
            valid_tags = ["BOOK", "CONF", "PREPRINT", "WEBSITE", "PATENT", "HIGH_RISK"]
            type_match = re.search(r'TYPE:\s*([A-Z_]+)', content, re.IGNORECASE)
            if type_match:
                found_tag = type_match.group(1).upper()
                if found_tag in valid_tags:
                    diagnosis_tag = found_tag
            else:
                # Fallback: check if any valid tag appears in content
                for tag in valid_tags:
                    if tag in content.upper():
                        diagnosis_tag = tag
                        break
            
            # Parse CHAPTER (for BOOK type)
            chapter_match = re.search(r'CHAPTER:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if chapter_match:
                chapter_title = chapter_match.group(1).strip().strip('"').strip("'")
                if chapter_title.lower() in ['无', '空', 'none', 'n/a', '留空', '']:
                    chapter_title = ""
            
            # Parse BOOK_TITLE (for BOOK type)
            book_match = re.search(r'BOOK_TITLE:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if book_match:
                book_title = book_match.group(1).strip().strip('"').strip("'")
                if book_title.lower() in ['无', '空', 'none', 'n/a', '留空', '']:
                    book_title = ""

            # Parse PUBLISHER (for BOOK type)
            pub_match = re.search(r'PUBLISHER:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if pub_match:
                publisher = pub_match.group(1).strip().strip('"').strip("'")
                if publisher.lower() in ['无', '空', 'none', 'n/a', '留空', '']:
                    publisher = ""
            
            # Parse TITLE (for non-BOOK types)
            title_match = re.search(r'TITLE:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if title_match:
                extracted_title = title_match.group(1).strip()
                extracted_title = extracted_title.strip('"').strip("'").strip('【').strip('】')
                extracted_title = re.sub(r'[.。]+$', '', extracted_title)
                if extracted_title.lower() in ['无', '空', 'none', 'n/a', '留空', '']:
                    extracted_title = ""
            
            # Parse AUTHOR
            author_match = re.search(r'AUTHOR:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if author_match:
                author = author_match.group(1).strip().strip('"').strip("'")
                if author.lower() in ['无', '空', 'none', 'n/a', '留空', '']:
                    author = ""
            
            # Parse YEAR
            year_match = re.search(r'YEAR:\s*(\d{4})', content, re.IGNORECASE)
            if year_match:
                year = year_match.group(1)
            
            # Parse URL
            url_match = re.search(r'URL:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if url_match:
                extracted_url = url_match.group(1).strip()
                extracted_url = extracted_url.strip('"').strip("'").strip('<').strip('>')
                if extracted_url.lower() in ['无', '空', 'none', 'n/a', '留空', '']:
                    extracted_url = ""
            
            # Parse SEARCH_QUERY
            query_match = re.search(r'SEARCH_QUERY:\s*(.+?)(?:\n|$)', content, re.IGNORECASE)
            if query_match:
                search_query = query_match.group(1).strip()
                if search_query.lower() in ['无', '空', 'none', 'n/a', '留空', '']:
                    search_query = ""
            
            # For BOOK type: use chapter title as the main extracted_title if available
            if diagnosis_tag == "BOOK":
                if chapter_title:
                    extracted_title = chapter_title
                elif book_title:
                    extracted_title = book_title
            
            # Build search_query if AI didn't provide one or it's empty
            if not search_query:
                search_query = build_search_query(diagnosis_tag, extracted_title, chapter_title, book_title, publisher, author, year)
                
        else:
            logger.error(f"AI API Failed: {response.code} {response.message}")
    except Exception as e:
        logger.error(f"AI Call Exception: {e}")

    # Fallback Regex for diagnosis if AI failed
    if diagnosis_tag == "UNKNOWN":
        if re.search(r'\b(Ed\.|Eds\.|Pp\.|Vol\.|ISBN)\b', ref_text, re.IGNORECASE):
            diagnosis_tag = "BOOK"
        elif re.search(r'\b(Proc\.|Publisher|Conference|Symposium|Workshop)\b', ref_text, re.IGNORECASE):
            diagnosis_tag = "CONF"
    
    # Fallback: Try regex URL extraction from original text if WEBSITE but no URL extracted
    if diagnosis_tag == "WEBSITE" and not extracted_url:
        url_regex_match = re.search(r'https?://[^\s<>"\']+', ref_text)
        if url_regex_match:
            extracted_url = url_regex_match.group(0).rstrip('.,;:)')
    
    # Also try regex URL extraction if we have a URL in text but AI didn't tag as WEBSITE
    if not extracted_url:
        url_regex_match = re.search(r'https?://[^\s<>"\']+', ref_text)
        if url_regex_match:
            extracted_url = url_regex_match.group(0).rstrip('.,;:)')
    
    # Final fallback for search_query if still empty
    if not search_query and extracted_title:
        search_query = build_search_query(diagnosis_tag, extracted_title, "", "", "", author, year)
    
    return (diagnosis_tag, extracted_title, extracted_url, search_query)


def build_search_query(diagnosis_tag: str, title: str, chapter: str, book_title: str, publisher: str, author: str, year: str) -> str:
    """
    Build an optimized search query for Google Scholar based on extracted information.
    """
    parts = []
    
    if diagnosis_tag == "BOOK":
        # For books: combine chapter, book title, publisher
        if chapter:
            parts.append(f'"{chapter}"')
        
        if book_title:
            parts.append(f'"{book_title}"')
            
        if publisher:
             parts.append(publisher)

    else:
        # For other types: use the main title
        if title:
            parts.append(f'"{title}"')
    
    # Add author if title is short (less than 5 words) or always for BOOK type
    title_to_check = chapter if chapter else (book_title if book_title else title)
    if author and (len(title_to_check.split()) < 5 or diagnosis_tag == "BOOK"):
        parts.append(author)
    
    # Add year if title is short or for BOOK type
    if year and (len(title_to_check.split()) < 5 or diagnosis_tag == "BOOK"):
        parts.append(year)
    
    return " ".join(parts)


def query_nlm_ids_by_doi(doi: str, api_key: Optional[str]) -> Tuple[str, str]:
    """通过DOI在NLM (PubMed)上查询PMID和PMCID。
    
    Args:
        doi: 文献的DOI
        api_key: NCBI API密钥
        
    Returns:
        Tuple of (pmid, pmcid)
    """
    if not doi or not api_key:
        return "", ""
    
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    headers = {"User-Agent": USER_AGENT}
    pmid = ""
    pmcid = ""
    
    try:
        # Step 1: 使用 esearch 通过 DOI 查找 PMID
        search_params = {
            "db": "pubmed",
            "retmode": "json",
            "api_key": api_key,
            "term": f"{doi}[AID]"
        }
        response = requests.get(f"{base_url}esearch.fcgi", params=search_params, headers=headers)
        response.raise_for_status()
        search_data = response.json()
        id_list = search_data.get("esearchresult", {}).get("idlist", [])
        
        if not id_list:
            print(f"    NLM中未找到DOI: {doi}")
            return "", ""
        
        pmid = id_list[0]
        print(f"    NLM查询到PMID: {pmid}")
        
        # Step 2: 使用 esummary 获取 PMCID
        summary_params = {
            "db": "pubmed",
            "retmode": "json",
            "api_key": api_key,
            "id": pmid
        }
        response = requests.get(f"{base_url}esummary.fcgi", params=summary_params, headers=headers)
        response.raise_for_status()
        summary_data = response.json()
        
        result = summary_data.get("result", {})
        article_info = result.get(str(pmid), {})
        article_ids = article_info.get("articleids", [])
        
        for aid in article_ids:
            if aid.get("idtype") == "pmc":
                pmcid = aid.get("value", "")
                print(f"    NLM查询到PMCID: {pmcid}")
                break
        
        if not pmcid:
            print(f"    未找到PMCID")
            
    except requests.exceptions.RequestException as e:
        logger.error(f"NLM ID查询错误 (DOI: {doi}): {e}")
    
    return pmid, pmcid


def query_nlm_for_corrections(doi: str, api_key: Optional[str], pmid: str = "") -> Tuple[str, str]:
    """
    通过DOI或PMID在NLM (PubMed)上查询更正和撤稿信息，并获取其DOI。
    """
    if not api_key:
        return "", ""
    if not doi and not pmid:
        return "", ""
        
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    params = {"db": "pubmed", "retmode": "json", "api_key": api_key}
    headers = {"User-Agent": USER_AGENT}
    correction_doi, retraction_doi = "", ""
    
    try:
        # Step 1: 如果没有PMID，先用DOI换PMID (原文的)
        if not pmid and doi:
            search_params = params.copy()
            search_params["term"] = f"{doi}[AID]"
            response = requests.get(f"{base_url}esearch.fcgi", params=search_params, headers=headers)
            response.raise_for_status()
            search_data = response.json()
            id_list = search_data.get("esearchresult", {}).get("idlist", [])
            if not id_list:
                return "", ""
            pmid = id_list[0]
        
        if not pmid:
            return "", ""
            
        # Step 2: 直接查原文的 Summary，看有没有被撤稿/更正的记录
        summary_params = params.copy()
        summary_params["id"] = pmid
        response = requests.get(f"{base_url}esummary.fcgi", params=summary_params, headers=headers)
        response.raise_for_status()
        summary_data = response.json()
        
        # 收集需要查询DOI的 撤稿/更正 声明的PMID
        notice_pmids = {} # {pmid: "type"}
        
        if "result" in summary_data and str(pmid) in summary_data["result"]:
            doc_info = summary_data["result"][str(pmid)]
            
            # 检查 commentscorrections 字段 (这是正确的查询位置)
            if "commentscorrections" in doc_info:
                for ref in doc_info["commentscorrections"]:
                    ref_type = ref.get("reftype", "")
                    ref_pmid = str(ref.get("id", ""))
                    
                    if ref_type == "RetractionIn":
                        notice_pmids[ref_pmid] = "retraction"
                    elif ref_type == "ErratumIn":
                        notice_pmids[ref_pmid] = "correction"

        # Step 3: 如果发现了撤稿/更正的PMID，额外查一次以获取它们的DOI
        if notice_pmids:
            # 批量查询这些声明的详细信息
            notice_summary_params = params.copy()
            notice_summary_params["id"] = ",".join(notice_pmids.keys())
            response = requests.get(f"{base_url}esummary.fcgi", params=notice_summary_params, headers=headers)
            response.raise_for_status()
            notice_data = response.json()
            result = notice_data.get("result", {})
            
            for notice_pmid, type_ in notice_pmids.items():
                info = result.get(notice_pmid, {})
                article_ids = info.get("articleids", [])
                
                # 提取DOI
                found_doi = ""
                for aid in article_ids:
                    if aid.get("idtype") == "doi":
                        found_doi = aid.get("value", "")
                        break
                
                # 如果没找到DOI，降级使用 PMID 格式
                if not found_doi:
                    found_doi = f"PMID:{notice_pmid}"
                
                # 赋值并打印
                if type_ == "retraction":
                    retraction_doi = found_doi
                    print(f"    NLM发现撤稿: {retraction_doi}")
                elif type_ == "correction":
                    correction_doi = found_doi
                    print(f"    NLM发现更正: {correction_doi}")

    except requests.exceptions.RequestException as e:
        logger.error(f"NLM查询错误 (DOI: {doi}): {e}")
        
    return correction_doi, retraction_doi


def update_author_count(authors: List[Author], all_authors_count: dict):
    """更新作者出现次数统计"""
    for author in authors:
        author_name = author.format_name()
        if author_name:
            all_authors_count[author_name] = all_authors_count.get(author_name, 0) + 1


def update_doi_count(doi: str, all_doi_count: dict):
    """更新DOI出现次数统计"""
    if doi:
        all_doi_count[doi] = all_doi_count.get(doi, 0) + 1


def process_single_reference_new(ref: str, idx: int, total_refs: int, all_authors_count: dict, all_doi_count: dict) -> dict:
    """
    Strict DOI Priority Reference Processing
    """
    log_msg = f"Processing {idx}/{total_refs}: {ref[:50]}..."
    logger.info(log_msg)
    print(f"\nProcessing {idx}/{total_refs}: {ref[:80]}...")

    # Data to collect
    extracted_doi = extract_doi_from_text(ref)
    api_doi = ""
    match_status = None
    has_retraction = False
    has_correction = False
    title = ""
    journal = ""
    year = ""
    all_authors = []
    is_recent_5 = False
    is_recent_3 = False
    ai_diag = ""
    ai_extracted_title = ""  # AI extracted title for Scholar search
    ai_extracted_url = ""    # AI extracted URL for WEBSITE type
    ai_search_query = ""     # AI generated optimized search query
    matched_ref_str = ""
    crossref_data = None
    pmid = ""    # PubMed ID
    pmcid = ""   # PubMed Central ID
    
    # Calculate cleaned ref for global duplicate checking
    cleaned_original_ref = re.sub(r'^\d+\.?\s*|https?://\S+', '', ref).lower()
    cleaned_original_ref = re.sub(r'[^\w\s]', '', cleaned_original_ref)
    cleaned_original_ref = re.sub(r'\s+', ' ', cleaned_original_ref).strip()

    
    # 1. DOI Priority Lookup
    if extracted_doi:
        print(f"    [1] Extracted DOI: {extracted_doi}. Querying API...")
        crossref_data = query_crossref_by_doi(extracted_doi)
        
        if crossref_data:
            print("    -> DOI Found in API.")
            api_doi = crossref_data.doi
            
            # Reconstruct APA for Verification
            matched_ref_str = format_reference_apa(crossref_data)
            
            # Fuzzy match verification
            # Compare original text vs Reconstructed APA
            similarity = fuzz.token_sort_ratio(ref, matched_ref_str)
            print(f"    -> Similarity Check (Original vs API Ref): {similarity}%")
            
            if similarity >= 60: # Threshold can be tuned, 60-70 reasonable for citation variations
                match_status = "match"
            else:
                match_status = "doi_mismatch"
                print(f"    -> WARNING: DOI Mismatch (Similarity {similarity} < 60)")
        else:
             print("    -> DOI Not Found in API.")
    
    # 2. Text Search Fallback (Only if no DOI data found yet)
    if not crossref_data:
        print("    [2] DOI Lookup Failed/Empty. Trying Text Search...")
        crossref_data = query_crossref_search(ref)
        if crossref_data:
             print("    -> Search Result found.")
             matched_ref_str = format_reference_apa(crossref_data)
             similarity = fuzz.token_sort_ratio(ref, matched_ref_str)
             print(f"    -> Similarity: {similarity}%")
             
             if similarity >= 60:
                 match_status = "match"
                 api_doi = crossref_data.doi
             else:
                 # If search result is low similarity, it's NOT a match.
                 # We discard this as a false positive from search, or handle as unmatched.
                 print(f"    -> Low similarity search result. Discarding.")
                 crossref_data = None # Reset
    
    # 3. Process matched data
    if crossref_data:
        # Populate basic info
        title = crossref_data.title
        journal = crossref_data.journal_full_title
        if crossref_data.year:
            year = str(crossref_data.year)
            current_year = datetime.datetime.now().year
            if current_year - int(year) <= 5: is_recent_5 = True
            if current_year - int(year) <= 3: is_recent_3 = True

        all_authors = crossref_data.all_authors
        has_retraction = crossref_data.has_retraction
        has_correction = crossref_data.has_correction
        
        # Check NLM for PMID, PMCID and additional retraction/correction info if DOI exists
        if crossref_data.doi and NCBI_API_KEY:
             # 查询 PMID 和 PMCID
             pmid, pmcid = query_nlm_ids_by_doi(crossref_data.doi, NCBI_API_KEY)
             # 使用已获取的PMID查询更正和撤稿信息（避免重复调用esearch）
             corr, retr = query_nlm_for_corrections(crossref_data.doi, NCBI_API_KEY, pmid)
             if retr: has_retraction = True
             if corr: has_correction = True

        # Update global counters
        if api_doi:
             update_doi_count(api_doi, all_doi_count)
        # Update global author count using cleaned all_authors list
        for author_name in all_authors:
            if author_name:
                all_authors_count[author_name] = all_authors_count.get(author_name, 0) + 1

    else:
        # 4. No match found -> Fallback to Regex Authors and AI Diagnosis
        print("    [3] No match found. Running Fallbacks...")
        all_authors = extract_authors_regex(ref)
        # Update authors from regex (Optional: might be noisy, but requested to collect all)
        # We need to construct simplified Author objects to use update_author_count if we want generic global counting
        # For now, we just adding to all_authors list in result
        
        # AI Diagnosis - now returns (tag, extracted_title, extracted_url, search_query)
        ai_diag, ai_extracted_title, ai_extracted_url, ai_search_query = ai_diagnosis_ref(ref)
        print(f"    -> AI Diagnosis: {ai_diag}")
        if ai_extracted_title:
            print(f"    -> AI Extracted Title: {ai_extracted_title}")
            title = ai_extracted_title  # Use AI extracted title if Crossref didn't provide one
        if ai_extracted_url:
            print(f"    -> AI Extracted URL: {ai_extracted_url}")
        if ai_search_query:
            print(f"    -> AI Search Query: {ai_search_query}")

    # Construct Result Dict
    result_dict = {
        "original_text": ref,
        "extracted_doi": extracted_doi if extracted_doi else "",
        "api_doi": api_doi,
        "match_status": match_status if match_status else "None",
        "has_retraction": has_retraction,
        "has_correction": has_correction, # Added missing field
        "title": title,
        "journal": journal,
        "year": year,
        "all_authors": all_authors,
        "pmid": pmid,
        "pmcid": pmcid,
        "is_recent_5_years": is_recent_5,
        "is_recent_3_years": is_recent_3,
        "ai_diagnosis": ai_diag,
        "ai_extracted_title": ai_extracted_title,  # AI extracted title for Scholar search
        "ai_extracted_url": ai_extracted_url,      # AI extracted URL for WEBSITE type
        "ai_search_query": ai_search_query,        # AI optimized search query
        "cleaned_original_ref": cleaned_original_ref,
        # Legacy/UI compatibility fields (Optional, if UI needs them)
        "matched_ref": matched_ref_str if matched_ref_str else "Not Found",
        "similarity": 0, # Placeholder or calculated above
    }
    
    return result_dict

def find_fuzzy_duplicates(results: List[dict]) -> Tuple[Dict[int, str], int]:
    """
    对所有参考文献进行模糊匹配查重。

    Args:
        results: 包含所有文献处理结果的列表。

    Returns:
        一个元组 (duplicate_info, pair_count)，
        duplicate_info 是一个字典，键是结果索引，值是重复信息的字符串。
        pair_count 是发现的重复对数。
    """
    n = len(results)
    if n < 2:
        return {}, 0

    print("\n正在进行模糊查重...")
    duplicate_info = {}
    processed_indices = set() # 记录已标记为重复的项，避免重复报告
    pair_count = 0

    for i in range(n):
        if i in processed_indices:
            continue
        
        # 寻找与第i条文献重复的所有文献
        duplicates_for_i = []
        for j in range(i + 1, n):
            if j in processed_indices:
                continue

            ref1_cleaned = results[i].get('cleaned_original_ref', '')
            ref2_cleaned = results[j].get('cleaned_original_ref', '')
            
            # 如果清洗后的字符串太短，则跳过，避免误判
            if len(ref1_cleaned) < 20 or len(ref2_cleaned) < 20:
                continue

            similarity = fuzz.token_sort_ratio(ref1_cleaned, ref2_cleaned)

            if similarity > 70:
                # 发现一对重复项
                if not duplicates_for_i: # 这是i的第一个重复项
                    pair_count += 1
                
                duplicates_for_i.append(j + 1) # j+1 是实际的条目编号
                processed_indices.add(j)

        # 如果找到了与i重复的项，则记录下来
        if duplicates_for_i:
            # 格式化重复信息
            i_info = f"与ref. {', '.join(map(str, duplicates_for_i))} 重复"
            duplicate_info[i] = i_info
            
            for dup_index in duplicates_for_i:
                # k 是实际条目编号, k-1是列表索引
                k = dup_index
                other_items = [i+1] + [d for d in duplicates_for_i if d != k]
                k_info = f"与ref. {', '.join(map(str, other_items))} 重复"
                duplicate_info[k - 1] = k_info

            processed_indices.add(i)
    
    print(f"模糊查重完成，发现 {pair_count} 对重复项。")
    return duplicate_info, pair_count


def calculate_statistics(results: List[dict], total_refs: int, fuzzy_duplicate_pairs: int) -> dict:
    """计算参考文献的统计信息 - 兼容新版 process_single_reference_new 输出"""
    stats = {
        "total_references": total_refs, "recent_5_years": 0, "recent_3_years": 0,
        "with_doi": 0, "without_doi": 0, "duplicate_refs": 0, "matched_refs": 0,
        "correction_count": 0, "retraction_count": 0,
        "fuzzy_duplicate_pairs": fuzzy_duplicate_pairs,
        "doi_mismatch_count": 0,  # 新增: DOI不匹配计数
    }

    doi_seen = {}  # 用于检测重复 DOI

    for result in results:
        # 近年统计 - 使用新字段 is_recent_5_years / is_recent_3_years
        if result.get('is_recent_5_years', False):
            stats["recent_5_years"] += 1
        if result.get('is_recent_3_years', False):
            stats["recent_3_years"] += 1
        
        # DOI统计 - 新字段是 api_doi
        api_doi = result.get('api_doi', '') or result.get('doi', '')
        if api_doi:
            stats["with_doi"] += 1
            # 检测重复 DOI
            if api_doi in doi_seen:
                stats["duplicate_refs"] += 1
            doi_seen[api_doi] = True
        else:
            stats["without_doi"] += 1
        
        # 匹配状态统计
        match_status = result.get('match_status', '')
        if match_status == "match":
            stats["matched_refs"] += 1
        elif match_status == "doi_mismatch":
            stats["doi_mismatch_count"] += 1

        # 撤稿/更正统计 - 新版是布尔值，旧版是 "是" 字符串
        has_retraction = result.get('has_retraction', False)
        if has_retraction is True or has_retraction == "是":
            stats["retraction_count"] += 1
        
        has_correction = result.get('has_correction', False)
        if has_correction is True or has_correction == "是":
            stats["correction_count"] += 1

    # 计算百分比
    if total_refs > 0:
        stats["matched_refs_pct"] = float(stats["matched_refs"] / total_refs * 100)
        stats["duplicate_refs_pct"] = float(stats["duplicate_refs"] / total_refs * 100)
        stats["recent_5_years_pct"] = float(stats["recent_5_years"] / total_refs * 100)
        stats["recent_3_years_pct"] = float(stats["recent_3_years"] / total_refs * 100)
        stats["correction_pct"] = float(stats["correction_count"] / total_refs * 100)
        stats["retraction_pct"] = float(stats["retraction_count"] / total_refs * 100)
        stats["with_doi_pct"] = float(stats["with_doi"] / total_refs * 100)
        stats["without_doi_pct"] = float(stats["without_doi"] / total_refs * 100)
    
    return stats


def process_file(input_file: str, status_callback: Callable[[str], None] = None) -> str:
    """Main processing function to be called by GUI"""
    if status_callback:
        status_callback("正在读取文件...")
    
    try:
        input_dir = os.path.dirname(input_file)
        base_name = os.path.splitext(os.path.basename(input_file))[0]
        output_json_file = os.path.join(input_dir, f"{base_name}_cache.json")
        
        if not NCBI_API_KEY:
            print("警告: 未找到 NCBI_API_KEY 环境变量。NLM的更正/撤稿查询功能将被跳过。")

        # 读取文件
        try:
            file_ext = os.path.splitext(input_file)[1].lower()
            refs = []
            if file_ext == '.csv':
                try:
                    df = pd.read_csv(input_file, header=None, encoding='utf-8')
                except UnicodeDecodeError:
                    encodings = ['gbk', 'gb2312', 'gb18030', 'latin1', 'cp1252']
                    for encoding in encodings:
                        try:
                            df = pd.read_csv(input_file, header=None, encoding=encoding)
                            break
                        except UnicodeDecodeError:
                            continue
                    if df is None:
                        raise Exception("无法使用任何编码格式读取文件，请检查文件编码")
                
                if df.shape[1] > 1:
                    other_columns_data = df.iloc[:, 1:].notna().any().any()
                    if other_columns_data:
                        print("警告: CSV文件除了第一列外，其他列也包含数据。程序将只使用第一列作为参考文献。")
                
                refs = df.iloc[:, 0].tolist()
                refs = [r for r in refs if isinstance(r, str) and r.strip() != ""]
                
            elif file_ext in ['.xlsx', '.xls']:
                df = pd.read_excel(input_file, header=None)
                
                if df.shape[1] > 1:
                    other_columns_data = df.iloc[:, 1:].notna().any().any()
                    if other_columns_data:
                        print("警告: Excel文件除了第一列外，其他列也包含数据。程序将只使用第一列作为参考文献。")
                
                refs = df.iloc[:, 0].tolist()
                refs = [r for r in refs if isinstance(r, str) and r.strip() != ""]
            else:
                raise ValueError("不支持的文件格式，请选择 .csv 或 .xlsx/.xls 文件。")
                
        except Exception as e:
            raise Exception(f"读取文件错误: {e}")

        total_refs = len(refs)
        logger.info(f"读取 {total_refs} 条参考文献")
        print(f"读取到 {total_refs} 条参考文献")

        all_authors_count = {}
        all_doi_count = {}

        # 增量缓存
        results = []
        start_idx = 1
        if os.path.exists(output_json_file):
            try:
                with open(output_json_file, 'r', encoding='utf-8') as f:
                    cached_data = json.load(f)
                cached_results = cached_data.get('results', [])
                if cached_results:
                    results = cached_results
                    start_idx = len(results) + 1
                    print(f"📂 发现已有缓存，已处理 {len(results)} 条，从第 {start_idx} 条继续...")
                    # 重建作者和DOI计数器
                    for res in results:
                        for author_name in res.get('all_authors', []):
                            if author_name:
                                all_authors_count[author_name] = all_authors_count.get(author_name, 0) + 1
                        api_doi = res.get('api_doi', '')
                        if api_doi:
                            all_doi_count[api_doi] = all_doi_count.get(api_doi, 0) + 1
            except Exception as e:
                print(f"⚠ 缓存读取失败，将从头开始: {e}")

        if status_callback:
            status_callback(f"开始处理... 总计 {total_refs} 条")

        print("正在处理参考文献...")
        for idx, ref in enumerate(refs, 1):
            if idx < start_idx:
                continue
                
            if idx > 1 and idx % 100 == 0:
                print(f"\n⏸ 已处理 {idx} 条，休息60秒防止API被限速...")
                logger.info(f"处理到第 {idx} 条，暂停60秒")
                time.sleep(60)
            
            if status_callback:
                status_callback(f"正在处理 {idx}/{total_refs}...")
            
            result = process_single_reference_new(ref, idx, total_refs, all_authors_count, all_doi_count)
            results.append(result)
            
            try:
                temp_output = {
                    "statistics": {"total_references": total_refs, "processed": len(results)},
                    "results": results
                }
                with open(output_json_file, 'w', encoding='utf-8') as f:
                    json.dump(temp_output, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"⚠ 缓存保存失败: {e}")
            
            time.sleep(1 + random.uniform(0, 0.5))

        print(f"\n处理完成，共发现 {len(all_authors_count)} 位作者，{len(all_doi_count)} 个DOI")

        duplicate_info, fuzzy_duplicate_pairs = find_fuzzy_duplicates(results)
        if duplicate_info:
            for index, info_str in duplicate_info.items():
                results[index]['fuzzy_duplicates'] = info_str

        print("\n计算统计信息...")
        stats = calculate_statistics(results, total_refs, fuzzy_duplicate_pairs)

        print("\n参考文献统计报告:")
        print("=" * 50)
        print(f"总参考文献数: {stats['total_references']}")
        print(f"近五年发表篇数: {stats['recent_5_years']} ({stats.get('recent_5_years_pct', 0):.2f}%)")
        print(f"近三年发表篇数: {stats['recent_3_years']} ({stats.get('recent_3_years_pct', 0):.2f}%)")
        print(f"有DOI的篇数: {stats['with_doi']} ({stats.get('with_doi_pct', 0):.2f}%)")
        print(f"更正/撤稿篇数: {stats['correction_count']}/{stats['retraction_count']}")
        print(f"DOI完全重复篇数: {stats['duplicate_refs']}")
        print(f"模糊匹配重复对数: {stats['fuzzy_duplicate_pairs']}")
        print(f"匹配成功篇数: {stats['matched_refs']} ({stats.get('matched_refs_pct', 0):.2f}%)")

        for res in results:
            res.setdefault('fuzzy_duplicates', '')
            res.pop('cleaned_original_ref', None)

        output_data = {
            "statistics": stats,
            "results": results
        }

        with open(output_json_file, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        
        print(f"处理完成！结果已保存至：{output_json_file}")
        if status_callback:
            status_callback(f"处理完成！结果保存为 {output_json_file}")
            
        return output_json_file

    except Exception as e:
        print(f"处理过程中发生错误: {e}")
        if status_callback:
            status_callback(f"处理失败: {e}")
        raise e
