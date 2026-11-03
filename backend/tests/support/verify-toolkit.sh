#!/usr/bin/env bash
# 自检脚本：由脚本而不是 AI 判断六件事（mock 测试 / 真实冒烟记录 / 密钥排除 / 重启恢复 / 前端构建 / 文件齐全与位置）。
# 每个产品一个文件夹：projects/<产品名>/。脚本进到这个文件夹里检查。
# 用法：./verify.sh [产品名] [阶段目录名]     例：./verify.sh 我的第一个产品 阶段2-1   （提示"权限不够"时在前面加 bash）
# projects/ 下只有一个产品时，产品名可以省略：./verify.sh 阶段2-1
# 给了阶段目录名，会把完整输出写到 projects/<产品名>/docs/evidence/<阶段目录名>/verify-输出.txt。
# 退出码：全部通过或 N/A 为 0；任一"不通过"、"未配置"或"待验"为 1；找不到产品为 2。
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# ---------- 选产品 ----------
PRODUCTS=()
for d in projects/*/; do [ -d "$d" ] && PRODUCTS+=("$(basename "$d")"); done
if [ $# -ge 1 ] && [ -d "projects/$1" ]; then
  PRODUCT="$1"; STAGE="${2:-}"
elif [ "${#PRODUCTS[@]}" -eq 1 ] && { [ $# -eq 0 ] || [[ "$1" == 阶段* ]]; }; then
  PRODUCT="${PRODUCTS[0]}"; STAGE="${1:-}"
else
  [ $# -ge 1 ] && echo "找不到产品\"$1\"，它也不是阶段名（阶段名以\"阶段\"开头，如 阶段2-1）。"
  echo "请指明检查哪个产品：./verify.sh <产品名> [阶段N]"
  echo "projects/ 下现有的产品：${PRODUCTS[*]:-（没有）}"
  exit 2
fi
P="projects/$PRODUCT"
LOG="$(mktemp)"
log() { printf '%s\n' "$*" | tee -a "$LOG"; }
sect() { log ""; log "================ $* ================"; }

R1="未配置"; R2="未配置"; R3="未配置"; R4="N/A（本阶段无正式前端）"; R5=""; R6="待验"
NOCODE=0; case "$STAGE" in 阶段0|阶段1|阶段3-0) NOCODE=1;; esac   # 无代码阶段（AGENTS.md 第 4 节）：测试、冒烟和重启检查不适用
D1=""; D2=""; D3=""; D4=""

log "verify.sh 自检  $(date '+%Y-%m-%d %H:%M:%S')  产品：${PRODUCT}（${ROOT}/${P}）"
cd "$P"

# ---------- 1. mock 自动化测试 ----------
sect "1. mock 自动化测试"
if [ "$NOCODE" -eq 1 ]; then
  R1="N/A（无代码阶段）"
elif [ -d backend/tests ]; then
  PY="$ROOT/$P/backend/.venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
  T="$(mktemp)"
  (cd backend && MODEL_API_KEY="" "$PY" -m pytest -q 2>&1) > "$T"; RC=$?
  cat "$T" >> "$LOG"
  PASSED="$(grep -Eo '[0-9]+ passed' "$T" | tail -1 | grep -Eo '[0-9]+')"; PASSED="${PASSED:-0}"
  FAILED="$(grep -Eo '[0-9]+ (failed|error)' "$T" | grep -Eo '[0-9]+' | paste -sd+ - | bc 2>/dev/null)"; FAILED="${FAILED:-0}"
  TOTAL=$((PASSED + FAILED))
  if [ "$RC" -eq 0 ] && [ "$PASSED" -ge 5 ]; then R1="通过（${PASSED}/${TOTAL}）"
  elif [ "$RC" -eq 0 ]; then R1="不通过（只有 ${PASSED} 条测试通过，至少要 5 条，防止空测试充数）"
  else R1="不通过（通过 ${PASSED}/${TOTAL}，退出码 ${RC}）"; fi
  D1="后端 pytest"
  rm -f "$T"
else
  log "未找到 backend/tests，跳过后端测试。"
fi
if [ -f frontend/package.json ] && grep -q '"test"' frontend/package.json; then
  T="$(mktemp)"
  (cd frontend && npm test --silent -- --run 2>&1) > "$T"; RC=$?
  cat "$T" >> "$LOG"
  if [ "$RC" -eq 0 ]; then R1="${R1}；前端测试通过"; else R1="不通过（前端测试退出码 ${RC}）"; fi
  rm -f "$T"
fi
log "结果：$R1"

# ---------- 2. 真实模型冒烟记录 ----------
sect "2. 真实模型冒烟记录（docs/evidence/<阶段>/冒烟记录.md，须由冒烟脚本写入，格式见通用手册第十节）"
if [ "$NOCODE" -eq 1 ]; then
  R6="N/A（无代码阶段）"
elif [ -z "$STAGE" ]; then
  R6="没给阶段名，未检查"; log "没给阶段名，跳过冒烟记录检查。"
else
  SMK="docs/evidence/$STAGE/冒烟记录.md"
  TS='^\| *20[0-9]{2}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}'
  if [ ! -s "$SMK" ]; then
    log "缺：${SMK}。真实冒烟还没跑，或冒烟脚本没有写记录。无 Key 时如实填\"待验\"，不要手造记录。"
    R6="待验（缺冒烟记录）"
  else
    MODEL="$(grep -m1 -E '^- *模型(：|:)' "$SMK" | sed -E 's/^- *模型(：|:) *//')"
    CALLS="$(grep -cE "$TS" "$SMK")"
    LAST="$(grep -E "$TS" "$SMK" | tail -1 | awk -F'|' '{print $2}' | xargs)"
    # Keep failure history; only the latest explicit run and its per-chain outcomes decide success.
    RUN_RESULT="$(awk '/^- 本轮结果[：:]/{line=$0; sub(/^- 本轮结果[：:] */, "", line); result=line} END {print result}' "$SMK")"
    BAD_RESULTS="$(awk -F'|' '
      /^## 运行 / {delete outcomes}
      /^[|] *20[0-9][0-9]-[0-9][0-9]-[0-9][0-9] [0-9][0-9]:[0-9][0-9]/ {
        chain=$3; result=$(NF-1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", chain)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", result)
        outcomes[chain]=result
      }
      END {for (chain in outcomes) if (outcomes[chain] != "通过") print chain "：" outcomes[chain]}
    ' "$SMK")"
    NEWER="$(find backend frontend scripts \( -path '*/.venv' -o -path '*/node_modules' -o -path '*/.next' -o -path '*/__pycache__' -o -path '*/.pytest_cache' -o -path '*/dist' \) -prune -o -type f -newer "$SMK" -print 2>/dev/null | head -5)"
    if [ -z "$MODEL" ] || [ "$CALLS" -eq 0 ]; then
      log "问题：$SMK 格式不对：缺\"- 模型：\"行，或没有一行带时间（YYYY-MM-DD HH:MM）的调用记录。"
      R6="待验（记录格式不对）"
    elif [ -n "$RUN_RESULT" ] && [ "$RUN_RESULT" != "通过" ]; then
      log "问题：最近一次冒烟整链路结果为 ${RUN_RESULT}，不能只凭记录存在判为通过。"
      R6="待验（最近一次真实冒烟未通过）"
    elif [ -n "$BAD_RESULTS" ]; then
      log "问题：最近运行中以下链路没有通过："; log "$BAD_RESULTS"
      R6="待验（真实调用失败或质量待打分）"
    elif [ -n "$NEWER" ]; then
      log "问题：以下代码文件比冒烟记录新，说明冒烟之后又改了代码，要重跑冒烟："; log "$NEWER"
      R6="待验（记录早于最近代码改动）"
    else
      log "有：${SMK}，模型 ${MODEL}，${CALLS} 次真实调用，最近一次 ${LAST}"
      R6="有记录（${MODEL}，${CALLS} 次，最近 ${LAST}）"
    fi
  fi
