"""
scraper.py - 核心爬虫逻辑
使用 DuckDuckGo 搜索 URL，然后用 trafilatura 提取全文
"""

import requests
import time
import random
import logging
import os
import re
import hashlib
from urllib.parse import urljoin, urlparse, urldefrag
import xml.etree.ElementTree as ET
from datetime import datetime
from bs4 import BeautifulSoup

try:
    import trafilatura
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

try:
    from ddgs import DDGS
    HAS_DDGS = True
except ImportError:
    HAS_DDGS = False

logger = logging.getLogger(__name__)

PROXY_URL = os.environ.get("SCRAPER_PROXY", "").strip()
PROXIES_DICT = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

# 1. 移除了 br 压缩，防止二进制乱码
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
}

# ⭐️ 优化点：使用 Session 保持连接池，大幅提升连续抓取速度，降低被封概率
session = requests.Session()
session.headers.update(HEADERS)
if PROXIES_DICT:
    session.proxies.update(PROXIES_DICT)

DEFAULT_RAW_DIR = os.environ.get(
    "SCRAPER_RAW_DIR",
    os.path.join(os.path.dirname(__file__), "data", "raw"),
)

SITE_SELECTORS = {
    'zhihu.com':      '.Post-RichTextContainer, .RichText, .AnswerItem .RichText',
    'tieba.baidu.com': '.p_content, .d_post_content',
    'v2ex.com':       '.topic_content, .reply_content',
    'sspai.com':      '.article-body, .content',
    '36kr.com':       '.articleDetailContent, .content-detail',
    'huxiu.com':      '.article-content-wrap',
    'hupu.com':       '.bbs-content',
    'mydigit.cn':     '.t_msgfont, .postmessage',
}

def _safe_mkdir(path: str):
    os.makedirs(path, exist_ok=True)

def _sha1(s: str) -> str:
    return hashlib.sha1((s or "").encode("utf-8"), usedforsecurity=False).hexdigest()

