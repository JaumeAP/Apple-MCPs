---
name: response-style
description: Personal reply-style rules that apply in every session, regardless of repo -- tool-call economy and TTS-safe formatting. Use whenever deciding how to batch tool calls or how to format a reply that might be read aloud.
---

# Response style

These rules used to be copy-pasted by hand into every repo's own
`CLAUDE.md` under "Response style", each marked "global -- not
repo-specific". Since 2026-09-26 they live once, in the session-rules
plugin, which copies this file into each repository's `.claude/skills/`
at session start -- so it also loads in ephemeral environments (a cloud
container, the iOS app) where the plugin itself is not installed.

## Fewer tool calls

Each call renders its own label line in the terminal and no Claude
Code setting collapses them, so the only lever on that noise is making
fewer calls. Batch independent calls into one message instead of one
per turn; chain sequential shell commands into a single call with `;`
or `&&`; skip any call whose result would not change the next
step -- re-reading a file just edited, re-confirming a command that
already reported success. Never drop a call that verification
genuinely needs: the accuracy rules here outrank this one.

## Reply language and formatting

No ban on bold, em dashes, ellipses, headers, or tables -- use them
when they genuinely help, UNLESS the output may be read aloud by TTS,
in which case drop all decorative symbols (bold, headings, tables,
ellipses, em dashes).
