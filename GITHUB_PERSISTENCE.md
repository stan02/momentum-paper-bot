# 每日动量策略论文 — GitHub 持久化方案

## 痛点

自动化任务每次新建对话→新工作区→文件全丢。
解决方式：**用 GitHub 仓库做持久层**。

## 三步搞定

### ① 建仓库

```bash
# 在 GitHub 创建一个空仓库（私有即可）：momentum-paper-bot

# 然后运行初始化脚本
chmod +x setup_github_persistence.sh
./setup_github_persistence.sh
```

### ② 配认证（让服务器可以推送）

```bash
# 方式 A：Personal Access Token（推荐）
export GIT_REPO_URL="https://<你的用户名>:<你的token>@github.com/<用户名>/momentum-paper-bot.git"

# 方式 B：SSH Key（需要提前配置）
export GIT_REPO_URL="git@github.com:<用户名>/momentum-paper-bot.git"
```

### ③ 写自动化提示词

在你自动化任务的提示词**最开头**加上这段：

```
【持久化配置】
export GIT_REPO_URL="https://<你的用户名>:<你的token>@github.com/<用户名>/momentum-paper-bot.git"
export REPO_ROOT="/workspace/momentum-paper-bot"

if [ -d "$REPO_ROOT/.git" ]; then
    cd "$REPO_ROOT" && git pull --ff-only
else
    rm -rf "$REPO_ROOT"
    git clone --depth=1 "$GIT_REPO_URL" "$REPO_ROOT"
fi
cd "$REPO_ROOT"

python3 daily_momentum_paper.py
```

然后接你原来的 Step 2~5 即可。

## 工作原理

```
自动化启动
    │
    ├─ git pull（取回上次的状态、日志）
    │
    ├─ python3 daily_momentum_paper.py
    │    ├─ 读 state.json（哪些论文处理过了）
    │    ├─ 搜 arXiv → 找未处理的新论文 → 下载 PDF
    │    ├─ 生成解读报告 *.md
    │    └─ 更新 state.json + 日志
    │
    └─ git commit + git push（同步回 GitHub）
```

## 仓库结构

```
momentum-paper-bot/
├── daily_momentum_paper.py    # 主脚本
├── 动量策略论文解读日志.md      # 跨会话累积日志
├── momentum_papers/
│   ├── .state.json            # 已处理论文列表（跨会话持久）
│   ├── arxiv_id.pdf           # PDF（gitignore，不推送）
│   ├── arxiv_id.txt           # 全文文本（可选）
│   └── arxiv_id_解读报告.md    # 解读报告
└── .gitignore
```

## 注意

- **PDF 不推送**（太大），放 `.gitignore` 里，每次运行重新下载
- **token 不要写死到脚本里**，放在环境变量或提示词里
- 建议把 `GIT_REPO_URL` 放到你创建自动化任务时的**环境变量配置**位置，而不是明文写在提示词里