def _normalize_url(base_url: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith(("javascript:", "mailto:", "tel:")):
        return None
    abs_url = urljoin(base_url, href)
    abs_url, _frag = urldefrag(abs_url)
    parsed = urlparse(abs_url)
    if parsed.scheme not in ("http", "https"):
        return None
    return abs_url

# 2. 禁用了 DDG 时间限制，解决中文站点搜不出结果的问题
def get_ddg_timelimit(start_date: datetime, end_date: datetime) -> str:
    return None

def search_ddg(query: str, timelimit: str = None, max_results: int = 20) -> list:
    if not HAS_DDGS:
        raise ImportError("请先安装 ddgs：pip install ddgs")
    results = []
    try:
        # ⭐️ 优化点：修复了 proxies 报错问题，最新版库要求使用单数 proxy
        with DDGS(proxy=PROXY_URL or None) as ddgs:
            for r in ddgs.text(query, timelimit=timelimit, max_results=max_results):
                results.append(r)
                if len(results) >= max_results:
                    break
    except Exception as e:
        logger.warning(f"DDG 搜索出错 [{query}]: {e}")
    return results

# 3. 增加了 Content-Type 过滤，防止爬到图片崩溃
def fetch_page(url: str, timeout: int = 20) -> dict | None:
    for attempt in range(3):
        try:
            # ⭐️ 优化点：改用全局 session 获取数据
            resp = session.get(url, timeout=timeout)
            resp.raise_for_status()
            
            content_type = resp.headers.get('Content-Type', '').lower()
            allowed_types = ['text/html', 'text/plain', 'text/xml', 'application/xml', 'application/xhtml']
            if not any(t in content_type for t in allowed_types):
                logger.warning(f"跳过非网页内容 ({content_type}): {url}")
                return None

            if not resp.encoding or resp.encoding.lower() == 'iso-8859-1':
                resp.encoding = resp.apparent_encoding
                
            return {
                "url": url,
                "final_url": str(resp.url),
                "status_code": resp.status_code,
                "html": resp.text,
                "headers": dict(resp.headers),
            }
        except Exception as e:
            if attempt == 2:
                logger.warning(f"抓取失败 {url}: {e}")
                return None
            time.sleep(random.uniform(1, 2))
    return None

def _fetch_text(url: str, timeout: int = 20) -> str | None:
    page = fetch_page(url, timeout=timeout)
    if not page:
        return None
    return page.get("html") or None

def discover_from_sitemap(site: str, limit: int = 200) -> list[str]:
    host = site.replace("https://", "").replace("http://", "").rstrip("/")
    base = f"https://{host}"
    candidates = [
        f"{base}/sitemap.xml",
        f"{base}/sitemap_index.xml",
        f"{base}/sitemap-index.xml",
        f"{base}/sitemap1.xml",
    ]
    urls = []

    def parse_sitemap(xml_text: str) -> list[str]:
        out = []
        try:
            root = ET.fromstring(xml_text)
        except Exception:
            return out
        def strip_ns(tag: str) -> str:
            return tag.split("}", 1)[-1] if "}" in tag else tag
        tag = strip_ns(root.tag).lower()
        if tag.endswith("sitemapindex"):
            for sm in root.findall(".//{*}sitemap/{*}loc"):
                if sm.text:
                    out.append(sm.text.strip())
        elif tag.endswith("urlset"):
            for loc in root.findall(".//{*}url/{*}loc"):
                if loc.text:
                    out.append(loc.text.strip())
        return out

    for sm_url in candidates:
        xml_text = _fetch_text(sm_url, timeout=20)
        if not xml_text:
            continue
        first = parse_sitemap(xml_text)
        if not first:
            continue
        if first and any(u.endswith(".xml") for u in first[:5]):
            for child in first[:20]:
                child_xml = _fetch_text(child, timeout=20)
                if not child_xml:
                    continue
                child_urls = parse_sitemap(child_xml)
                for u in child_urls:
                    if u.startswith("http"):
                        urls.append(u)
                        if len(urls) >= limit:
                            return list(dict.fromkeys(urls))
        else:
            for u in first:
                if u.startswith("http"):
                    urls.append(u)
                    if len(urls) >= limit:
                        return list(dict.fromkeys(urls))
        if urls:
            break
    return list(dict.fromkeys(urls))[:limit]

def discover_from_rss(site: str, limit: int = 100) -> list[str]:
    host = site.replace("https://", "").replace("http://", "").rstrip("/")
    base = f"https://{host}"
    feeds = [
        f"{base}/feed", f"{base}/feed/", f"{base}/rss",
        f"{base}/rss/", f"{base}/atom.xml", f"{base}/rss.xml", f"{base}/feed.xml",
    ]
    urls = []
    for feed_url in feeds:
        xml_text = _fetch_text(feed_url, timeout=20)
        if not xml_text:
            continue
        soup = BeautifulSoup(xml_text, "xml")
        for item in soup.find_all(["item", "entry"]):
            link = item.find("link")
            href = ""
            if link:
                href = link.get("href") or (link.get_text(strip=True) if link.get_text() else "")
            if href:
                href = href.strip()
                if href.startswith("http"):
                    urls.append(href)
            if len(urls) >= limit:
                break
        if urls:
            break
    return list(dict.fromkeys(urls))[:limit]

def _keyword_match(text: str, keywords: list[str]) -> bool:
    if not keywords:
        return True
    t = (text or "").lower()
    return any((k or "").strip().lower() in t for k in keywords if (k or "").strip())

def extract_content(html: str, url: str = '') -> str:
    if HAS_TRAFILATURA:
        content = trafilatura.extract(
            html, include_comments=False, include_tables=True, no_fallback=False, url=url
        )
        if content and len(content) > 150:
            return content.strip()
    soup = BeautifulSoup(html, 'lxml')
    for domain, selector in SITE_SELECTORS.items():
        if domain in url:
            elems = soup.select(selector)
            if elems:
                text = '\n'.join(e.get_text(separator='\n', strip=True) for e in elems)
                if len(text) > 100:
                    return text
    for selector in ['article', 'main', '.content', '#content', '.post-content',
                     '.article-content', '.entry-content', '[role="main"]']:
        elem = soup.select_one(selector)
        if elem:
            text = elem.get_text(separator='\n', strip=True)
            if len(text) > 200:
                return text
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'iframe']):
        tag.decompose()
    body = soup.find('body')
    if body:
        lines = [l.strip() for l in body.get_text(separator='\n').split('\n') if len(l.strip()) > 15]
        return '\n'.join(lines[:300])
    return ''

def extract_publish_date(html: str) -> str:
    if not html:
        return ''
    soup = BeautifulSoup(html, 'lxml')
    for prop in ['article:published_time', 'og:published_time', 'datePublished', 'pubdate']:
        tag = (soup.find('meta', property=prop) or
               soup.find('meta', attrs={'name': prop}) or
               soup.find('meta', itemprop=prop))
        if tag and tag.get('content'):
            return tag['content'][:10]
    time_tag = soup.find('time')
    if time_tag:
        dt = time_tag.get('datetime', '') or time_tag.get_text(strip=True)
        return dt[:10] if dt else ''
    return ''