fi
log "结果：$R6"

# ---------- 3. 密钥排除 ----------
sect "3. 密钥排除（扫描整个仓库会进 git 的文件，以及本产品的构建产物）"
cd "$ROOT"
# 样式：OpenAI/DeepSeek 等 sk-…、火山引擎 AKLT…、腾讯云 SecretId AKID…、AWS AKIA…、以及"名字像密钥 = 长值"
PAT='sk-[A-Za-z0-9]{20,}|AKLT[A-Za-z0-9]{10,}|AKID[A-Za-z0-9]{10,}|AKIA[0-9A-Z]{16}|(api[_-]?key|secret|password|token)[[:space:]]*[=:][[:space:]]*["'"'"']?[A-Za-z0-9_-]{20,}'
HITS=0
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "问题：工具包文件夹不是 Git 仓库，无法判断哪些文件会进仓库。请先让 AI 执行 git init 并配置 .gitignore。"
  HITS=$((HITS+1))
fi
# 扫描范围：已跟踪文件 + 未跟踪但没被 .gitignore 忽略的文件（即下次 git add 会进仓库的）
lsfiles() { git ls-files -z --cached --others --exclude-standard 2>/dev/null; }
# 2a. .env 类文件不得被跟踪
ENV_TRACKED="$(git ls-files | grep -E '(^|/)\.env(\.[A-Za-z0-9_-]+)?$' | grep -v '\.env\.example$' || true)"
if [ -n "$ENV_TRACKED" ]; then log "问题：以下 .env 文件被 git 跟踪："; log "$ENV_TRACKED"; HITS=$((HITS+1)); else log "通过：没有 .env 文件被 git 跟踪。"; fi
# 2b. 跟踪文件中的密钥样式
M="$(lsfiles | xargs -0 grep -InE --exclude=verify.sh "$PAT" 2>/dev/null | sed -E 's/([A-Za-z0-9_-]{6})[A-Za-z0-9_-]{8,}/\1******/g' || true)"
if [ -n "$M" ]; then log "问题：会进仓库的文件中发现疑似密钥（已打码）："; log "$M"; HITS=$((HITS+1)); else log "通过：会进仓库的文件中未发现密钥样式字符串。"; fi
# 2c. 构建产物中的密钥样式
for d in "$P/frontend/.next" "$P/frontend/dist" "$P/frontend/out" "$P/backend/dist"; do
  [ -d "$d" ] || continue
  M="$(grep -rIlE "$PAT" "$d" 2>/dev/null || true)"
  if [ -n "$M" ]; then log "问题：构建产物 $d 中发现疑似密钥，文件："; log "$M"; HITS=$((HITS+1)); else log "通过：构建产物 $d 未发现密钥样式字符串。"; fi
