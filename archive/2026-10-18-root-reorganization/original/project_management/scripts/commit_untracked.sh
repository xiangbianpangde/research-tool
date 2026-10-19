#!/bin/bash
# research-tool 未跟踪文件处理脚本
# 启动期 6 个未跟踪文件处置：
#   1. research_tool/infrastructure/export/wiki_stage.py
#   2. research_tool/infrastructure/search/opencli_backend.py
#   3. research_tool/tests/test_wiki_stage.py
#   4. research_tool/tests/test_opencli_search_backend.py
#   5. research_tool/tests/test_pipeline_reliability_profiles.py
#   6. worklogs/2026-07-16-llm-healthcheck-source-patch-attempt.md
# 用法：bash commit_untracked.sh --dry-run | --apply

set -e
REPO="/Volumes/项目/research-tool"
cd "$REPO"
DRY_RUN=true
[ "$1" = "--apply" ] && DRY_RUN=false

FILES=(
    "research_tool/infrastructure/export/wiki_stage.py"
    "research_tool/infrastructure/search/opencli_backend.py"
    "research_tool/tests/test_wiki_stage.py"
    "research_tool/tests/test_opencli_search_backend.py"
    "research_tool/tests/test_pipeline_reliability_profiles.py"
    "worklogs/2026-07-16-llm-healthcheck-source-patch-attempt.md"
)

echo "=== research-tool 未跟踪文件处置（$( [ "$DRY_RUN" = true ] && echo 'dry-run' || echo 'apply' )）==="
echo ""

echo "[1/3] 文件存在性检查："
EXISTING=0; MISSING=0
for f in "${FILES[@]}"; do
    if [ -f "$f" ]; then echo "  ✅ $f"; EXISTING=$((EXISTING+1))
    else echo "  ❌ $f (不存在)"; MISSING=$((MISSING+1)); fi
done
echo "  存在: $EXISTING / 缺失: $MISSING / 总: ${#FILES[@]}"
echo ""

echo "[2/3] 文件大小检查："
for f in "${FILES[@]}"; do
    if [ -f "$f" ]; then
        size=$(wc -c < "$f" | tr -d ' ')
        lines=$(wc -l < "$f" | tr -d ' ')
        if [ "$size" -lt 100 ]; then echo "  ⚠️  $f (${size}B ${lines}行) 疑似占位"
        else echo "  ✅ $f (${size}B ${lines}行)"; fi
    fi
done
echo ""

echo "[3/3] git add 操作："
if [ "$DRY_RUN" = true ]; then
    echo "  (dry-run 模式，未实际执行)"
    for f in "${FILES[@]}"; do
        if [ -f "$f" ]; then echo "    git add \"$f\""; fi
    done
    echo ""
    echo "建议 commit message:"
    echo "  feat(ingest): add wiki_stage + opencli_backend + reliability (R13)"
    echo ""
    echo "实际执行：bash commit_untracked.sh --apply"
else
    for f in "${FILES[@]}"; do
        if [ -f "$f" ]; then git add "$f"; echo "  ✅ git add $f"; fi
    done
    echo ""
    echo "下一步："
    echo "  git status --short"
    echo "  git commit -m 'feat(ingest): add R13 untracked files'"
fi
echo ""
echo "如需撤回：git reset HEAD <file>"