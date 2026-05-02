#!/bin/bash
set -e
SCRIPT="scripts/clip-pipeline.sh"
TMP=$(pwd)/_heredoc_tmp
mkdir -p "$TMP"

# Stage 4 heredoc body lines (between python3 << PYEOF and PYEOF):
sed -n '781,2726p' "$SCRIPT" > "$TMP/stage4_body.py.tmpl"

# Wrap into a tiny script that just emits the heredoc content via cat,
# letting bash interpret it the same way the real pipeline would.
{
    echo 'LLM_URL=dummy_url'
    echo 'TEXT_MODEL=dummy_model'
    echo 'TEXT_MODEL_PASSB=dummy_model'
    echo 'CLIP_STYLE=auto'
    echo 'cat << PYEOF'
    cat "$TMP/stage4_body.py.tmpl"
    echo 'PYEOF'
} > "$TMP/stage4_wrap.sh"

bash "$TMP/stage4_wrap.sh" > "$TMP/stage4_expanded.py" 2> "$TMP/stage4_bash.err"
if [ -s "$TMP/stage4_bash.err" ]; then
    echo "BASH ERRORS during Stage 4 heredoc expansion:"
    cat "$TMP/stage4_bash.err"
    exit 1
fi
python3 -c "import ast,sys; ast.parse(open('$TMP/stage4_expanded.py').read()); print('OK: Stage 4 heredoc parses post bash')"

# Same for Stage 3 (557..761) and Stage 6 (2997..3675)
for range in "557 761 stage3" "2997 3675 stage6"; do
    set -- $range
    a=$1; b=$2; tag=$3
    sed -n "${a},${b}p" "$SCRIPT" > "$TMP/${tag}_body.py.tmpl"
    {
        echo 'LLM_URL=dummy_url'
        echo 'TEXT_MODEL=dummy_model'
        echo 'TEXT_MODEL_PASSB=dummy_model'
        echo 'CLIP_STYLE=auto'
        echo 'STREAM_TYPE_HINT='
        echo 'cat << PYEOF'
        cat "$TMP/${tag}_body.py.tmpl"
        echo 'PYEOF'
    } > "$TMP/${tag}_wrap.sh"
    bash "$TMP/${tag}_wrap.sh" > "$TMP/${tag}_expanded.py" 2> "$TMP/${tag}_bash.err"
    if [ -s "$TMP/${tag}_bash.err" ]; then
        echo "BASH ERRORS during ${tag} heredoc expansion:"
        cat "$TMP/${tag}_bash.err"
        exit 1
    fi
    python3 -c "import ast,sys; ast.parse(open('$TMP/${tag}_expanded.py').read()); print('OK: ${tag} heredoc parses post bash')"
done

rm -rf "$TMP"