done
# 2d. 本机 .env 里的真实 Key 值不得出现在任何跟踪文件或构建产物里（值本身不打印）
#     范围：产品文件夹下所有 .env* 文件（.env.example 除外，依赖目录除外）+ 根目录 .env。产品名不含空格（AGENTS.md 第 9 节），按空白切分即可。
ENVFILES="$(find "$P" -maxdepth 3 -name '.env*' ! -name '.env.example' -not -path '*/node_modules/*' -not -path '*/.venv/*' -not -path '*/.next/*' 2>/dev/null)"
for envf in $ENVFILES .env; do
  [ -f "$envf" ] || continue
  while IFS= read -r line; do
    case "$line" in \#*|"") continue;; esac
    name="${line%%=*}"
    # 只检查名字像密钥的变量（KEY / SECRET / TOKEN / PASSWORD / ACCESS），URL、模型名之类不算
    echo "$name" | grep -qiE 'key|secret|token|pass|access' || continue
    val="${line#*=}"; val="${val%\"}"; val="${val#\"}"
    [ "${#val}" -ge 12 ] || continue
    FOUND="$(lsfiles | xargs -0 grep -IlF -- "$val" 2>/dev/null || true)"
    for d in "$P/frontend/.next" "$P/frontend/dist" "$P/frontend/out" "$P/backend/dist"; do [ -d "$d" ] && FOUND="$FOUND $(grep -rIlF -- "$val" "$d" 2>/dev/null || true)"; done
    FOUND="$(echo "$FOUND" | xargs 2>/dev/null || true)"
    if [ -n "$FOUND" ]; then log "问题：$envf 中 ${line%%=*} 的值出现在：$FOUND"; HITS=$((HITS+1)); else log "通过：$envf 中 ${line%%=*} 的值未出现在任何跟踪文件或构建产物中。"; fi
  done < "$envf"
done
if [ "$HITS" -eq 0 ]; then R2="通过"; else R2="不通过（${HITS} 处问题，见输出）"; fi
log "结果：$R2"
cd "$P"

