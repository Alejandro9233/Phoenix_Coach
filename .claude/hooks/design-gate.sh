#!/usr/bin/env bash
# PreToolUse gate for SwiftUI styling literals.
# Blocks the hard bans from .claude/skills/swiftui-design/SKILL.md; warns on the rest.
# DesignSystem.swift is exempt: it is where tokens are defined.

input=$(cat)
path=$(jq -r '.tool_input.file_path // empty' <<<"$input")
case "$path" in
  *ios/*.swift) ;;
  *) exit 0 ;;
esac
case "$path" in *DesignSystem.swift) exit 0 ;; esac

content=$(jq -r '.tool_input.content // .tool_input.new_string // empty' <<<"$input")
[ -z "$content" ] && exit 0

block=()
grep -qE 'Color\(hex:' <<<"$content" && block+=("Color(hex:) — use a DS.Colors token")
grep -qE '"#[0-9A-Fa-f]{3,8}"' <<<"$content" && block+=("hex color string — use a DS.Colors token")
grep -qE '\.spring\(response:' <<<"$content" && block+=("literal .spring(response:) — use DS.Animation.quick/normal/slow")
grep -qE '\.foregroundColor\(' <<<"$content" && block+=(".foregroundColor is deprecated — use .foregroundStyle")

warn=()
sizes=$(grep -oE '\.font\(\.system\(size: *[0-9]+' <<<"$content" | grep -oE '[0-9]+$' | sort -u || true)
for s in $sizes; do
  case " 48 36 24 22 15 13 11 10 " in
    *" $s "*) ;;
    *) warn+=("font size $s is off the scale 48/36/24/22/15/13/11/10") ;;
  esac
done
grep -qE '\.cornerRadius\(' <<<"$content" && warn+=(".cornerRadius() is deprecated — .clipShape(.rect(cornerRadius: DS.Radius.x))")
grep -qE '\.tracking\( *[0-9.]+ *\)' <<<"$content" && warn+=("literal .tracking() — use DS.Tracking")
grep -qE '\.(easeIn|easeOut|easeInOut|linear)\(duration:' <<<"$content" && warn+=("ad-hoc animation duration — use DS.Animation")
grep -qE 'UIScreen\.main\.bounds' <<<"$content" && warn+=("UIScreen.main.bounds — use .containerRelativeFrame(.horizontal)")

file=$(basename "$path")
if ((${#block[@]})); then
  reason="design-gate blocked $file: $(printf '%s; ' "${block[@]}")Add the token to DesignSystem.swift first, then use it."
  jq -n --arg r "$reason" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
fi
if ((${#warn[@]})); then
  ctx="design-gate warning for $file: $(printf '%s; ' "${warn[@]}")Fix it now, or list it as drift in the pre-commit summary to Alex."
  jq -n --arg c "$ctx" '{systemMessage:$c,hookSpecificOutput:{hookEventName:"PreToolUse",additionalContext:$c}}'
fi
exit 0