def extract_meta_fields(html: str) -> dict:
    if not html:
        return {}
    soup = BeautifulSoup(html, "lxml")
    def meta_content(*, name=None, prop=None):
        tag = None
        if prop:
            tag = soup.find("meta", property=prop)
        if not tag and name:
            tag = soup.find("meta", attrs={"name": name})
        if tag and tag.get("content"):
            return tag["content"].strip()
        return ""
    title = (meta_content(prop="og:title") or meta_content(name="twitter:title") or 
             (soup.title.get_text(strip=True) if soup.title else ""))
    description = (meta_content(prop="og:description") or meta_content(name="description") or 
                   meta_content(name="twitter:description"))
    author = (meta_content(name="author") or meta_content(prop="article:author") or 
              meta_content(name="byline"))
    keywords = meta_content(name="keywords")
    og_image = meta_content(prop="og:image") or meta_content(name="twitter:image")
    canonical = ""
    link = soup.find("link", rel=lambda x: x and "canonical" in x)
    if link and link.get("href"):
        canonical = link["href"].strip()
    jsonld_texts = []
    for sc in soup.find_all("script", attrs={"type": re.compile(r"application/ld\+json", re.I)}):
        if sc.string and len(sc.string) < 200_000:
            jsonld_texts.append(sc.string)
    jsonld_blob = "\n".join(jsonld_texts)
    return {
        "meta_title": title, "meta_description": description, "meta_author": author,
        "meta_keywords": keywords, "meta_og_image": og_image, "canonical_url": canonical,
        "jsonld": jsonld_blob[:200_000] if jsonld_blob else "",
    }

def extract_images(html: str, base_url: str) -> list[str]:
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    urls = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
        u = _normalize_url(base_url, src)
        if u:
            urls.append(u)
    seen = set()
    out = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out

def discover_links(html: str, base_url: str, allow_domains: list[str] | None = None) -> list[str]:
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    links = []
    for a in soup.find_all("a"):
        href = a.get("href") or ""
        u = _normalize_url(base_url, href)
        if not u:
            continue
        if allow_domains:
            host = urlparse(u).netloc.lower()
            if not any(d in host for d in allow_domains):
                continue
        links.append(u)
    seen = set()
    out = []
    for u in links:
        if u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out

def archive_raw_html(*, task_id: str | None, url: str, html: str, raw_dir: str = DEFAULT_RAW_DIR) -> str | None:
    if not html:
        return None
    tid = task_id or "adhoc"
    folder = os.path.join(raw_dir, tid)
    _safe_mkdir(folder)
    name = f"{_sha1(url)}.html"
    path = os.path.join(folder, name)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        return os.path.relpath(path, os.path.join(os.path.dirname(__file__), "data"))
    except Exception as e:
        logger.warning(f"原始HTML归档失败 {url}: {e}")
        return None

