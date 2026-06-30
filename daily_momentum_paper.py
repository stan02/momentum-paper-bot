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
    'all:"daily momentum" AND all:strategy',
    'all:"daily momentum" AND all:trading',
    'all:"time-series momentum" AND all:daily',
    'all:"momentum strategy" AND all:daily AND all:return',
    'all:"short-term momentum" AND all:asset',
    'all:"cross-sectional momentum" AND all:daily',
    'all:"momentum" AND all:behavioral AND all:finance',
    'all:"momentum factor" AND all:empirical',
    'all:"momentum" AND all:asset pricing AND all:anomaly',
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


def search_arxiv(query, max_results=15):
    params = {
        'search_query': f'({query}) AND ({CATEGORIES})',
        'start': 0,
        'max_results': max_results,
        'sortBy': 'submittedDate',
        'sortOrder': 'descending',
    }
    url = f"{ARXIV_API_URL}?{urllib.parse.urlencode(params, safe='():')}"
    print(f"[搜索] {url[:110]}...")
    req = urllib.request.Request(url, headers={
        'User-Agent': 'MomentumPaperBot/1.0 (mailto:research@example.com)'
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_data = resp.read().decode('utf-8')
    except Exception as e:
        print(f"[错误] 搜索失败: {e}")
        return []
    ns = {'atom': 'http://www.w3.org/2005/Atom',
          'arxiv': 'http://arxiv.org/schemas/atom'}
    root = ET.fromstring(xml_data)
    entries = root.findall('atom:entry', ns)
    papers = []
    for entry in entries:
        paper_id_full = entry.find('atom:id', ns).text.strip()
        arxiv_id_match = re.search(r'arxiv\.org/abs/(.+?)(?:v\d+)?$', paper_id_full)
        if not arxiv_id_match:
            continue
        arxiv_id = arxiv_id_match.group(1)
        title = entry.find('atom:title', ns).text.strip().replace('\n', ' ').replace('  ', ' ')
        summary = entry.find('atom:summary', ns).text.strip().replace('\n', ' ').replace('  ', ' ')
        published = entry.find('atom:published', ns).text.strip()
        updated = entry.find('atom:updated', ns).text.strip()
        authors = []
        for author_elem in entry.findall('atom:author', ns):
            authors.append(author_elem.find('atom:name', ns).text.strip())
        categories = []
        for cat_elem in entry.findall('atom:category', ns):
            categories.append(cat_elem.attrib.get('term', ''))
        pdf_link = None
        for link in entry.findall('atom:link', ns):
            if link.attrib.get('title') == 'pdf':
                pdf_link = link.attrib.get('href')
                break
        if not pdf_link:
            pdf_link = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        papers.append({
            'arxiv_id': arxiv_id,
            'title': title,
            'summary': summary[:500],
            'published': published,
            'updated': updated,
            'authors': authors[:10],
            'categories': categories[:10],
            'pdf_url': pdf_link,
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
    for query in SEARCH_QUERIES:
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
