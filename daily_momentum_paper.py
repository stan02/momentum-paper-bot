#!/usr/bin/env python3
"""
每日动量策略论文搜索下载脚本（GitHub 持久化版）
==============================================
- 搜索 arXiv 上关于 daily momentum / time-series momentum 的最新论文
- 支持 GitHub 仓库存放状态、日志、解读报告（跨会话持久化）
- PDF 文件体积大，不推入 git，仅保存在本地工作目录
==============================================

环境变量（可选）：
  GIT_REPO_URL    GitHub 仓库地址，开启持久化同步
                   例：https://github.com/yourname/momentum-paper-bot.git
  GIT_BRANCH      分支名（默认 main）
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

# ============================================================
# 配置
# ============================================================

# 基础路径：如果从 git 仓库启动，以仓库目录为准
REPO_ROOT = os.environ.get("REPO_ROOT", "/workspace/momentum-paper-bot")
PAPERS_DIR = os.path.join(REPO_ROOT, "momentum_papers")
STATE_FILE = os.path.join(PAPERS_DIR, ".state.json")
LOG_FILE = os.path.join(REPO_ROOT, "动量策略论文解读日志.md")

GIT_REPO_URL = os.environ.get("GIT_REPO_URL", "")
GIT_BRANCH = os.environ.get("GIT_BRANCH", "main")

ARXIV_API_URL = "http://export.arxiv.org/api/query"

# 搜索关键词组合
SEARCH_QUERIES = [
    '"time-series momentum" strategy',
    '"daily momentum" trading strategy',
    '"momentum strategy" daily return',
    '"cross-sectional momentum"',
    '"momentum factor"',
    'momentum asset pricing anomaly',
]

CATEGORIES = ("cat:q-fin.PR OR cat:q-fin.GN OR cat:q-fin.ST "
              "OR cat:q-fin.PM OR cat:q-fin.RM OR cat:stat.ME OR cat:cs.LG")


# ============================================================
# Git 同步层
# ============================================================

def git_run(*args, check=True):
    """在 REPO_ROOT 中执行 git 命令"""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if check and result.returncode != 0:
            print(f"[git 警告] {' '.join(args)}: {result.stderr.strip()}")
        return result
    except FileNotFoundError:
        print("[git 警告] git 未安装，跳过 git 操作")
        return None
    except subprocess.TimeoutExpired:
        print("[git 警告] git 操作超时，跳过")
        return None


def git_init_if_needed():
    """如果仓库未初始化但提供了 GIT_REPO_URL，则 clone"""
    if not GIT_REPO_URL:
        return False
    if os.path.exists(os.path.join(REPO_ROOT, ".git")):
        return True
    print(f"[git] 克隆仓库 {GIT_REPO_URL} ...")
    try:
        parent = os.path.dirname(REPO_ROOT)
        os.makedirs(parent, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--depth=1", "-b", GIT_BRANCH, GIT_REPO_URL, REPO_ROOT],
            check=True, capture_output=True, text=True, timeout=60,
        )
        print("[git] 克隆成功")
        return True
    except Exception as e:
        print(f"[git] 克隆失败（首次运行?）: {e}")
        os.makedirs(REPO_ROOT, exist_ok=True)
        return False


def git_sync_pull():
    """拉取远程最新数据"""
    if not GIT_REPO_URL:
        return
    if not os.path.exists(os.path.join(REPO_ROOT, ".git")):
        return
    print("[git] 拉取远程更新...")
    # stash 本地未提交变更（如运行时产生的新文件）
    git_run("stash", check=False)
    result = git_run("pull", "--ff-only", "origin", GIT_BRANCH, check=False)
    if result and result.returncode == 0:
        print("[git] 拉取成功")
    else:
        print("[git] 拉取无变更或失败，继续使用本地数据")
    # 恢复 stash
    git_run("stash", "pop", check=False)


def git_sync_push(commit_msg=""):
    """提交并推送变更到远程"""
    if not GIT_REPO_URL:
        return
    if not os.path.exists(os.path.join(REPO_ROOT, ".git")):
        return
    print("[git] 提交并推送到远程...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    default_msg = f"📄 每日更新 {now}"
    msg = commit_msg or default_msg
    git_run("add", "-A")
    result = git_run("commit", "-m", msg, check=False)
    if result and result.returncode == 0:
        push_result = git_run("push", "origin", GIT_BRANCH, check=False)
        if push_result and push_result.returncode == 0:
            print("[git] 推送成功 ✅")
        else:
            print("[git] 推送失败，请检查认证配置")
    else:
        print("[git] 无变更需提交")


# ============================================================
# 核心逻辑
# ============================================================

def ensure_dirs():
    os.makedirs(PAPERS_DIR, exist_ok=True)


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {"processed_ids": [], "last_run": None, "last_paper_id": None}


def save_state(state):
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def search_arxiv_via_api(query, max_results=15):
    """通过 arXiv 官方 API (export.arxiv.org) 搜索论文

    遵守 API 使用条款: https://info.arxiv.org/help/api/tou.html
    限速: 最多 1 请求/3 秒，单连接
    """
    # arXiv API 要求去掉引号，直接传搜索词
    search_query = re.sub(r'["\']', '', query).strip()
    url = (f"{ARXIV_API_URL}?"
           f"search_query=all:{urllib.parse.quote(search_query)}"
           f"&start=0&max_results={max_results}"
           f"&sortBy=submittedDate&sortOrder=descending")

    print(f"[API 搜索] {query[:60]}...")
    req = urllib.request.Request(url, headers={
        'User-Agent': 'MomentumPaperBot/1.0 (mailto:research@example.com)'
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            xml_data = resp.read().decode('utf-8')
    except Exception as e:
        print(f"[API 错误] 搜索失败: {e}")
        return []

    # 解析 Atom XML
    ns = {
        'atom': 'http://www.w3.org/2005/Atom',
        'arxiv': 'http://arxiv.org/schemas/atom',
    }
    root = ET.fromstring(xml_data)
    papers = []
    for entry in root.findall('atom:entry', ns):
        # arXiv ID
        id_full = entry.find('atom:id', ns).text
        id_match = re.search(r'(\d+\.\d+)', id_full)
        if not id_match:
            continue
        arxiv_id = id_match.group(1)

        # 标题
        title_el = entry.find('atom:title', ns)
        title = title_el.text.strip() if title_el is not None else "Unknown"
        title = re.sub(r'\s+', ' ', title).strip()

        # 摘要
        summary_el = entry.find('atom:summary', ns)
        summary = summary_el.text.strip() if summary_el is not None else ""
        summary = re.sub(r'\s+', ' ', summary).strip()[:500]

        # 作者
        authors = []
        for author in entry.findall('atom:author', ns):
            name_el = author.find('atom:name', ns)
            if name_el is not None and name_el.text:
                authors.append(name_el.text.strip())

        # 发布日期
        pub_el = entry.find('atom:published', ns)
        published = pub_el.text[:10] if pub_el is not None else ""

        # 分类
        categories = []
        for cat in entry.findall('atom:category', ns):
            term = cat.get('term', '')
            if term:
                categories.append(term)

        papers.append({
            'arxiv_id': arxiv_id,
            'title': title,
            'summary': summary,
            'published': published,
            'updated': published,
            'authors': authors[:10],
            'categories': categories[:10],
            'pdf_url': f"https://arxiv.org/pdf/{arxiv_id}.pdf",
            'abs_url': f"https://arxiv.org/abs/{arxiv_id}",
        })

    print(f"  >> API 返回 {len(papers)} 篇论文")
    return papers


def search_arxiv(query, max_results=15):
    """搜索论文：优先使用 arXiv 官方 API，失败时回退到 HTML 搜索页面"""
    # ── 优先：官方 API ──
    papers = search_arxiv_via_api(query, max_results)
    if papers:
        return papers

    # ── 回退：HTML 搜索页面 ──
    print("[回退] API 搜索失败，尝试 HTML 搜索页面...")
    search_query = query.replace("all:", "").replace('"', "")
    url = ("https://arxiv.org/search/?searchtype=all"
           f"&query={urllib.parse.quote(query)}"
           "&order=-announced_date_first")
    print(f"[HTML 搜索] {query[:60]}...")
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                       'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            html = resp.read().decode('utf-8')
    except Exception as e:
        print(f"[错误] 搜索失败: {e}")
        return []

    papers = []
    # 解析 HTML 条目
    # arXiv 搜索页面的每个结果形如:
    # <li class="arxiv-result"> ... <p class="title">Title</p> ... <span class="tag">q-fin.PM</span> ...
    # <a href="/abs/1234.56789"> 或 <a href="https://arxiv.org/abs/1234.56789">
    # 提取所有 arxiv-result 块
    pattern = r'<li[^>]*class="arxiv-result"[^>]*>(.*?)</li>'
    blocks = re.findall(pattern, html, re.DOTALL)
    print(f"  >> 找到 {len(blocks)} 个结果块")
    for block in blocks:
        # 提取 arXiv ID
        id_match = re.search(r'href="[^"]*/abs/(\d+\.\d+)"', block)
        if not id_match:
            continue
        arxiv_id = id_match.group(1)
        # 提取标题
        title_match = re.search(r'<p[^>]*class="title[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>', block, re.DOTALL)
        if not title_match:
            title_match = re.search(r'class="title[^"]*"[^>]*>(.*?)</p>', block, re.DOTALL)
        title = title_match.group(1).strip() if title_match else "Unknown"
        title = re.sub(r'<[^>]+>', '', title)
        title = title.replace('\n', ' ').replace('  ', ' ').strip()
        # 提取摘要
        summary_match = re.search(r'<span[^>]*class="abstract-short[^"]*"[^>]*>(.*?)</span>', block, re.DOTALL)
        summary = summary_match.group(1).strip() if summary_match else ""
        summary = re.sub(r'<[^>]+>', '', summary)
        summary = summary.replace('\n', ' ').replace('  ', ' ').strip()[:500]
        # 提取作者
        authors_match = re.search(r'<span[^>]*class="authors[^"]*"[^>]*>(.*?)</span>', block, re.DOTALL)
        authors = []
        if authors_match:
            authors_text = re.sub(r'<[^>]+>', '', authors_match.group(1))
            authors = [a.strip() for a in authors_text.replace('et al.', '').split(',') if a.strip()]
        # 提取日期
        date_match = re.search(r'Submitted\s+(\d+)\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', block, re.DOTALL)
        published = ""
        if date_match:
            day, month_str, year = date_match.group(1), date_match.group(2), date_match.group(3)
            month_map = {"January":"01","February":"02","March":"03","April":"04","May":"05","June":"06",
                         "July":"07","August":"08","September":"09","October":"10","November":"11","December":"12"}
            published = f"{year}-{month_map.get(month_str,'01')}-{day.zfill(2)}"
        # 提取分类
        categories = []
        cat_matches = re.findall(r'<span[^>]*class="tag[^"]*"[^>]*>([^<]+)</span>', block)
        categories = [c.strip() for c in cat_matches if c.strip()]
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"

        papers.append({
            'arxiv_id': arxiv_id,
            'title': title,
            'summary': summary[:500],
            'published': published,
            'updated': published,
            'authors': authors[:10],
            'categories': categories[:10],
            'pdf_url': pdf_url,
            'abs_url': f"https://arxiv.org/abs/{arxiv_id}",
        })
    return papers


def download_pdf(arxiv_id, pdf_url):
    pdf_path = os.path.join(PAPERS_DIR, f"{arxiv_id}.pdf")
    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 10000:
        print(f"[跳过] PDF 已存在: {pdf_path}")
        return pdf_path
    print(f"[下载] {pdf_url} -> {pdf_path}")
    req = urllib.request.Request(pdf_url, headers={
        'User-Agent': 'MomentumPaperBot/1.0 (mailto:research@example.com)'
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(pdf_path, 'wb') as f:
                f.write(resp.read())
        print(f"[完成] 下载成功 ({os.path.getsize(pdf_path) / 1024:.1f} KB)")
        return pdf_path
    except Exception as e:
        print(f"[错误] 下载失败: {e}")
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
        return None


def is_relevant_paper(paper):
    title_lower = paper['title'].lower()
    summary_lower = paper['summary'].lower()
    combined = title_lower + ' ' + summary_lower

    # ── 排除物理/工程语境下的假阳性 ──
    # 如果 "momentum" 出现在力学/流体/地球物理等语境中
    # 且没有金融语境关键词，直接排除
    physics_exclusion = [
        'momentum balance', 'balance of momentum', 'linear momentum',
        'quasi-static momentum', 'fluid momentum', 'angular momentum',
        'momentum equation', 'momentum conservation', 'momentum source',
        'momentum exchange', 'momentum transfer', 'momentum flux',
        'thermo-poroelasticity', 'poroelasticity', 'poroelastic',
        'navier-stokes', 'reynolds-averaged', 'rans', 'les',
        'computational fluid dynamics', 'cfd simulation',
        'finite element momentum', 'particle momentum',
        'momentum thickness', 'momentum integral',
        'electromagnetic momentum', 'photon momentum',
        'momentum space', 'momentum distribution function',
        'momentum operator', 'wave momentum',
    ]
    financial_context = [
        'stock', 'equity', 'portfolio', 'trading strategy',
        'asset pricing', 'investor', 'sharpe', 'volatility',
        'risk premium', 'factor model', 'market anomaly',
        'momentum return', 'momentum profit', 'momentum portfolio',
        'return predictability', 'abnormal return',
    ]
    has_physics = any(kw in combined for kw in physics_exclusion)
    has_finance = any(kw in combined for kw in financial_context)
    if has_physics and not has_finance:
        return False, 0

    strong_keywords = ['momentum', 'time-series momentum', 'cross-sectional momentum']
    aux_keywords = [
        'daily', 'short-term', 'trading strategy', 'factor', 'anomaly',
        'asset pricing', 'return predictability', 'behavioral',
        'risk premium', 'factor investing', 'price trend',
        'moving average', 'trend following', 'technical trading',
        'market efficiency', 'overreaction', 'underreaction',
        'volume', 'turnover', '52-week high',
    ]
    has_strong = any(kw in combined for kw in strong_keywords)
    if not has_strong:
        return False, 0
    score = 1.0
    if 'daily momentum' in combined or 'time-series momentum' in combined:
        score += 2.0
    if 'strategy' in combined:
        score += 0.5
    if 'trading' in combined:
        score += 0.5
    score += sum(1 for kw in aux_keywords if kw in combined) * 0.3
    return True, round(score, 1)


def append_to_log(arxiv_id, title, authors, published, score_summary):
    """追加一条日志记录"""
    entry = (
        f"\n## {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"### 论文：{title}\n\n"
        f"| 项目 | 内容 |\n"
        f"|------|------|\n"
        f"| **arXiv ID** | {arxiv_id} |\n"
        f"| **作者** | {', '.join(authors[:3])} 等 |\n"
        f"| **日期** | {published[:10]} |\n"
        f"| **核心结论** | {score_summary} |\n"
        f"| **报告路径** | `momentum_papers/{arxiv_id}_解读报告.md` |\n"
        f"| **状态** | ✅ 已完成 |\n"
        f"\n---\n"
    )
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(entry)


def main():
    print("=" * 60)
    print(f"  每日动量策略论文搜索（GitHub 持久化版）")
    print(f"  运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  仓库: {GIT_REPO_URL or '（未配置，仅本地运行）'}")
    print("=" * 60)

    # ── 第 0 步：Git 同步 ──
    git_init_if_needed()
    if GIT_REPO_URL:
        git_sync_pull()

    ensure_dirs()
    state = load_state()

    print(f"\n已处理论文: {len(state['processed_ids'])} 篇")
    if state['last_paper_id']:
        print(f"上次论文: {state['last_paper_id']}")

    # ── 第 1 步：搜索 ──
    all_papers = []
    seen_ids = set()
    for idx, query in enumerate(SEARCH_QUERIES):
        if idx > 0:
            time.sleep(3)  # 遵守 API 限速: 1 请求/3 秒
        for p in search_arxiv(query):
            if p['arxiv_id'] not in seen_ids:
                seen_ids.add(p['arxiv_id'])
                all_papers.append(p)

    all_papers.sort(key=lambda p: p['published'], reverse=True)
    print(f"\n搜索到 {len(all_papers)} 篇去重候选")

    relevant = []
    for p in all_papers:
        is_rel, score = is_relevant_paper(p)
        if is_rel:
            p['relevance_score'] = score
            relevant.append(p)
    relevant.sort(key=lambda p: p['relevance_score'], reverse=True)
    print(f"相关论文: {len(relevant)} 篇")

    # ── 第 2 步：选论文 ──
    if not relevant:
        result = {"status": "no_new_paper",
                  "message": "未找到相关的动量策略论文",
                  "searched": len(all_papers),
                  "timestamp": datetime.now(timezone.utc).isoformat()}
        print(f"\n结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
        print("\n---JSON_OUTPUT---")
        print(json.dumps(result, ensure_ascii=False))
        return result

    selected = None
    for p in relevant:
        if p['arxiv_id'] not in state['processed_ids']:
            selected = p
            break

    if not selected:
        result = {"status": "no_new_paper",
                  "message": "所有相关论文均已处理",
                  "searched": len(all_papers),
                  "relevant": len(relevant),
                  "total_processed": len(state['processed_ids']),
                  "timestamp": datetime.now(timezone.utc).isoformat()}
        print(f"\n结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
        print("\n---JSON_OUTPUT---")
        print(json.dumps(result, ensure_ascii=False))
        return result

    print(f"\n═══ 选中论文 ═══")
    print(f"  ID:      {selected['arxiv_id']}")
    print(f"  标题:    {selected['title']}")
    print(f"  作者:    {', '.join(selected['authors'][:5])}")
    print(f"  日期:    {selected['published'][:10]}")
    print(f"  得分:    {selected['relevance_score']}")
    print(f"  URL:     {selected['abs_url']}")

    # ── 第 3 步：下载 PDF ──
    pdf = download_pdf(selected['arxiv_id'], selected['pdf_url'])
    if not pdf:
        result = {"status": "download_failed",
                  "message": f"下载失败: {selected['arxiv_id']}",
                  "paper": selected,
                  "timestamp": datetime.now(timezone.utc).isoformat()}
        print(f"\n结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
        print("\n---JSON_OUTPUT---")
        print(json.dumps(result, ensure_ascii=False))
        return result

    # ── 更新状态 ──
    state['processed_ids'].append(selected['arxiv_id'])
    state['last_paper_id'] = selected['arxiv_id']
    save_state(state)

    result = {
        "status": "new_paper",
        "message": f"成功下载: {selected['title'][:80]}...",
        "paper": {
            "arxiv_id": selected['arxiv_id'],
            "title": selected['title'],
            "authors": selected['authors'][:5],
            "published": selected['published'],
            "categories": selected['categories'],
            "relevance_score": selected['relevance_score'],
            "pdf_path": pdf,
            "txt_path": pdf.replace('.pdf', '.txt'),
            "report_path": os.path.join(
                PAPERS_DIR, f"{selected['arxiv_id']}_解读报告.md"),
            "abs_url": selected['abs_url'],
        },
        "num_processed": len(state['processed_ids']),
        "repo_root": REPO_ROOT,
        "git_sync": bool(GIT_REPO_URL),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    print(f"\n结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
    print("\n---JSON_OUTPUT---")
    print(json.dumps(result, ensure_ascii=False))

    # ── Git 推送（如果配置了仓库） ──
    if GIT_REPO_URL:
        git_sync_push(
            f"📄 每日更新 {datetime.now().strftime('%Y-%m-%d')}: "
            f"{selected['arxiv_id']} - {selected['title'][:60]}"
        )

    return result


if __name__ == "__main__":
    main()
