# System Design: Computer Control (files, apps, lanes)

How Halo acts on the machine, and the three-lane model for *how* it acts.

## Capabilities
- **Files:** inspect, create, edit, move, organize — via native filesystem calls (Tier 1–2 per [permissions](04-permissions.md)).
- **Commands:** run installed CLIs or generated Python/PowerShell as bounded, durable Lane-1 tasks. Simple file/folder work still uses typed file tools.
- **Apps:** open/focus apps; drive them when needed.
- **GUI:** click/type into desktop apps that have no API.

## The three lanes (chosen per task by the Tool Executor)
| Lane | Mechanism | Cursor | You | When |
|---|---|---|---|---|
| **1 Fast** | filesystem calls, CLIs, APIs, MCP | none | free to work | default — whenever a programmatic path exists |
| **2 Takeover** | GUI on your real desktop (UI Automation → vision fallback) | your real cursor | wait/watch | GUI-only task needing your real logins |
| **3 Sandbox** | GUI in a VM with its own cursor | separate | free to work | GUI task you want isolated/observable; one-time login in the VM |

### Lane selection
```
task → has fast path? → Lane 1
     → else GUI-only → user pinned a lane? → use it
                     → else default Lane 2, announce it
```
Halo **always states the lane it used**; user can pin per task.

## Managed command execution (implemented Phase-3a foundation)

`command_run` accepts an executable plus a structured argv list; `script_run`
accepts generated source only when real program logic is the economical route.
Neither accepts an unparsed shell command. Both normalize the executable, cwd,
arguments, environment references, timeout, and expected artifacts before the
permission gate classifies them.

- Known read-only profiles may run at Tier 1; explicit project-local runners
  may run at Tier 2 when the current user message names both operation and
  target. Unknown commands, generated scripts, network use, installs,
  overwrites, and paths outside registered roots are Tier 3.
- `cmd /c`, encoded PowerShell, detached/background execution, and broad
  disk/boot/elevation tools are refused by the generic runner.
- Processes run with `shell=False`, closed stdin, a minimal child environment,
  independent 256 KiB live caps, bounded head/tail results, binary suppression,
  and exact secret-value redaction. Generated-script scratch is capped at 64 MiB.
- Windows children start suspended, enter a kill-on-close Job Object, and only
  then resume. Stop and timeout therefore cover the complete descendant tree,
  including grandchildren, without a pre-assignment escape window.
- Requested outputs must be declared. A zero exit code with a missing, invalid,
  or unchanged overwrite target is a failed task. PDFs receive structural and
  parser verification in a disposable helper process; verification shares the
  operation deadline and rejects files above 256 MiB. Useful artifacts from
  non-zero exits remain visible as partial results.
- Approval is bound to a digest of the resolved executable identity and exact
  normalized request. Only the PATH-discovered executable may inherit a known
  low-tier profile. Fingerprint and tier are frozen at task admission and both
  are rechecked after any queue wait and immediately before spawn.
  Raw generated source remains only in the resumable local graph checkpoint;
  approvals, action rows, task args, logs, and continuations use hashes and
  redacted metadata.

This is process containment and permission gating, not a host sandbox: an
approved project script still has the operating-system access of the Halo user.
Unknown tools, custom environments, installs, networking, and generated source
remain Tier 3 so that limitation is visible at the authority boundary.

## GUI mechanism (Lanes 2/3)
- **Primary: Windows UI Automation** — drive by real UI elements (reliable).
- **Fallback: vision + coordinates** — screenshot → model picks target → click/type, for apps with no accessibility tree.

## Live view
- Lane 1: observed via the activity feed (no visuals).
- Lanes 2/3: UI shows a **live stream** of the desktop being driven; the lane indicator is always visible.

## Known tradeoff (from PRD §14.4)
Lane 3 gives concurrency but needs its own logins; Lane 2 has your logins but takes your screen. Same GUI action can't have both — chosen per task.

## Lane-3 VM choice (proposed — Windows 11 Home constraint)
This machine is **Windows 11 Home**, where **Hyper-V and Windows Sandbox are unavailable** (Pro/Enterprise only). WSL2 exists but is Linux — can't run the user's Windows apps. So:
- **If upgraded to Win 11 Pro (~$99 one-time): Windows Sandbox** — lightweight, disposable, auto-discards state, minimal setup. Re-login each session is inherent to isolation.
- **Staying on Home: VirtualBox** (free/OSS) with one persistent Windows guest — heavier (~4 GB RAM live, ~40 GB disk) but persistent logins remove the re-login pain.
- **MVP recommendation:** don't build Lane 3 at all initially. Ship Lanes 1–2; gate Lane 3 behind whichever VM is chosen when that phase is reached.

## Failure handling
- UI Automation element not found → retry once → fall back to vision → if still stuck, stop and ask (don't blind-click).
- Any destructive/irreversible GUI step still routes through the Tier-3 gate.