# ---------- 4. 重启恢复 ----------
sect "4. 数据重启恢复（verify.sh 自己起服务、写随机标记、杀进程、再起、读回；钩子 scripts/restart-check.sh 只提供 start/write/read/stop）"
if [ "$NOCODE" -eq 1 ]; then
  R3="N/A（无代码阶段）"
elif [ -f scripts/restart-check.sh ]; then
  export RESTART_CHECK_DIR="$(mktemp -d)"
  NONCE="rc-$(date +%s)-${RANDOM}${RANDOM}"
  FAIL=""; PID1=""; PID2=""; RID=""
  hook() { bash scripts/restart-check.sh "$@" 2>>"$LOG" | tail -1 | tr -d '[:space:]'; }   # 取标准输出最后一行
  alive() { local st; st="$(ps -o stat= -p "$1" 2>/dev/null | tr -d ' ')"; [ -n "$st" ] && [ "${st:0:1}" != "Z" ]; }   # 僵尸不算活着
  PID1="$(hook start)"
  if ! [[ "$PID1" =~ ^[0-9]+$ ]] || ! alive "$PID1"; then FAIL="第 1 步 start 没有返回一个正在运行的进程号（得到：${PID1:-空}）"; fi
  if [ -z "$FAIL" ]; then
    RID="$(hook write "$NONCE")"
    [ -n "$RID" ] || FAIL="第 2 步 write 没有返回记录 ID"
  fi
  if [ -z "$FAIL" ]; then
    kill -9 "$PID1" 2>/dev/null; sleep 1
    alive "$PID1" && FAIL="第 3 步 进程 ${PID1} 杀不掉"
  fi
  if [ -z "$FAIL" ]; then
    PID2="$(hook start)"
    if ! [[ "$PID2" =~ ^[0-9]+$ ]] || ! alive "$PID2"; then FAIL="第 4 步 重启后 start 没有返回一个正在运行的进程号（得到：${PID2:-空}）"
    elif [ "$PID2" = "$PID1" ]; then FAIL="第 4 步 重启后进程号没变（${PID1}），没有真的重启"; fi
  fi
  if [ -z "$FAIL" ]; then
    GOT="$(hook read "$RID")"
    [ "$GOT" = "$NONCE" ] || FAIL="第 5 步 重启后读回的标记和 verify.sh 生成的随机标记不一致（得到：${GOT:-空}）"
  fi
  [ -n "$PID2" ] && kill "$PID2" 2>/dev/null
  bash scripts/restart-check.sh stop >/dev/null 2>&1 || true
  rm -rf "$RESTART_CHECK_DIR"; unset RESTART_CHECK_DIR
  if [ -z "$FAIL" ]; then
    log "通过：起服务（PID ${PID1}）→ 写入随机标记 → 杀掉 → 再起（PID ${PID2}）→ 按 ID ${RID} 读回，标记一致。"
    R3="通过（进程 ${PID1}→${PID2}，随机标记读回一致）"
  else
    log "不通过：${FAIL}。钩子契约见通用手册第十节。"; R3="不通过（${FAIL}）"
  fi
else
  log "未找到 scripts/restart-check.sh。有可重启的服务时，AI 必须按通用手册第十节的契约写这个钩子（start / write / read / stop）。"
fi
log "结果：$R3"

# ---------- 5. 前端类型检查 / Lint / 生产构建 ----------
sect "5. 前端类型检查 / Lint / 生产构建"
if [ -f frontend/package.json ]; then
  FAIL=""
  T="$(mktemp)"
  if grep -q '"lint"' frontend/package.json; then (cd frontend && npm run lint 2>&1) >> "$T" || FAIL="$FAIL lint"; fi
  if [ -f frontend/tsconfig.json ]; then (cd frontend && npx tsc --noEmit 2>&1) >> "$T" || FAIL="$FAIL 类型检查"; fi
  if grep -q '"build"' frontend/package.json; then (cd frontend && npm run build 2>&1) >> "$T" || FAIL="$FAIL 生产构建"; fi
  cat "$T" >> "$LOG"; rm -f "$T"
  if [ -z "$FAIL" ]; then R4="通过"; else R4="不通过（失败项：${FAIL}）"; fi
else
  log "未找到 frontend/package.json，本阶段无正式前端。"
fi
log "结果：$R4"

