#!/usr/bin/env bash
set -e

# ============================================================
# 每日动量策略论文 - GitHub 持久化一键初始化
# ============================================================
# 用法:
#   1. 先在 GitHub 创建一个空仓库 (比如 momentum-paper-bot)
#   2. 在终端运行:
#      chmod +x setup_github_persistence.sh
#      ./setup_github_persistence.sh
#   3. 按提示输入 GitHub 仓库 URL
# ============================================================

BOLD='\033[1m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BOLD}${BLUE}============================================${NC}"
echo -e "${BOLD}${BLUE}  每日动量策略论文 - GitHub 持久化初始化${NC}"
echo -e "${BOLD}${BLUE}============================================${NC}"
echo ""

# 检查 git 是否可用
if ! command -v git &> /dev/null; then
    echo "❌ 未检测到 git，请先安装。"
    exit 1
fi

# 输入仓库信息
read -p "请输入 GitHub 仓库 URL (如 https://github.com/yourname/momentum-paper-bot.git): " REPO_URL

if [ -z "$REPO_URL" ]; then
    echo "❌ 仓库 URL 不能为空"
    exit 1
fi

REPO_DIR="/workspace/momentum-paper-bot"

# 如果目录已存在则先清理
if [ -d "$REPO_DIR" ]; then
    echo "目录 $REPO_DIR 已存在，移除中..."
    rm -rf "$REPO_DIR"
fi

# ========== 创建本地仓库 ==========
echo ""
echo -e "${BOLD}📦 创建本地仓库...${NC}"
mkdir -p "$REPO_DIR"

# 复制当前脚本目录下的所有文件（脚本自身除外）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SCRIPT_DIR/daily_momentum_paper.py" "$REPO_DIR/"
mkdir -p "$REPO_DIR/momentum_papers"

# 创建 .gitkeep 保持目录结构
touch "$REPO_DIR/momentum_papers/.gitkeep"

# 创建状态文件
cat > "$REPO_DIR/momentum_papers/.state.json" << 'STATEEOF'
{
  "processed_ids": [],
  "last_run": null,
  "last_paper_id": null
}
STATEEOF

# 创建日志文件
cat > "$REPO_DIR/动量策略论文解读日志.md" << 'LOGEOF'
# 动量策略论文解读日志

---

LOGEOF

# 创建 .gitignore
cat > "$REPO_DIR/.gitignore" << 'GITIGNOREEOF'
# 不提交 PDF 文件（体积太大）
*.pdf
# Python 缓存
__pycache__/
*.pyc
# 系统文件
.DS_Store
GITIGNOREEOF

# ========== 初始化 git 仓库 ==========
cd "$REPO_DIR"
git init
git checkout -b main

git config user.name "Momentum Paper Bot"
git config user.email "bot@example.com"

git add -A
git commit -m "🎉 初始化：每日动量策略论文自动化系统"

# ========== 推送到远程 ==========
echo ""
echo -e "${BOLD}📤 推送到 GitHub...${NC}"
git remote add origin "$REPO_URL"

# 尝试推送（可能需要认证）
if git push -u origin main 2>/dev/null; then
    echo -e "${GREEN}✅ 推送成功！${NC}"
else
    echo ""
    echo "⚠️  推送失败（可能未配置认证）。请手动执行："
    echo "   cd $REPO_DIR"
    echo "   # 配置认证后"
    echo "   git push -u origin main"
fi

echo ""
echo -e "${BOLD}${GREEN}============================================${NC}"
echo -e "${BOLD}${GREEN}  ✅ 初始化完成！${NC}"
echo -e "${BOLD}${GREEN}============================================${NC}"
echo ""
echo "仓库目录: $REPO_DIR"
echo ""
echo -e "${BOLD}下一步：${NC}"
echo "1. 将仓库设为私有或公开"
echo "2. 在自动化任务的提示词开头加上这段话："
echo ""
echo "─────────────────────────────────────────────"
echo "【持久化配置】"
echo "GitHub 仓库: $REPO_URL"
echo "本地路径: \$REPO_DIR"
echo ""
echo "# 每次自动化启动时执行："
echo "if [ -d $REPO_DIR ]; then"
echo "    cd $REPO_DIR && git pull --ff-only"
echo "else"
echo "    git clone $REPO_URL $REPO_DIR"
echo "fi"
echo "cd $REPO_DIR"
echo "─────────────────────────────────────────────"