def scrape(keywords: list, sites: list, start_date: datetime, end_date: datetime,
           max_per_site: int = 20, progress_callback=None, stop_event=None,
           seed_urls: list[str] | None = None, task_id: str | None = None) -> list:
    def log(msg):
        if progress_callback:
            progress_callback(msg)
        logger.info(msg)

    keyword_str = ' '.join(keywords) if isinstance(keywords, list) else keywords
    timelimit = get_ddg_timelimit(start_date, end_date)
    search_hits = []

    seed_urls = seed_urls or []
    discovered_urls = []
    
    if seed_urls:
        log(f'🌱 处理种子URL {len(seed_urls)} 个...')
        allow_domains = []
        if sites:
            for s in sites:
                s = (s or "").strip()
                if s:
                    # ⭐️ 优化点：防呆设计，自动为域名补全 .com 
                    clean_s = s.replace("https://", "").replace("http://", "").rstrip("/")
                    if "." not in clean_s:
                        clean_s += ".com"
                    allow_domains.append(clean_s)
                    
        for su in seed_urls:
            if stop_event and stop_event.is_set():
                log('⏹ 任务已手动停止')
                break
            su = (su or "").strip()
            if not su:
                continue
            discovered_urls.append({
                "href": su, "title": "Seed URL", "body": "",
                "_site": urlparse(su).netloc, "_seed": su,
            })
            log(f'   → 抓取种子页: {su}')
            page = fetch_page(su, timeout=30)
            if not page or not page.get("html"):
                continue
            html = page["html"]
            final_url = page.get("final_url") or su
            links = discover_links(html, final_url, allow_domains=allow_domains or None)
            pruned = []
            for u in links:
                if "toutiao.com" in u and ("/article/" in u or "/w/" in u):
                    pruned.append(u)
                elif "ednchina.com" in u and any(p in u for p in ("/news/", "/article/", "/content/", "/topic/")):
                    pruned.append(u)
            if pruned:
                links = pruned
            for u in links[: max_per_site * 2]:
                discovered_urls.append({
                    "href": u, "title": "Seed discovered", "body": "",
                    "_site": urlparse(u).netloc, "_seed": su,
                })
            log(f'   → 发现链接 {len(links)} 个')
            time.sleep(random.uniform(0.8, 1.6))

    if sites:
        for site in sites:
            site = site.strip()
            if not site:
                continue
            clean_site = site.replace('https://', '').replace('http://', '').rstrip('/')
            
            # ⭐️ 优化点：核心防呆，如果你输入 zhihu，自动变成 zhihu.com
            if "." not in clean_site:
                clean_site += ".com"
                
            query = f'{keyword_str} site:{clean_site}'
            log(f'🔍 搜索 {clean_site}: "{keyword_str}"')
            try:
                hits = search_ddg(query, timelimit=timelimit, max_results=max_per_site)
                if not hits:
                    log(f'   → 搜索为空，尝试从 sitemap/rss 发现链接...')
                    sm_urls = discover_from_sitemap(clean_site, limit=max(200, max_per_site * 50))
                    rss_urls = discover_from_rss(clean_site, limit=max(50, max_per_site * 20))
                    cand = (rss_urls + sm_urls)[: max(200, max_per_site * 50)]
                    for u in cand:
                        search_hits.append({
                            "href": u, "title": "Sitemap/RSS discovered", "body": "",
                            "_site": clean_site, "_seed": "",
                        })
                    log(f'   → sitemap/rss 发现 {len(cand)} 个候选')
                for h in hits:
                    h['_site'] = clean_site
                search_hits.extend(hits)
                log(f'   → 找到 {len(hits)} 个结果')
            except Exception as e:
                log(f'   → 搜索失败: {e}')
            time.sleep(random.uniform(1.5, 2.5))
    else:
        log(f'🔍 全网搜索: "{keyword_str}"')
        try:
            hits = search_ddg(keyword_str, timelimit=timelimit, max_results=max_per_site)
            for h in hits:
                h['_site'] = '全网'
            search_hits.extend(hits)
            log(f'   → 找到 {len(hits)} 个结果')
        except Exception as e:
            log(f'   → 搜索失败: {e}')

    search_hits = discovered_urls + search_hits

    if not search_hits:
        log('未找到任何结果，请检查关键词或网络连接')
        return []

    log(f'📥 开始抓取 {len(search_hits)} 篇内容...')
    results = []
    seen_url = set()
    matched_any = False

    for i, hit in enumerate(search_hits):
        if stop_event and stop_event.is_set():
            log('⏹ 任务已手动停止')
            break

        url = hit.get('href', '')
        title = hit.get('title', '无标题')
        snippet = hit.get('body', '')

        if not url:
            continue
        if url in seen_url:
            continue
        seen_url.add(url)

        log(f'[{i + 1}/{len(search_hits)}] {title[:50]}')

        page = fetch_page(url)
        content = ''
        publish_date = ''
        raw_html_relpath = None
        meta = {}
        images = []
        final_url = ''

        if page and page.get("html"):
            html = page["html"]
            final_url = page.get("final_url") or url
            raw_html_relpath = archive_raw_html(task_id=task_id, url=final_url, html=html)
            content = extract_content(html, final_url)
            publish_date = extract_publish_date(html)
            meta = extract_meta_fields(html)
            images = extract_images(html, final_url)
            
            # 4. 修复了标题替换问题（严格对齐版）
            if meta.get("meta_title"):
                title = meta.get("meta_title")

        if not content:
            content = snippet

        haystack = " ".join([
            title or "",
            (meta.get("meta_title") or ""),
            (meta.get("meta_description") or ""),
            snippet or "",
            (content or "")[:2000],
            (final_url or url),
        ])
        is_match = _keyword_match(haystack, keywords if isinstance(keywords, list) else [str(keywords)])
        if is_match:
            matched_any = True

        results.append({
            'title': title,
            'url': final_url or url,
            'platform': hit.get('_site', ''),
            'keywords': keyword_str,
            'publish_date': publish_date,
            'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'snippet': snippet,
            'content': content,
            'author': (meta.get("meta_author") or ""),
            'meta': meta,
            'images': images,
            'raw_html_path': raw_html_relpath,
