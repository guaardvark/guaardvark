#!/bin/bash
# Guaardvark Dashboard — GitHub + Discord stats at a glance
# Usage: ./scripts/dashboard.sh

set -e
cd "$(dirname "$0")/.."
source .env 2>/dev/null || true

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
DIM='\033[2m'
NC='\033[0m'

echo ""
echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${WHITE}  GUAARDVARK DASHBOARD${NC}  ${DIM}$(date '+%Y-%m-%d %H:%M:%S')${NC}"
echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# --- GITHUB ---
echo ""
echo -e "${CYAN}  GITHUB${NC}"
echo -e "${DIM}  ──────${NC}"

REPO_DATA=$(gh repo view --json stargazerCount,forkCount,watchers 2>/dev/null)
STARS=$(echo "$REPO_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin)['stargazerCount'])" 2>/dev/null || echo "?")
FORKS=$(echo "$REPO_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin)['forkCount'])" 2>/dev/null || echo "?")

VIEWS_DATA=$(gh api repos/guaardvark/guaardvark/traffic/views 2>/dev/null)
VIEWS_TOTAL=$(echo "$VIEWS_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "?")
VIEWS_UNIQUE=$(echo "$VIEWS_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin)['uniques'])" 2>/dev/null || echo "?")
VIEWS_TODAY=$(echo "$VIEWS_DATA" | python3 -c "
import json,sys
d=json.load(sys.stdin)
views=d.get('views',[])
print(views[-1]['count'] if views else 0)
" 2>/dev/null || echo "?")

CLONES_DATA=$(gh api repos/guaardvark/guaardvark/traffic/clones 2>/dev/null)
CLONES_TOTAL=$(echo "$CLONES_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "?")
CLONES_UNIQUE=$(echo "$CLONES_DATA" | python3 -c "import json,sys; print(json.load(sys.stdin)['uniques'])" 2>/dev/null || echo "?")

OPEN_ISSUES=$(gh issue list --state open --json number 2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")
OPEN_PRS=$(gh pr list --state open --json number 2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin)))" 2>/dev/null || echo "?")

echo -e "  ${WHITE}Stars:${NC} $STARS  ${WHITE}Forks:${NC} $FORKS  ${WHITE}Issues:${NC} $OPEN_ISSUES open  ${WHITE}PRs:${NC} $OPEN_PRS open"
echo -e "  ${WHITE}Views (14d):${NC} $VIEWS_TOTAL total, $VIEWS_UNIQUE unique  ${DIM}(latest day: $VIEWS_TODAY)${NC}"
echo -e "  ${WHITE}Clones (14d):${NC} $CLONES_TOTAL total, $CLONES_UNIQUE unique"

# Recent PRs
echo ""
echo -e "  ${DIM}Recent PRs:${NC}"
gh pr list --state all --limit 5 --json number,title,state,author 2>/dev/null | python3 -c "
import json,sys
prs=json.load(sys.stdin)
for pr in prs:
    icon = '✅' if pr['state']=='MERGED' else '❌' if pr['state']=='CLOSED' else '🔵'
    print(f'    {icon} #{pr[\"number\"]} {pr[\"title\"][:50]} ({pr[\"author\"][\"login\"]})')
" 2>/dev/null || echo "    (unable to fetch)"

# --- DISCORD ---
echo ""
echo -e "${CYAN}  DISCORD BOT${NC}"
echo -e "${DIM}  ───────────${NC}"

BOT_HEALTH=$(curl -sf http://localhost:8200/health 2>/dev/null)
if [ -n "$BOT_HEALTH" ]; then
    echo "$BOT_HEALTH" | python3 -c "
import json,sys
d=json.load(sys.stdin)
status = d['status'].upper()
color = '\033[0;32m' if status == 'HEALTHY' else '\033[1;33m'
print(f'  \033[1;37mStatus:\033[0m {color}{status}\033[0m  \033[1;37mGuilds:\033[0m {d[\"guild_count\"]}  \033[1;37mLatency:\033[0m {d[\"latency_ms\"]}ms  \033[1;37mUptime:\033[0m {d[\"uptime_seconds\"]//3600}h {(d[\"uptime_seconds\"]%3600)//60}m')
" 2>/dev/null
else
    echo -e "  ${WHITE}Status:${NC} ${RED}OFFLINE${NC}"
fi

# Discord server stats via API
if [ -n "$DISCORD_BOT_TOKEN" ]; then
    GUILD_ID="1481468315187810306"
    GUILD_DATA=$(curl -sf -H "Authorization: Bot $DISCORD_BOT_TOKEN" "https://discord.com/api/v10/guilds/$GUILD_ID?with_counts=true" 2>/dev/null)
    if [ -n "$GUILD_DATA" ]; then
        echo "$GUILD_DATA" | python3 -c "
import json,sys
d=json.load(sys.stdin)
online = d.get('approximate_presence_count', '?')
members = d.get('approximate_member_count', '?')
print(f'  \033[1;37mMembers:\033[0m {members}  \033[1;37mOnline:\033[0m {online}')
" 2>/dev/null
    fi
fi

# VIP greeting status
VIP_FILE="data/context/vip_greeted.json"
if [ -f "$VIP_FILE" ]; then
    GREETED=$(python3 -c "import json; print(len(json.load(open('$VIP_FILE')).get('greeted',[])))" 2>/dev/null || echo "0")
    echo -e "  ${WHITE}VIP Greetings Sent:${NC} $GREETED"
else
    echo -e "  ${WHITE}VIP Greetings Sent:${NC} 0 ${DIM}(armed and waiting)${NC}"
fi

# Recent bot activity
echo ""
echo -e "  ${DIM}Recent activity:${NC}"
grep "\[/ask\]\|\[channel\]\|\[/claude\]\|VIP\|/demo" logs/discord_bot.log 2>/dev/null | tail -5 | while read line; do
    echo -e "    ${DIM}$line${NC}"
done
if ! grep -q "\[/ask\]\|\[channel\]\|\[/claude\]\|VIP\|/demo" logs/discord_bot.log 2>/dev/null; then
    echo -e "    ${DIM}(no recent chat activity)${NC}"
fi

# --- SYSTEM ---
echo ""
echo -e "${CYAN}  SYSTEM${NC}"
echo -e "${DIM}  ──────${NC}"

BACKEND_UP=$(curl -sf http://localhost:${FLASK_PORT:-5002}/api/health >/dev/null 2>&1 && echo "ONLINE" || echo "OFFLINE")
BACKEND_COLOR="${GREEN}"
[ "$BACKEND_UP" = "OFFLINE" ] && BACKEND_COLOR="${RED}"
echo -e "  ${WHITE}Backend:${NC} ${BACKEND_COLOR}${BACKEND_UP}${NC} (port ${FLASK_PORT:-5002})"

FRONTEND_UP=$(curl -sf http://localhost:${VITE_PORT:-5175} >/dev/null 2>&1 && echo "ONLINE" || echo "OFFLINE")
FRONTEND_COLOR="${GREEN}"
[ "$FRONTEND_UP" = "OFFLINE" ] && FRONTEND_COLOR="${RED}"
echo -e "  ${WHITE}Frontend:${NC} ${FRONTEND_COLOR}${FRONTEND_UP}${NC} (port ${VITE_PORT:-5175})"

OLLAMA_UP=$(curl -sf http://localhost:11434/api/tags >/dev/null 2>&1 && echo "ONLINE" || echo "OFFLINE")
OLLAMA_COLOR="${GREEN}"
[ "$OLLAMA_UP" = "OFFLINE" ] && OLLAMA_COLOR="${RED}"
OLLAMA_MODELS=$(curl -sf http://localhost:11434/api/tags 2>/dev/null | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('models',[])))" 2>/dev/null || echo "?")
echo -e "  ${WHITE}Ollama:${NC} ${OLLAMA_COLOR}${OLLAMA_UP}${NC} ($OLLAMA_MODELS models)"

GPU_INFO=$(nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits 2>/dev/null)
if [ -n "$GPU_INFO" ]; then
    echo "$GPU_INFO" | while IFS=', ' read gpu_util mem_used mem_total temp; do
        echo -e "  ${WHITE}GPU:${NC} ${gpu_util}% util, ${mem_used}/${mem_total} MB VRAM, ${temp}°C"
    done
fi

# Unpushed commits
UNPUSHED=$(git log origin/main..HEAD --oneline 2>/dev/null | wc -l)
if [ "$UNPUSHED" -gt 0 ]; then
    echo ""
    echo -e "  ${YELLOW}⚠ $UNPUSHED unpushed commits${NC}"
fi

echo ""
echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