# ---------- 6. 文件齐全与位置 ----------
sect "6. 文件齐全与位置（AGENTS.md 第 7 节的产出清单；根目录不放产品文件；前面阶段有没有被跳过）"
MISS=0; STRAY=0
cd "$ROOT"
# 5a. 根目录只放工具包自己的文件，产品的代码、依赖、数据都应在 projects/<产品名>/ 里
for e in * .[!.]*; do
  [ -e "$e" ] || continue
  case "$e" in
    AGENTS.md|CLAUDE.md|README.md|开始看这里.md|LICENSE|verify.sh|kit.sh|开工.command|.gitignore|assets|docs|projects|.git|.DS_Store) ;;
    scripts) for f in scripts/*; do [ "$f" = scripts/export-clean-kit.sh ] || { log "放错位置：${f}（应在产品文件夹里）"; STRAY=$((STRAY+1)); }; done ;;
    .venv|.env|.env.*|.next|.vefaas|.pytest_cache) log "放错位置：${e}（应在产品文件夹里）"; STRAY=$((STRAY+1)) ;;
    .*) ;;  # 其他隐藏文件夹多为编码工具自己的配置
    *) log "放错位置：${e}（应在产品文件夹里）"; STRAY=$((STRAY+1)) ;;
  esac
done
for e in docs/*; do
  case "$e" in docs/手册|docs/PRD模板.md) ;; *) log "放错位置：${e}（产品文档应在 ${P}/docs/ 里）"; STRAY=$((STRAY+1)) ;; esac
done
[ "$STRAY" -eq 0 ] && log "通过：根目录没有放错位置的文件。"
cd "$P"
# 5b. 本阶段必须产出的文件
NEED=(); EVI=0
case "$STAGE" in
  "") ;;
  阶段0) NEED=(docs/阶段文档/阶段0-PRD补全清单.md "docs/evidence/$STAGE/是否表.md") ;;
  阶段1) NEED=(docs/阶段文档/技术适配声明.md "docs/evidence/$STAGE/是否表.md") ;;
  阶段2*) NEED=("docs/阶段文档/${STAGE}技术开发文档.md"); EVI=1 ;;
  阶段3-0) NEED=(docs/阶段文档/前端适配声明.md "docs/evidence/$STAGE/是否表.md") ;;
  阶段3*) NEED=(docs/阶段文档/前端适配声明.md "docs/阶段文档/${STAGE}技术开发文档.md"); EVI=1 ;;
  阶段4) NEED=(docs/阶段文档/上线方案摘要.md); EVI=1 ;;
  阶段5) ;;
  *) log "问题：不认识的阶段名 ${STAGE}（应为 阶段0、阶段1、阶段2-1、阶段3-0、阶段3-1、阶段4、阶段5 这类）。"; MISS=$((MISS+1)) ;;
esac
if [ -n "$STAGE" ]; then
  NEED+=(docs/项目状态.md)
  [ "$EVI" -eq 1 ] && NEED+=(README.md "docs/evidence/$STAGE/照着做清单.md" "docs/evidence/$STAGE/是否表.md")
  for f in ${NEED[@]+"${NEED[@]}"}; do
    if [ -s "$f" ]; then log "有：$f"; else log "缺：$f"; MISS=$((MISS+1)); fi
  done
  if [ "$EVI" -eq 1 ]; then
    SHOTS="$(ls "docs/evidence/$STAGE" 2>/dev/null | grep -icE '\.(png|jpe?g)$')"
    if [ "$SHOTS" -gt 0 ]; then log "有：docs/evidence/$STAGE/ 下 $SHOTS 张截图"; else log "缺：docs/evidence/$STAGE/ 下至少一张截图（.png / .jpg）"; MISS=$((MISS+1)); fi
  fi
fi
# 5c. 前面阶段的产物：AI 跳过了前面的阶段，在这里报出来（阶段 4 可选，阶段 5 不要求它）
stage_files() {  # 参数：阶段名 → 该阶段必须有的文件，每行一个
  case "$1" in
    阶段0) echo docs/阶段文档/阶段0-PRD补全清单.md; echo docs/evidence/阶段0/是否表.md ;;
    阶段1) echo docs/阶段文档/技术适配声明.md; echo docs/evidence/阶段1/是否表.md ;;
    阶段3-0) echo docs/阶段文档/前端适配声明.md; echo docs/evidence/阶段3-0/是否表.md ;;
    阶段2-*|阶段3-*) echo "docs/阶段文档/$1技术开发文档.md"; echo "docs/evidence/$1/是否表.md" ;;
    阶段4) echo docs/阶段文档/上线方案摘要.md; echo docs/evidence/阶段4/是否表.md ;;
  esac
}
PRIOR=(); N=""
case "$STAGE" in
  阶段1) PRIOR=(阶段0) ;;
  阶段2-*) N="${STAGE#阶段2-}"; PRIOR=(阶段0 阶段1); i=1; while [[ "$N" =~ ^[0-9]+$ ]] && [ "$i" -lt "$N" ]; do PRIOR+=("阶段2-$i"); i=$((i+1)); done ;;
  阶段3-0) PRIOR=(阶段0 阶段1 阶段2-1) ;;
  阶段3-*) N="${STAGE#阶段3-}"; PRIOR=(阶段0 阶段1 阶段2-1 阶段3-0); i=1; while [[ "$N" =~ ^[0-9]+$ ]] && [ "$i" -lt "$N" ]; do PRIOR+=("阶段3-$i"); i=$((i+1)); done ;;
  阶段4|阶段5) PRIOR=(阶段0 阶段1 阶段2-1 阶段3-0 阶段3-1) ;;
esac
PMISS=0
for st in ${PRIOR[@]+"${PRIOR[@]}"}; do
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if [ ! -s "$f" ]; then log "缺：${f}（前面的阶段 ${st} 没做完就到了 ${STAGE}）"; PMISS=$((PMISS+1)); fi
  done < <(stage_files "$st")
done
[ "${#PRIOR[@]}" -gt 0 ] && [ "$PMISS" -eq 0 ] && log "通过：前面阶段（${PRIOR[*]}）的产物都在。"
MISS=$((MISS+PMISS))
if [ "$MISS" -gt 0 ] || [ "$STRAY" -gt 0 ]; then
  R5="不通过（缺 ${MISS} 个文件，${STRAY} 处放错位置）"
  [ "$PMISS" -gt 0 ] && R5="不通过（缺 ${MISS} 个文件，其中 ${PMISS} 个属于前面没做完的阶段；${STRAY} 处放错位置）"
elif [ -z "$STAGE" ]; then R5="通过（只查了文件位置；给阶段名才查文件齐全）"
else R5="通过"; fi
log "结果：$R5"

# Product-owned business criteria are additional gates, never replacements for the six checks.
R7="N/A"
if [ -f scripts/stage-gate.sh ]; then
  sect "阶段专项闸门"
  T="$(mktemp)"
  bash scripts/stage-gate.sh "$STAGE" > "$T" 2>&1; RC=$?
  cat "$T" | tee -a "$LOG"
  if [ "$RC" -eq 0 ]; then R7="通过或不适用"; else R7="待验（阶段专项闸门未通过）"; fi
  rm -f "$T"
fi

# ---------- 汇总 ----------
sect "汇总（抄进是否表的就是这六行）"
log "是否表第 2 项  mock 测试：      $R1"
log "是否表第 3 项  真实模型冒烟：   $R6"
log "是否表第 4 项  前端检查与构建： $R4"
log "是否表第 5 项  密钥排除：       $R2"
log "是否表第 6 项  重启后数据恢复： $R3"
log "是否表第 10 项 文件齐全与位置： $R5"
EXIT=0
for r in "$R1" "$R2" "$R3" "$R4" "$R5" "$R6" "$R7"; do case "$r" in 不通过*|未配置*|待验*) EXIT=1;; esac; done
log "阶段专项闸门：$R7"
if [ "$EXIT" -eq 0 ]; then log "总判定：六项全部通过或不适用。"; else log "总判定：有不通过、未配置或待验项，对应是否表只能填\"否\"（第 3 项填\"待验\"）。"; fi

if [ -n "$STAGE" ]; then
  mkdir -p "docs/evidence/$STAGE"
  cp "$LOG" "docs/evidence/$STAGE/verify-输出.txt"
  log "完整输出已写入：$P/docs/evidence/$STAGE/verify-输出.txt"
fi
rm -f "$LOG"
exit "$EXIT"
