#!/bin/bash
# Check if commit message contains Claude attribution
# Used by pre-commit hook to enforce commit message policy

# Get the commit message from the file passed by pre-commit
commit_msg_file="${1:-.git/COMMIT_EDITMSG}"

if [ -f "$commit_msg_file" ]; then
    commit_msg=$(cat "$commit_msg_file")
else
    # Fallback to last commit if file doesn't exist (for testing)
    commit_msg=$(git log -1 --format=%B 2>/dev/null || echo "")
fi

# Check for Claude attribution
if echo "$commit_msg" | grep -qi "Co-Authored-By: Claude"; then
    echo ""
    echo "ERROR: Commit message contains Claude attribution!"
    echo ""
    echo "The commit message includes 'Co-Authored-By: Claude' which violates project policy."
    echo "See CLAUDE.md section 'CRITICAL: COMMIT AND PUSH RULES' for details."
    echo ""
    echo "Please remove the Co-Authored-By line and try again."
    echo ""
    exit 1
fi

# Check for Claude branding in commit message
if echo "$commit_msg" | grep -v "CLAUDE.md" | grep -v "\.claude" | grep -qi "generated with.*claude\|claude code"; then
    echo ""
    echo "ERROR: Commit message contains Claude branding!"
    echo ""
    echo "The commit message includes Claude Code branding which violates project policy."
    echo "See CLAUDE.md section 'CRITICAL: COMMIT AND PUSH RULES' for details."
    echo ""
    echo "Please remove the Claude branding and try again."
    echo ""
    exit 1
fi

exit 0
